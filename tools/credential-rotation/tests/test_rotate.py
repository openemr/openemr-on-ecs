"""Tests for rotate module: RotationOrchestrator logic, from_env, slot helpers, full rotation flow."""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pymysql
import pytest

from credential_rotation.rotate import RotationContext, RotationOrchestrator, main_json_error


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(tmp_path: Path, dry_run: bool = True) -> RotationContext:
    return RotationContext(
        region="us-east-1",
        rds_slots_secret_id="rds-slot",
        rds_admin_secret_id="rds-admin",
        sites_mount_root=str(tmp_path),
        ecs_cluster_name="cluster",
        ecs_service_name="service",
        openemr_health_url=None,
        dry_run=dry_run,
    )


def _write_sqlconf(tmp_path: Path, host="db-a", port="3306", username="openemr_a",
                   password="pass-a", dbname="openemr") -> Path:
    default_dir = tmp_path / "default"
    default_dir.mkdir(parents=True, exist_ok=True)
    sqlconf = default_dir / "sqlconf.php"
    sqlconf.write_text(
        f"<?php\n$host   = '{host}';\n$port   = '{port}';\n"
        f"$login  = '{username}';\n$pass   = '{password}';\n$dbase  = '{dbname}';\n",
        encoding="utf-8",
    )
    return sqlconf


class _FakeState:
    """Minimal stand-in for SlotSecretState."""

    def __init__(self, payload):
        self.payload = payload

    @property
    def active_slot(self):
        return self.payload["active_slot"]

    def slot(self, name):
        if name not in self.payload or not isinstance(self.payload[name], dict):
            raise ValueError(f"Slot {name} missing")
        return self.payload[name]


class _FakeSecrets:
    """In-memory replacement for SecretsManagerSlots."""

    def __init__(self, rds_payload, admin_payload=None):
        self._rds = dict(rds_payload)
        self._admin = admin_payload or {
            "username": "dbadmin", "password": "adminpw",
            "host": "db-a", "port": "3306",
        }

    def get_secret(self, secret_id):
        if secret_id == "rds-admin":
            return _FakeState(dict(self._admin))
        return _FakeState(dict(self._rds))

    def put_payload(self, secret_id, payload):
        if secret_id == "rds-admin":
            self._admin = dict(payload)
        else:
            self._rds = dict(payload)

    @staticmethod
    def standby_slot(active):
        return "B" if active == "A" else "A"


def _default_rds_payload(active="A"):
    return {
        "active_slot": active,
        "A": {"host": "db-a", "port": "3306", "username": "openemr_a", "password": "pass-a", "dbname": "openemr"},
        "B": {"host": "db-b", "port": "3306", "username": "openemr_b", "password": "pass-b", "dbname": "openemr"},
    }


# ---------------------------------------------------------------------------
# _slot_matches_sqlconf
# ---------------------------------------------------------------------------

