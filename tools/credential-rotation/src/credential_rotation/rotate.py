"""Credential rotation orchestration."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import pymysql

from .app_refresh import force_new_ecs_deployment
from .config_discovery import discover_runtime_paths
from .efs_editor import atomic_write, parse_sqlconf, read_text, render_sqlconf
from .secrets_manager import SecretsManagerSlots, generate_password
from .validators import validate_openemr_health, validate_rds_connection


@dataclass
class RotationContext:
    region: str
    rds_slots_secret_id: str
    rds_admin_secret_id: str
    sites_mount_root: str
    ecs_cluster_name: str
    ecs_service_name: str
    openemr_health_url: str | None
    dry_run: bool


class RotationOrchestrator:
    def __init__(self, context: RotationContext):
        self.ctx = context
        self.secrets = SecretsManagerSlots(region=context.region)

    def rotate(self) -> None:
        runtime = discover_runtime_paths(self.ctx.sites_mount_root)
        sqlconf_path = runtime.sqlconf_path

        rds_state = self.secrets.get_secret(self.ctx.rds_slots_secret_id)

        active = rds_state.active_slot
        standby = self.secrets.standby_slot(active)

        if not self.ctx.dry_run:
            self._load_admin_secret()

        current_sqlconf = parse_sqlconf(read_text(sqlconf_path))
        active_slot = rds_state.slot(active)
        standby_slot = rds_state.slot(standby)

        self._ensure_slot_initialized(standby_slot, fallback_host=active_slot["host"], fallback_db=active_slot["dbname"])
        self._ensure_slot_initialized(active_slot, fallback_host=standby_slot["host"], fallback_db=standby_slot["dbname"])

        if not self.ctx.dry_run:
            for slot_name in ("A", "B"):
                slot = dict(rds_state.slot(slot_name))
                self._upsert_openemr_db_user(slot)

        config_matches_active = self._slot_matches_sqlconf(active_slot, current_sqlconf)
        config_matches_standby = self._slot_matches_sqlconf(standby_slot, current_sqlconf)

        if not config_matches_active and not config_matches_standby:
            self._bootstrap_slots_from_sqlconf(
                current_sqlconf=current_sqlconf,
                rds_state=rds_state,
                active=active,
                standby=standby,
            )
            rds_state = self.secrets.get_secret(self.ctx.rds_slots_secret_id)
            active_slot = rds_state.slot(active)
            standby_slot = rds_state.slot(standby)
            config_matches_active = True
            config_matches_standby = False

        elif config_matches_standby and not config_matches_active:
            self._reconcile_active_slot_to_standby(rds_state, active, standby)
            if not self.ctx.dry_run:
                self._rotate_admin_password()
            return

        if config_matches_active and active == "B":
            self._rotate_old_slot(new_active=active, old_slot=standby)
            if not self.ctx.dry_run:
                self._rotate_admin_password()
            return

        self._upsert_openemr_db_user(standby_slot)
        validate_rds_connection(standby_slot)

        original_sqlconf_content = read_text(sqlconf_path)
        updated_sqlconf_content = render_sqlconf(original_sqlconf_content, standby_slot)

        if self.ctx.dry_run:
            return

        rollback_needed = True
        try:
            atomic_write(sqlconf_path, updated_sqlconf_content)

            force_new_ecs_deployment(
                region=self.ctx.region,
                cluster_name=self.ctx.ecs_cluster_name,
                service_name=self.ctx.ecs_service_name,
            )

            self._validate_runtime(standby_slot)

            rds_payload = dict(rds_state.payload)
            rds_payload["active_slot"] = standby
            self.secrets.put_payload(self.ctx.rds_slots_secret_id, rds_payload)

            rollback_needed = False
        except Exception:
            if rollback_needed:
                self._rollback(sqlconf_path, original_sqlconf_content, active_slot)
            raise

        self._rotate_old_slot(new_active=standby, old_slot=active)

        self._rotate_admin_password()

    def _rotate_old_slot(self, new_active: str, old_slot: str) -> None:
        rds_state = self.secrets.get_secret(self.ctx.rds_slots_secret_id)

        new_active_slot = rds_state.slot(new_active)
        old_rds = dict(rds_state.slot(old_slot))

        old_rds["password"] = generate_password()

        if not self.ctx.dry_run:
            self._upsert_openemr_db_user(old_rds)
            validate_rds_connection(old_rds)

        if self.ctx.dry_run:
            return

        rds_payload = dict(rds_state.payload)
        rds_payload[old_slot] = old_rds
        rds_payload[new_active] = new_active_slot
        rds_payload["active_slot"] = new_active
        self.secrets.put_payload(self.ctx.rds_slots_secret_id, rds_payload)

    def _rollback(
        self,
        sqlconf_path: Path,
        original_sqlconf_content: str,
        active_slot: Dict[str, Any],
    ) -> None:
        atomic_write(sqlconf_path, original_sqlconf_content)
        force_new_ecs_deployment(
            region=self.ctx.region,
            cluster_name=self.ctx.ecs_cluster_name,
            service_name=self.ctx.ecs_service_name,
        )
        self._validate_runtime(active_slot)

    def _validate_runtime(self, rds_slot: Dict[str, Any]) -> None:
        validate_rds_connection(rds_slot)
        validate_openemr_health(self.ctx.openemr_health_url)

    def sync_db_users(self) -> None:
        """Sync RDS users (openemr_a, openemr_b) to match slot secret passwords."""
        rds_state = self.secrets.get_secret(self.ctx.rds_slots_secret_id)
        for slot_name in ("A", "B"):
            slot = dict(rds_state.slot(slot_name))
            self._ensure_slot_initialized(
                slot,
                fallback_host=rds_state.slot("A" if slot_name == "B" else "B")["host"],
                fallback_db=rds_state.slot("A")["dbname"],
            )
            self._upsert_openemr_db_user(slot)
            validate_rds_connection(slot)

    def _bootstrap_slots_from_sqlconf(
        self,
        current_sqlconf: Dict[str, str],
        rds_state: Any,
        active: str,
        standby: str,
    ) -> None:
        """When sqlconf doesn't match either slot, create proper app users.

        Both slots get dedicated application users (openemr_a / openemr_b) with
        fresh passwords. This avoids leaking the admin user into slot rotation,
        which would cause the admin password to drift from the admin secret.
        """
        host = current_sqlconf.get("host", "")
        port = str(current_sqlconf.get("port", "3306"))
        dbname = current_sqlconf.get("dbname", "openemr")

        slot_a = {
            "username": "openemr_a",
            "password": generate_password(),
            "host": host,
            "port": port,
            "dbname": dbname,
        }
        slot_b = {
            "username": "openemr_b",
            "password": generate_password(),
            "host": host,
            "port": port,
            "dbname": dbname,
        }
        if not self.ctx.dry_run:
            self._upsert_openemr_db_user(slot_a)
            validate_rds_connection(slot_a)
            self._upsert_openemr_db_user(slot_b)
            validate_rds_connection(slot_b)
        rds_payload = dict(rds_state.payload)
        rds_payload[active] = slot_a
        rds_payload[standby] = slot_b
        rds_payload["active_slot"] = active
        if not self.ctx.dry_run:
            self.secrets.put_payload(self.ctx.rds_slots_secret_id, rds_payload)

    def _reconcile_active_slot_to_standby(
        self,
        rds_state: Any,
        active: str,
        standby: str,
    ) -> None:
        """App points at standby; secrets say active. Flip active_slot to match, then rotate old slot."""
        rds_payload = dict(rds_state.payload)
        rds_payload["active_slot"] = standby
        if not self.ctx.dry_run:
            self.secrets.put_payload(self.ctx.rds_slots_secret_id, rds_payload)
        self._rotate_old_slot(new_active=standby, old_slot=active)

    def _slot_matches_sqlconf(self, slot: Dict[str, Any], sqlconf: Dict[str, str]) -> bool:
        return (
            sqlconf.get("host") == str(slot.get("host"))
            and str(sqlconf.get("port", "3306")) == str(slot.get("port", "3306"))
            and sqlconf.get("username") == str(slot.get("username"))
            and sqlconf.get("password") == str(slot.get("password"))
            and sqlconf.get("dbname") == str(slot.get("dbname"))
        )

    def _ensure_slot_initialized(self, slot: Dict[str, Any], fallback_host: str, fallback_db: str) -> None:
        slot.setdefault("username", "openemr_slot")
        slot.setdefault("password", generate_password())
        slot.setdefault("host", fallback_host)
        slot.setdefault("port", "3306")
        slot.setdefault("dbname", fallback_db)

    def _rotate_admin_password(self) -> None:
        """Rotate the RDS master (dbadmin) password.

        Runs at the end of every non-dry-run rotation to keep the privileged
        credential fresh. The ordering (ALTER USER -> validate -> update secret)
        minimises the drift window: if the secret update fails, the next run
        detects the mismatch and retries with both passwords.
        """
        admin = self._load_admin_secret()
        host = admin.get("host", "")
        port = int(admin.get("port", 3306))
        username = admin["username"]
        old_password = admin["password"]

        new_password = generate_password()

        conn = pymysql.connect(
            host=host,
            port=port,
            user=username,
            password=old_password,
            connect_timeout=5,
            ssl={"ssl": {}},
            autocommit=True,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "ALTER USER %s@'%%' IDENTIFIED BY %s",
                    (username, new_password),
                )
        finally:
            conn.close()

        pymysql.connect(
            host=host, port=port, user=username, password=new_password,
            connect_timeout=5, ssl={"ssl": {}},
        ).close()

        admin["password"] = new_password
        self.secrets.put_payload(self.ctx.rds_admin_secret_id, admin)

    def _load_admin_secret(self) -> Dict[str, Any]:
        """Load admin credentials, handling drift from a prior rotation where
        the DB password was changed but the secret update failed."""
        admin_state = self.secrets.get_secret(self.ctx.rds_admin_secret_id)
        admin = admin_state.payload
        host = admin.get("host", "")
        port = int(admin.get("port", 3306))

        try:
            pymysql.connect(
                host=host, port=port, user=admin["username"],
                password=admin["password"], connect_timeout=5,
                ssl={"ssl": {}},
            ).close()
        except pymysql.OperationalError:
            pass
        else:
            return admin

        rds_state = self.secrets.get_secret(self.ctx.rds_slots_secret_id)
        for slot_name in ("A", "B"):
            slot = rds_state.slot(slot_name)
            if slot.get("username") == admin["username"]:
                try:
                    pymysql.connect(
                        host=host, port=port, user=admin["username"],
                        password=slot["password"], connect_timeout=5,
                        ssl={"ssl": {}},
                    ).close()
                except pymysql.OperationalError:
                    continue
                admin["password"] = slot["password"]
                self.secrets.put_payload(self.ctx.rds_admin_secret_id, admin)
                return admin

        raise RuntimeError(
            "Admin credentials in DbSecretArn are invalid and no fallback "
            "password was found. Reset the RDS master password manually."
        )

    def _upsert_openemr_db_user(self, slot: Dict[str, Any]) -> None:
        admin = self._load_admin_secret()
        admin_host = admin.get("host") or slot["host"]
        admin_port = int(admin.get("port", 3306))
        admin_user = admin["username"]
        admin_password = admin["password"]
        db_name = slot["dbname"]

        if not db_name.replace("_", "").isalnum():
            raise ValueError(f"Unsupported dbname for rotation: {db_name}")

        conn = pymysql.connect(
            host=admin_host,
            port=admin_port,
            user=admin_user,
            password=admin_password,
            connect_timeout=5,
            ssl={"ssl": {}},
            autocommit=True,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s REQUIRE SSL",
                    (slot["username"], slot["password"]),
                )
                cur.execute(
                    "ALTER USER %s@'%%' IDENTIFIED BY %s REQUIRE SSL",
                    (slot["username"], slot["password"]),
                )
                cur.execute(f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO %s@'%%'", (slot["username"],))
                cur.execute("FLUSH PRIVILEGES")
        finally:
            conn.close()

    @staticmethod
    def from_env(dry_run: bool) -> "RotationOrchestrator":
        required = [
            "AWS_REGION",
            "RDS_SLOT_SECRET_ID",
            "RDS_ADMIN_SECRET_ID",
            "OPENEMR_SITES_MOUNT_ROOT",
            "OPENEMR_ECS_CLUSTER",
            "OPENEMR_ECS_SERVICE",
        ]
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

        context = RotationContext(
            region=os.environ["AWS_REGION"],
            rds_slots_secret_id=os.environ["RDS_SLOT_SECRET_ID"],
            rds_admin_secret_id=os.environ["RDS_ADMIN_SECRET_ID"],
            sites_mount_root=os.environ["OPENEMR_SITES_MOUNT_ROOT"],
            ecs_cluster_name=os.environ["OPENEMR_ECS_CLUSTER"],
            ecs_service_name=os.environ["OPENEMR_ECS_SERVICE"],
            openemr_health_url=os.getenv("OPENEMR_HEALTHCHECK_URL"),
            dry_run=dry_run,
        )
        return RotationOrchestrator(context)


def main_json_error(exc: Exception) -> str:
    return json.dumps({"status": "error", "error": str(exc)})
