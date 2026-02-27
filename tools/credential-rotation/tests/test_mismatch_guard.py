from pathlib import Path
from unittest.mock import patch

import pytest

from credential_rotation.rotate import RotationContext, RotationOrchestrator


class _FakeState:
    def __init__(self, payload):
        self.payload = payload

    @property
    def active_slot(self):
        return self.payload["active_slot"]

    def slot(self, name):
        return self.payload[name]


class _FakeSecrets:
    def __init__(self, rds_payload):
        self._rds = _FakeState(rds_payload)

    def get_secret(self, secret_id):
        if secret_id == "rds":
            return self._rds
        return _FakeState({"username": "admin", "password": "pw", "host": "db", "port": "3306"})

    @staticmethod
    def standby_slot(active):
        return "B" if active == "A" else "A"

    def put_payload(self, secret_id, payload):
        return None


def test_sqlconf_matches_standby_auto_reconciles(tmp_path: Path):
    """When sqlconf points at standby (B) but secrets say active=A, auto-reconcile by flipping active_slot."""
    default_dir = tmp_path / "default"
    default_dir.mkdir(parents=True, exist_ok=True)
    sqlconf = default_dir / "sqlconf.php"
    sqlconf.write_text(
        "\n".join(
            [
                "<?php",
                "$host   = 'db-b';",
                "$port   = '3306';",
                "$login  = 'openemr_b';",
                "$pass   = 'pass-b';",
                "$dbase  = 'openemr';",
            ]
        ),
        encoding="utf-8",
    )

    ctx = RotationContext(
        region="us-east-1",
        rds_slots_secret_id="rds",
        rds_admin_secret_id="admin",
        sites_mount_root=str(tmp_path),
        ecs_cluster_name="cluster",
        ecs_service_name="service",
        openemr_health_url=None,
        dry_run=True,
    )

    orchestrator = RotationOrchestrator(ctx)
    orchestrator.secrets = _FakeSecrets(
        rds_payload={
            "active_slot": "A",
            "A": {"host": "db-a", "port": "3306", "username": "openemr_a", "password": "pass-a", "dbname": "openemr"},
            "B": {"host": "db-b", "port": "3306", "username": "openemr_b", "password": "pass-b", "dbname": "openemr"},
        },
    )

    orchestrator.rotate()


@patch("credential_rotation.rotate.validate_rds_connection")
@patch("credential_rotation.rotate.validate_openemr_health")
def test_sqlconf_matches_neither_slot_bootstraps(mock_health, mock_validate_rds, tmp_path: Path):
    """When sqlconf matches neither slot (e.g. first run), bootstrap slots from current config."""
    default_dir = tmp_path / "default"
    default_dir.mkdir(parents=True, exist_ok=True)
    sqlconf = default_dir / "sqlconf.php"
    sqlconf.write_text(
        "\n".join(
            [
                "<?php",
                "$host   = 'db.example.com';",
                "$port   = '3306';",
                "$login  = 'admin';",
                "$pass   = 'current-secret';",
                "$dbase  = 'openemr';",
            ]
        ),
        encoding="utf-8",
    )

    ctx = RotationContext(
        region="us-east-1",
        rds_slots_secret_id="rds",
        rds_admin_secret_id="admin",
        sites_mount_root=str(tmp_path),
        ecs_cluster_name="cluster",
        ecs_service_name="service",
        openemr_health_url=None,
        dry_run=True,
    )

    orchestrator = RotationOrchestrator(ctx)
    orchestrator.secrets = _FakeSecrets(
        rds_payload={
            "active_slot": "A",
            "A": {"host": "db-a", "port": "3306", "username": "openemr_a", "password": "placeholder", "dbname": "openemr"},
            "B": {"host": "db-b", "port": "3306", "username": "openemr_b", "password": "placeholder", "dbname": "openemr"},
        },
    )
    orchestrator._upsert_openemr_db_user = lambda _: None

    orchestrator.rotate()