class TestSlotMatchesSqlconf:
    def _orch(self, tmp_path):
        return RotationOrchestrator(_make_ctx(tmp_path))

    def test_match_returns_true(self, tmp_path):
        orch = self._orch(tmp_path)
        slot = {"host": "h", "port": "3306", "username": "u", "password": "p", "dbname": "d"}
        sqlconf = {"host": "h", "port": "3306", "username": "u", "password": "p", "dbname": "d"}
        assert orch._slot_matches_sqlconf(slot, sqlconf) is True

    def test_mismatch_password(self, tmp_path):
        orch = self._orch(tmp_path)
        slot = {"host": "h", "port": "3306", "username": "u", "password": "p", "dbname": "d"}
        sqlconf = {"host": "h", "port": "3306", "username": "u", "password": "WRONG", "dbname": "d"}
        assert orch._slot_matches_sqlconf(slot, sqlconf) is False

    def test_mismatch_host(self, tmp_path):
        orch = self._orch(tmp_path)
        slot = {"host": "h", "port": "3306", "username": "u", "password": "p", "dbname": "d"}
        sqlconf = {"host": "other", "port": "3306", "username": "u", "password": "p", "dbname": "d"}
        assert orch._slot_matches_sqlconf(slot, sqlconf) is False

    def test_port_defaults_match(self, tmp_path):
        orch = self._orch(tmp_path)
        slot = {"host": "h", "username": "u", "password": "p", "dbname": "d"}
        sqlconf = {"host": "h", "username": "u", "password": "p", "dbname": "d"}
        assert orch._slot_matches_sqlconf(slot, sqlconf) is True

    def test_port_string_int_coercion(self, tmp_path):
        orch = self._orch(tmp_path)
        slot = {"host": "h", "port": 3306, "username": "u", "password": "p", "dbname": "d"}
        sqlconf = {"host": "h", "port": "3306", "username": "u", "password": "p", "dbname": "d"}
        assert orch._slot_matches_sqlconf(slot, sqlconf) is True

    def test_empty_sqlconf_never_matches(self, tmp_path):
        orch = self._orch(tmp_path)
        slot = {"host": "h", "port": "3306", "username": "u", "password": "p", "dbname": "d"}
        assert orch._slot_matches_sqlconf(slot, {}) is False


# ---------------------------------------------------------------------------
# _ensure_slot_initialized
# ---------------------------------------------------------------------------

class TestEnsureSlotInitialized:
    def _orch(self, tmp_path):
        return RotationOrchestrator(_make_ctx(tmp_path))

    def test_fills_missing_fields(self, tmp_path):
        orch = self._orch(tmp_path)
        slot: dict = {}
        orch._ensure_slot_initialized(slot, fallback_host="fb-host", fallback_db="fb-db")
        assert slot["username"] == "openemr_slot"
        assert slot["host"] == "fb-host"
        assert slot["port"] == "3306"
        assert slot["dbname"] == "fb-db"
        assert len(slot["password"]) == 30  # generate_password default

    def test_preserves_existing_fields(self, tmp_path):
        orch = self._orch(tmp_path)
        slot = {"username": "u", "password": "p", "host": "h", "port": "3307", "dbname": "d"}
        orch._ensure_slot_initialized(slot, fallback_host="ignored", fallback_db="ignored")
        assert slot == {"username": "u", "password": "p", "host": "h", "port": "3307", "dbname": "d"}

    def test_partial_fill(self, tmp_path):
        orch = self._orch(tmp_path)
        slot = {"username": "existing_user"}
        orch._ensure_slot_initialized(slot, fallback_host="fb", fallback_db="db")
        assert slot["username"] == "existing_user"
        assert slot["host"] == "fb"


# ---------------------------------------------------------------------------
# from_env
# ---------------------------------------------------------------------------

