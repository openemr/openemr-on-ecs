"""Optional integration coverage for the live TLS MySQL import-worker harness.

Skipped by default. Enable with OPENEMR_IMPORT_MYSQL_INTEGRATION=1 to run the
compose-backed script (slow; pulls OpenEMR/MariaDB images and waits for schema
bootstrap). CI invokes scripts/ci-import-worker-mysql.sh directly.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci-import-worker-mysql.sh"


@pytest.mark.integration
@pytest.mark.slow
def test_ci_import_worker_mysql_script_passes() -> None:
    if os.environ.get("OPENEMR_IMPORT_MYSQL_INTEGRATION") != "1":
        pytest.skip("Set OPENEMR_IMPORT_MYSQL_INTEGRATION=1 to run live MySQL import integration")
    assert SCRIPT.is_file(), "ci-import-worker-mysql.sh is missing"
    completed = subprocess.run(
        [str(SCRIPT)],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "import worker MySQL integration failed\n"
            f"stdout:\n{completed.stdout[-4000:]}\n"
            f"stderr:\n{completed.stderr[-4000:]}"
        )
