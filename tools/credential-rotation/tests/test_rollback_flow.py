import os
from pathlib import Path

import pytest

from credential_rotation.rotate import RotationOrchestrator, RotationContext

_requires_root = pytest.mark.skipif(os.getuid() != 0, reason="atomic_write chowns to apache; requires root")


class _DummyOrchestrator(RotationOrchestrator):
    def __init__(self, context):
        super().__init__(context)
        self.validated = False

    def _validate_runtime(self, rds_slot):
        self.validated = True


@_requires_root
def test_rollback_restores_original_sqlconf(tmp_path: Path):
    sqlconf = tmp_path / "sqlconf.php"
    sqlconf.write_text("original", encoding="utf-8")

    ctx = RotationContext(
        region="us-east-1",
        rds_slots_secret_id="rds",
        rds_admin_secret_id="admin",
        sites_mount_root=str(tmp_path),
        ecs_cluster_name="cluster",
        ecs_service_name="service",
        openemr_health_url=None,
        dry_run=False,
    )
    orch = _DummyOrchestrator(ctx)

    from credential_rotation import rotate as rotate_module

    def _no_refresh(region, cluster_name, service_name):
        return None

    rotate_module.force_new_ecs_deployment = _no_refresh

    orch._rollback(sqlconf, "original", {"host": "h", "port": 3306, "username": "u", "password": "p", "dbname": "d"})

    assert sqlconf.read_text(encoding="utf-8") == "original"
    assert orch.validated is True
