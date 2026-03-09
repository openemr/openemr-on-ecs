"""Tests for cli module: argument parsing, main entrypoint, fix_permissions."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from credential_rotation.cli import build_parser, fix_permissions, main


class TestBuildParser:
    def test_defaults(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.dry_run is False
        assert args.log_json is False
        assert args.fix_permissions is False
        assert args.sync_db_users is False

    def test_dry_run(self):
        parser = build_parser()
        args = parser.parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_log_json(self):
        parser = build_parser()
        args = parser.parse_args(["--log-json"])
        assert args.log_json is True

    def test_fix_permissions_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--fix-permissions"])
        assert args.fix_permissions is True

    def test_sync_db_users_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--sync-db-users"])
        assert args.sync_db_users is True

    def test_combined_flags(self):
        parser = build_parser()
        args = parser.parse_args(["--dry-run", "--log-json"])
        assert args.dry_run is True
        assert args.log_json is True


class TestFixPermissions:
    def test_returns_1_when_sqlconf_missing(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("OPENEMR_SITES_MOUNT_ROOT", str(tmp_path))
        assert fix_permissions() == 1

    def test_returns_0_and_chmods_when_exists(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("OPENEMR_SITES_MOUNT_ROOT", str(tmp_path))
        default = tmp_path / "default"
        default.mkdir()
        sqlconf = default / "sqlconf.php"
        sqlconf.write_text("<?php\n", encoding="utf-8")
        sqlconf.chmod(0o400)

        assert fix_permissions() == 0
        assert oct(sqlconf.stat().st_mode & 0o777) == "0o644"

    def test_uses_default_mount_root(self, monkeypatch):
        monkeypatch.delenv("OPENEMR_SITES_MOUNT_ROOT", raising=False)
        result = fix_permissions()
        assert result == 1


class TestMain:
    @patch("credential_rotation.cli.RotationOrchestrator")
    def test_rotation_success(self, mock_orch_cls, monkeypatch):
        monkeypatch.setattr("sys.argv", ["cli", "--dry-run"])
        mock_orch_cls.from_env.return_value = MagicMock()

        result = main()

        assert result == 0
        mock_orch_cls.from_env.assert_called_once_with(dry_run=True)
        mock_orch_cls.from_env.return_value.rotate.assert_called_once()

    @patch("credential_rotation.cli.RotationOrchestrator")
    def test_rotation_failure_returns_1(self, mock_orch_cls, monkeypatch):
        monkeypatch.setattr("sys.argv", ["cli"])
        mock_orch_cls.from_env.side_effect = RuntimeError("missing env")

        result = main()

        assert result == 1

    @patch("credential_rotation.cli.RotationOrchestrator")
    def test_rotation_success_json_output(self, mock_orch_cls, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["cli", "--log-json", "--dry-run"])
        mock_orch_cls.from_env.return_value = MagicMock()

        result = main()

        assert result == 0
        captured = capsys.readouterr()
        assert '"status": "ok"' in captured.out

    @patch("credential_rotation.cli.RotationOrchestrator")
    def test_rotation_failure_json_output(self, mock_orch_cls, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["cli", "--log-json"])
        mock_orch_cls.from_env.return_value = MagicMock()
        mock_orch_cls.from_env.return_value.rotate.side_effect = RuntimeError("boom")

        result = main()

        assert result == 1
        captured = capsys.readouterr()
        assert '"status": "error"' in captured.out
        assert "boom" in captured.out

    @patch("credential_rotation.cli.RotationOrchestrator")
    def test_sync_db_users_success(self, mock_orch_cls, monkeypatch):
        monkeypatch.setattr("sys.argv", ["cli", "--sync-db-users"])
        mock_orch_cls.from_env.return_value = MagicMock()

        result = main()

        assert result == 0
        mock_orch_cls.from_env.assert_called_once_with(dry_run=False)
        mock_orch_cls.from_env.return_value.sync_db_users.assert_called_once()

    @patch("credential_rotation.cli.RotationOrchestrator")
    def test_sync_db_users_failure(self, mock_orch_cls, monkeypatch):
        monkeypatch.setattr("sys.argv", ["cli", "--sync-db-users"])
        mock_orch_cls.from_env.return_value = MagicMock()
        mock_orch_cls.from_env.return_value.sync_db_users.side_effect = RuntimeError("db error")

        result = main()

        assert result == 1

    @patch("credential_rotation.cli.RotationOrchestrator")
    def test_sync_db_users_json_output(self, mock_orch_cls, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["cli", "--sync-db-users", "--log-json"])
        mock_orch_cls.from_env.return_value = MagicMock()

        result = main()

        assert result == 0
        captured = capsys.readouterr()
        assert '"action": "sync_db_users"' in captured.out

    def test_fix_permissions_mode(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("sys.argv", ["cli", "--fix-permissions"])
        monkeypatch.setenv("OPENEMR_SITES_MOUNT_ROOT", str(tmp_path))
        default = tmp_path / "default"
        default.mkdir()
        (default / "sqlconf.php").write_text("<?php\n", encoding="utf-8")

        result = main()

        assert result == 0