class TestFromEnv:
    REQUIRED_ENV = {
        "AWS_REGION": "us-east-1",
        "RDS_SLOT_SECRET_ID": "secret1",
        "RDS_ADMIN_SECRET_ID": "secret2",
        "OPENEMR_SITES_MOUNT_ROOT": "/mnt/sites",
        "OPENEMR_ECS_CLUSTER": "cluster",
        "OPENEMR_ECS_SERVICE": "service",
    }

    def test_success(self, monkeypatch):
        for k, v in self.REQUIRED_ENV.items():
            monkeypatch.setenv(k, v)
        monkeypatch.delenv("OPENEMR_HEALTHCHECK_URL", raising=False)

        orch = RotationOrchestrator.from_env(dry_run=True)

        assert orch.ctx.region == "us-east-1"
        assert orch.ctx.rds_slots_secret_id == "secret1"
        assert orch.ctx.dry_run is True
        assert orch.ctx.openemr_health_url is None

    def test_with_health_url(self, monkeypatch):
        for k, v in self.REQUIRED_ENV.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("OPENEMR_HEALTHCHECK_URL", "https://example.com/health")

        orch = RotationOrchestrator.from_env(dry_run=False)

        assert orch.ctx.openemr_health_url == "https://example.com/health"
        assert orch.ctx.dry_run is False

    @pytest.mark.parametrize("missing_var", [
        "AWS_REGION",
        "RDS_SLOT_SECRET_ID",
        "RDS_ADMIN_SECRET_ID",
        "OPENEMR_SITES_MOUNT_ROOT",
        "OPENEMR_ECS_CLUSTER",
        "OPENEMR_ECS_SERVICE",
    ])
    def test_missing_required_env_raises(self, monkeypatch, missing_var):
        for k, v in self.REQUIRED_ENV.items():
            monkeypatch.setenv(k, v)
        monkeypatch.delenv(missing_var)

        with pytest.raises(RuntimeError, match="Missing required environment variables"):
            RotationOrchestrator.from_env(dry_run=True)

    def test_multiple_missing_vars_listed(self, monkeypatch):
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("RDS_SLOT_SECRET_ID", raising=False)
        monkeypatch.delenv("RDS_ADMIN_SECRET_ID", raising=False)
        monkeypatch.delenv("OPENEMR_SITES_MOUNT_ROOT", raising=False)
        monkeypatch.delenv("OPENEMR_ECS_CLUSTER", raising=False)
        monkeypatch.delenv("OPENEMR_ECS_SERVICE", raising=False)

        with pytest.raises(RuntimeError, match="AWS_REGION.*RDS_SLOT_SECRET_ID"):
            RotationOrchestrator.from_env(dry_run=True)


# ---------------------------------------------------------------------------
# main_json_error
# ---------------------------------------------------------------------------

class TestMainJsonError:
    def test_formats_error(self):
        result = main_json_error(RuntimeError("something broke"))
        assert '"status": "error"' in result
        assert "something broke" in result

    def test_returns_valid_json(self):
        import json
        parsed = json.loads(main_json_error(ValueError("bad")))
        assert parsed["status"] == "error"
        assert parsed["error"] == "bad"


# ---------------------------------------------------------------------------
# Dry-run rotation: config matches active slot A -> performs standby update
# ---------------------------------------------------------------------------

class TestDryRunRotationActiveA:
    @patch("credential_rotation.rotate.validate_rds_connection")
    def test_config_matches_active_a_dry_run(self, mock_validate_rds, tmp_path):
        _write_sqlconf(tmp_path, host="db-a", username="openemr_a", password="pass-a")
        ctx = _make_ctx(tmp_path, dry_run=True)
        orch = RotationOrchestrator(ctx)
        orch.secrets = _FakeSecrets(_default_rds_payload("A"))
        orch._upsert_openemr_db_user = MagicMock()

        orch.rotate()

        mock_validate_rds.assert_called_once()


# ---------------------------------------------------------------------------
# Dry-run rotation: config matches active slot B -> _rotate_old_slot path
# ---------------------------------------------------------------------------

class TestDryRunRotationActiveB:
    def test_config_matches_active_b_dry_run(self, tmp_path):
        _write_sqlconf(tmp_path, host="db-b", username="openemr_b", password="pass-b")
        ctx = _make_ctx(tmp_path, dry_run=True)
        orch = RotationOrchestrator(ctx)
        orch.secrets = _FakeSecrets(_default_rds_payload("B"))
        orch._upsert_openemr_db_user = MagicMock()

        orch.rotate()


# ---------------------------------------------------------------------------
# _rotate_old_slot (dry_run=True)
# ---------------------------------------------------------------------------

class TestRotateOldSlotDryRun:
    def test_generates_new_password_but_no_mutation(self, tmp_path):
        ctx = _make_ctx(tmp_path, dry_run=True)
        orch = RotationOrchestrator(ctx)
        fake_secrets = _FakeSecrets(_default_rds_payload("A"))
        orch.secrets = fake_secrets

        original_payload = dict(fake_secrets._rds)
        orch._rotate_old_slot(new_active="A", old_slot="B")

        assert fake_secrets._rds == original_payload


# ---------------------------------------------------------------------------
# _upsert_openemr_db_user
# ---------------------------------------------------------------------------

