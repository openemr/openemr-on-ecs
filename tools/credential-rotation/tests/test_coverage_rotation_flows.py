"""Non-dry-run rotation flows, admin fallback exhaustion, and AWS/EFS helper wiring."""

import json
import logging
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pymysql
import pytest
from credential_rotation import efs_editor
from credential_rotation.cli import main
from credential_rotation.rotate import RotationContext, RotationOrchestrator
from credential_rotation.secrets_manager import SecretsManagerSlots

ADMIN = {"username": "dbadmin", "password": "adminpw", "host": "db-a", "port": "3306"}


def _ctx(tmp_path: Path, dry_run: bool = False) -> RotationContext:
    return RotationContext(
        region="us-east-1",
        rds_slots_secret_id="rds-slot",
        rds_admin_secret_id="rds-admin",
        sites_mount_root=str(tmp_path),
        ecs_cluster_name="cluster",
        ecs_service_name="service",
        openemr_health_url="https://openemr.example.com/",
        dry_run=dry_run,
    )


def _write_sqlconf(tmp_path: Path, host: str, username: str, password: str) -> Path:
    default_dir = tmp_path / "default"
    default_dir.mkdir(parents=True, exist_ok=True)
    sqlconf = default_dir / "sqlconf.php"
    sqlconf.write_text(
        f"<?php\n$host   = '{host}';\n$port   = '3306';\n"
        f"$login  = '{username}';\n$pass   = '{password}';\n$dbase  = 'openemr';\n",
        encoding="utf-8",
    )
    return sqlconf


def _rds_payload(active: str = "A") -> dict:
    return {
        "active_slot": active,
        "A": {"host": "db-a", "port": "3306", "username": "openemr_a", "password": "pass-a", "dbname": "openemr"},
        "B": {"host": "db-b", "port": "3306", "username": "openemr_b", "password": "pass-b", "dbname": "openemr"},
    }


class _State:
    def __init__(self, payload):
        self.payload = payload

    @property
    def active_slot(self):
        return self.payload["active_slot"]

    def slot(self, name):
        return self.payload[name]


class _Secrets:
    """In-memory Secrets Manager that records every write."""

    def __init__(self, rds_payload, admin_payload=None):
        self.rds = json.loads(json.dumps(rds_payload))
        self.admin = dict(admin_payload or ADMIN)
        self.writes: list[tuple[str, dict]] = []

    def get_secret(self, secret_id):
        source = self.admin if secret_id == "rds-admin" else self.rds
        return _State(json.loads(json.dumps(source)))

    def put_payload(self, secret_id, payload):
        self.writes.append((secret_id, json.loads(json.dumps(payload))))
        if secret_id == "rds-admin":
            self.admin = dict(payload)
        else:
            self.rds = json.loads(json.dumps(payload))

    @staticmethod
    def standby_slot(active):
        return "B" if active == "A" else "A"


@pytest.fixture
def aws_and_db():
    """Patch every external side effect of a rotation run."""

    with (
        patch("credential_rotation.rotate.pymysql.connect") as connect,
        patch("credential_rotation.rotate.validate_rds_connection") as validate_rds,
        patch("credential_rotation.rotate.validate_openemr_health") as validate_health,
        patch("credential_rotation.rotate.force_new_ecs_deployment") as ecs_deploy,
        patch("credential_rotation.rotate.atomic_write") as write,
    ):
        yield {
            "connect": connect,
            "validate_rds": validate_rds,
            "validate_health": validate_health,
            "ecs_deploy": ecs_deploy,
            "atomic_write": write,
        }


def _orchestrator(tmp_path: Path, secrets: _Secrets, dry_run: bool = False) -> RotationOrchestrator:
    orchestrator = RotationOrchestrator(_ctx(tmp_path, dry_run=dry_run))
    orchestrator.secrets = secrets
    return orchestrator


