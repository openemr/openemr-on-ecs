import os
from pathlib import Path

import pytest

from credential_rotation.efs_editor import atomic_write

_requires_root = pytest.mark.skipif(os.getuid() != 0, reason="atomic_write chowns to apache; requires root")


@_requires_root
def test_atomic_write_replaces_content(tmp_path: Path):
    target = tmp_path / "sqlconf.php"
    target.write_text("old", encoding="utf-8")

    atomic_write(target, "new")

    assert target.read_text(encoding="utf-8") == "new"