class TestUpsertOpenemrDbUser:
    @patch("credential_rotation.rotate.pymysql.connect")
    def test_creates_user_and_grants(self, mock_connect, tmp_path):
        ctx = _make_ctx(tmp_path, dry_run=False)
        orch = RotationOrchestrator(ctx)

        admin_payload = {"username": "admin", "password": "adminpw", "host": "db", "port": "3306"}
        orch.secrets = _FakeSecrets(_default_rds_payload(), admin_payload=admin_payload)

        conn = MagicMock()
        mock_connect.return_value = conn
        cursor = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        slot = {"username": "openemr_a", "password": "newpw", "host": "db", "port": "3306", "dbname": "openemr"}
        orch._upsert_openemr_db_user(slot)

        assert cursor.execute.call_count == 4
        calls = [c[0][0] for c in cursor.execute.call_args_list]
        assert any("CREATE USER" in c for c in calls)
        assert any("ALTER USER" in c for c in calls)
        assert any("GRANT ALL" in c for c in calls)
        assert any("FLUSH" in c for c in calls)

    @patch("credential_rotation.rotate.pymysql.connect")
    def test_rejects_unsafe_dbname(self, mock_connect, tmp_path):
        ctx = _make_ctx(tmp_path, dry_run=False)
        orch = RotationOrchestrator(ctx)

        admin_payload = {"username": "admin", "password": "pw", "host": "db", "port": "3306"}
        orch.secrets = _FakeSecrets(_default_rds_payload(), admin_payload=admin_payload)

        conn = MagicMock()
        mock_connect.return_value = conn
        cursor = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        slot = {"username": "u", "password": "p", "host": "h", "port": "3306", "dbname": "db; DROP TABLE--"}
        with pytest.raises(ValueError, match="Unsupported dbname"):
            orch._upsert_openemr_db_user(slot)


# ---------------------------------------------------------------------------
# _load_admin_secret
# ---------------------------------------------------------------------------

class TestLoadAdminSecret:
    @patch("credential_rotation.rotate.pymysql.connect")
    def test_primary_password_works(self, mock_connect, tmp_path):
        ctx = _make_ctx(tmp_path, dry_run=False)
        orch = RotationOrchestrator(ctx)

        admin_payload = {"username": "admin", "password": "good", "host": "db", "port": "3306"}
        orch.secrets = _FakeSecrets(_default_rds_payload(), admin_payload=admin_payload)

        conn = MagicMock()
        mock_connect.return_value = conn

        result = orch._load_admin_secret()

        assert result["password"] == "good"

    @patch("credential_rotation.rotate.pymysql.connect")
    def test_fallback_to_slot_password(self, mock_connect, tmp_path):
        ctx = _make_ctx(tmp_path, dry_run=False)
        orch = RotationOrchestrator(ctx)

        rds_payload = _default_rds_payload()
        rds_payload["A"]["username"] = "admin"
        rds_payload["A"]["password"] = "slot-pw"
        admin_payload = {"username": "admin", "password": "stale", "host": "db", "port": "3306"}
        orch.secrets = _FakeSecrets(rds_payload, admin_payload=admin_payload)

        call_count = [0]
        def side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise pymysql.OperationalError("auth failed")
            return MagicMock()
        mock_connect.side_effect = side_effect

        result = orch._load_admin_secret()

        assert result["password"] == "slot-pw"

    @patch("credential_rotation.rotate.pymysql.connect")
    def test_raises_when_no_password_works(self, mock_connect, tmp_path):
        ctx = _make_ctx(tmp_path, dry_run=False)
        orch = RotationOrchestrator(ctx)

        admin_payload = {"username": "admin", "password": "stale", "host": "db", "port": "3306"}
        orch.secrets = _FakeSecrets(_default_rds_payload(), admin_payload=admin_payload)

        mock_connect.side_effect = pymysql.OperationalError("auth failed")

        with pytest.raises(RuntimeError, match="Admin credentials.*invalid"):
            orch._load_admin_secret()