class TestFullRotation:
    def test_flips_to_standby_then_rotates_old_slot_and_admin(self, tmp_path, aws_and_db):
        sqlconf = _write_sqlconf(tmp_path, "db-a", "openemr_a", "pass-a")
        secrets = _Secrets(_rds_payload("A"))

        _orchestrator(tmp_path, secrets).rotate()

        path, rendered = aws_and_db["atomic_write"].call_args.args
        assert path == sqlconf
        assert "$host   = 'db-b';" in rendered
        assert "$login  = 'openemr_b';" in rendered
        assert "$pass   = 'pass-b';" in rendered
        aws_and_db["ecs_deploy"].assert_called_once_with(
            region="us-east-1", cluster_name="cluster", service_name="service"
        )
        aws_and_db["validate_health"].assert_called_with("https://openemr.example.com/")

        assert secrets.rds["active_slot"] == "B"
        assert secrets.rds["B"]["password"] == "pass-b"
        assert secrets.rds["A"]["username"] == "openemr_a"
        assert secrets.rds["A"]["password"] not in ("pass-a", "")
        assert secrets.admin["password"] != "adminpw"
        written_ids = [secret_id for secret_id, _ in secrets.writes]
        assert written_ids == ["rds-slot", "rds-slot", "rds-admin"]
        assert secrets.writes[0][1]["active_slot"] == "B"

    def test_reconciles_when_sqlconf_already_uses_standby(self, tmp_path, aws_and_db):
        _write_sqlconf(tmp_path, "db-b", "openemr_b", "pass-b")
        secrets = _Secrets(_rds_payload("A"))

        _orchestrator(tmp_path, secrets).rotate()

        aws_and_db["atomic_write"].assert_not_called()
        aws_and_db["ecs_deploy"].assert_not_called()
        assert secrets.writes[0] == ("rds-slot", {**_rds_payload("A"), "active_slot": "B"})
        assert secrets.rds["active_slot"] == "B"
        assert secrets.rds["A"]["password"] != "pass-a"
        assert secrets.admin["password"] != "adminpw"

    def test_active_b_matching_sqlconf_rotates_old_slot_without_flip(self, tmp_path, aws_and_db):
        _write_sqlconf(tmp_path, "db-b", "openemr_b", "pass-b")
        secrets = _Secrets(_rds_payload("B"))

        _orchestrator(tmp_path, secrets).rotate()

        aws_and_db["atomic_write"].assert_not_called()
        assert secrets.rds["active_slot"] == "B"
        assert secrets.rds["B"]["password"] == "pass-b"
        assert secrets.rds["A"]["password"] != "pass-a"
        assert [secret_id for secret_id, _ in secrets.writes] == ["rds-slot", "rds-admin"]

    def test_bootstraps_dedicated_users_when_sqlconf_matches_neither_slot(self, tmp_path, aws_and_db):
        _write_sqlconf(tmp_path, "legacy-host", "dbadmin", "legacy-pw")
        secrets = _Secrets(_rds_payload("A"))

        _orchestrator(tmp_path, secrets).rotate()

        bootstrap_payload = secrets.writes[0][1]
        assert bootstrap_payload["active_slot"] == "A"
        assert bootstrap_payload["A"]["username"] == "openemr_a"
        assert bootstrap_payload["B"]["username"] == "openemr_b"
        assert bootstrap_payload["A"]["host"] == "legacy-host"
        assert bootstrap_payload["B"]["host"] == "legacy-host"
        assert bootstrap_payload["A"]["password"] not in ("pass-a", "legacy-pw")
        validated_users = [call.args[0]["username"] for call in aws_and_db["validate_rds"].call_args_list]
        assert validated_users[:2] == ["openemr_a", "openemr_b"]
        assert secrets.rds["active_slot"] == "B"


