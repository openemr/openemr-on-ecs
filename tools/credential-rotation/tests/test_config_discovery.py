"""Tests for config_discovery module: discover_runtime_paths."""

from pathlib import Path

import pytest

from credential_rotation.config_discovery import RuntimeConfigPaths, discover_runtime_paths


class TestDiscoverRuntimePaths:
    def test_returns_sqlconf_path_when_exists(self, tmp_path: Path):
        default_dir = tmp_path / "default"
        default_dir.mkdir()
        sqlconf = default_dir / "sqlconf.php"
        sqlconf.write_text("<?php\n$host = 'db';\n", encoding="utf-8")

        result = discover_runtime_paths(str(tmp_path))

        assert isinstance(result, RuntimeConfigPaths)
        assert result.sqlconf_path == sqlconf
        assert result.cache_config_path is None

    def test_raises_when_sqlconf_missing(self, tmp_path: Path):
        default_dir = tmp_path / "default"
        default_dir.mkdir()

        with pytest.raises(FileNotFoundError, match="sqlconf.php not found"):
            discover_runtime_paths(str(tmp_path))

    def test_raises_when_default_dir_missing(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="sqlconf.php not found"):
            discover_runtime_paths(str(tmp_path))

    def test_frozen_dataclass(self, tmp_path: Path):
        default_dir = tmp_path / "default"
        default_dir.mkdir()
        (default_dir / "sqlconf.php").write_text("<?php\n", encoding="utf-8")

        result = discover_runtime_paths(str(tmp_path))

        with pytest.raises(AttributeError):
            result.sqlconf_path = Path("/other")  # type: ignore[misc]