# ---------------------------------------------------------------------------
# _rotate_admin_password
# ---------------------------------------------------------------------------

class TestRotateAdminPassword:
    @patch("credential_rotation.rotate.pymysql.connect")
    def test_alters_user_validates_and_updates_secret(self, mock_connect, tmp_path):
        ctx = _make_ctx(tmp_path, dry_run=False)
        orch = RotationOrchestrator(ctx)

        admin_payload = {"username": "admin", "password": "old", "host": "db", "port": "3306"}
        fake_secrets = _FakeSecrets(_default_rds_payload(), admin_payload=admin_payload)
        orch.secrets = fake_secrets

        conn = MagicMock()
        mock_connect.return_value = conn
        cursor = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        orch._rotate_admin_password()

        assert cursor.execute.call_count == 1
        alter_call = cursor.execute.call_args[0][0]
        assert "ALTER USER" in alter_call
        assert fake_secrets._admin["password"] != "old"
        assert mock_connect.call_count == 3  # _load_admin_secret + ALTER conn + validation conn


# ---------------------------------------------------------------------------
# sync_db_users
# ---------------------------------------------------------------------------

class TestSyncDbUsers:
    @patch("credential_rotation.rotate.validate_rds_connection")
    @patch("credential_rotation.rotate.pymysql.connect")
    def test_syncs_both_slots(self, mock_connect, mock_validate, tmp_path):
        ctx = _make_ctx(tmp_path, dry_run=False)
        orch = RotationOrchestrator(ctx)

        admin_payload = {"username": "admin", "password": "pw", "host": "db", "port": "3306"}
        orch.secrets = _FakeSecrets(_default_rds_payload(), admin_payload=admin_payload)

        conn = MagicMock()
        mock_connect.return_value = conn
        cursor = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        orch.sync_db_users()

        assert mock_validate.call_count == 2


# ---------------------------------------------------------------------------
# Full rotation flow: non-dry-run with rollback on failure
# ---------------------------------------------------------------------------

class TestFullRotationRollback:
    @patch("credential_rotation.rotate.force_new_ecs_deployment")
    @patch("credential_rotation.rotate.validate_openemr_health")
    @patch("credential_rotation.rotate.validate_rds_connection")
    @patch("credential_rotation.rotate.atomic_write")
    @patch("credential_rotation.rotate.pymysql.connect")
    def test_rollback_on_ecs_deployment_failure(
        self, mock_connect, mock_atomic_write, mock_validate_rds,
        mock_health, mock_ecs_deploy, tmp_path
    ):
        _write_sqlconf(tmp_path, host="db-a", username="openemr_a", password="pass-a")
        ctx = _make_ctx(tmp_path, dry_run=False)
        orch = RotationOrchestrator(ctx)

        admin_payload = {"username": "admin", "password": "pw", "host": "db-a", "port": "3306"}
        orch.secrets = _FakeSecrets(_default_rds_payload("A"), admin_payload=admin_payload)

        conn = MagicMock()
        mock_connect.return_value = conn
        cursor = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        call_count = [0]
        def ecs_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("ECS deployment timeout")
        mock_ecs_deploy.side_effect = ecs_side_effect

        with pytest.raises(RuntimeError, match="ECS deployment timeout"):
            orch.rotate()

        assert mock_atomic_write.call_count == 2
        rollback_call = mock_atomic_write.call_args_list[1]
        assert "pass-a" in rollback_call[0][1]


# ---------------------------------------------------------------------------
# RotationContext dataclass
# ---------------------------------------------------------------------------

class TestRotationContext:
    def test_fields(self):
        ctx = RotationContext(
            region="us-west-2",
            rds_slots_secret_id="s1",
            rds_admin_secret_id="s2",
            sites_mount_root="/mnt",
            ecs_cluster_name="c",
            ecs_service_name="s",
            openemr_health_url="https://h",
            dry_run=False,
        )
        assert ctx.region == "us-west-2"
        assert ctx.dry_run is False
        assert ctx.openemr_health_url == "https://h"