class TestAdminSecretFallbackExhausted:
    def test_matching_slot_password_also_rejected_raises_with_last_error(self, tmp_path):
        admin = {"username": "openemr_a", "password": "stale", "host": "db-a", "port": "3306"}
        secrets = _Secrets(_rds_payload("A"), admin_payload=admin)
        orchestrator = _orchestrator(tmp_path, secrets)

        with patch(
            "credential_rotation.rotate.pymysql.connect",
            side_effect=pymysql.OperationalError(1045, "Access denied"),
        ) as connect:
            with pytest.raises(RuntimeError, match=r"OperationalError: .*Access denied"):
                orchestrator._load_admin_secret()

        attempted = [call.kwargs["password"] for call in connect.call_args_list]
        assert attempted == ["stale", "pass-a"]
        assert secrets.writes == []


class TestSecretsManagerSlotsClient:
    def test_get_secret_and_put_payload_use_secrets_manager(self):
        client = MagicMock()
        client.get_secret_value.return_value = {
            "ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds",
            "SecretString": json.dumps({"active_slot": "A", "A": {}}),
        }
        with patch("credential_rotation.secrets_manager.boto3.client", return_value=client) as factory:
            slots = SecretsManagerSlots(region="us-east-1")
            state = slots.get_secret("rds")
            slots.put_payload("rds", {"active_slot": "B"})

        factory.assert_called_once_with("secretsmanager", region_name="us-east-1")
        client.get_secret_value.assert_called_once_with(SecretId="rds")
        assert state.secret_arn.endswith(":secret:rds")
        assert state.active_slot == "A"
        client.put_secret_value.assert_called_once_with(SecretId="rds", SecretString='{"active_slot":"B"}')


class TestAtomicWrite:
    def test_writes_content_with_apache_ownership_and_mode(self, tmp_path, monkeypatch):
        chown_calls = []
        fchown_calls = []
        monkeypatch.setattr(efs_editor.os, "chown", lambda path, uid, gid: chown_calls.append((Path(path), uid, gid)))
        monkeypatch.setattr(efs_editor.os, "fchown", lambda fd, uid, gid: fchown_calls.append((uid, gid)))
        target = tmp_path / "sites" / "default" / "sqlconf.php"

        efs_editor.atomic_write(target, "<?php $host = 'db';")

        assert target.read_text(encoding="utf-8") == "<?php $host = 'db';"
        assert stat.S_IMODE(target.stat().st_mode) == 0o644
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o755
        assert chown_calls == [
            (target.parent, 1000, 101),
            (target.parent.parent, 1000, 101),
            (target, 1000, 101),
        ]
        assert fchown_calls == [(1000, 101)]
        assert [p.name for p in target.parent.iterdir()] == ["sqlconf.php"]

    def test_parent_permission_errors_are_logged_not_fatal(self, tmp_path, monkeypatch, caplog):
        target = tmp_path / "default" / "sqlconf.php"

        def chown(path, uid, gid):
            if Path(path) != target:
                raise PermissionError("operation not permitted")

        monkeypatch.setattr(efs_editor.os, "chown", chown)
        monkeypatch.setattr(efs_editor.os, "fchown", lambda fd, uid, gid: None)

        with caplog.at_level(logging.WARNING, logger=efs_editor.logger.name):
            efs_editor.atomic_write(target, "new")

        assert target.read_text(encoding="utf-8") == "new"
        assert sum("Unable to normalize permissions" in r.getMessage() for r in caplog.records) == 2


class TestCliSyncJsonFailure:
    @patch("credential_rotation.cli.RotationOrchestrator")
    def test_sync_db_users_failure_emits_json_error(self, mock_orch_cls, monkeypatch, capsys):
        mock_orch_cls.from_env.return_value.sync_db_users.side_effect = RuntimeError("db unreachable")
        monkeypatch.setattr("sys.argv", ["credential-rotation", "--sync-db-users", "--log-json"])

        assert main() == 1

        assert json.loads(capsys.readouterr().out) == {"status": "error", "error": "db unreachable"}
        mock_orch_cls.from_env.assert_called_once_with(dry_run=False)
