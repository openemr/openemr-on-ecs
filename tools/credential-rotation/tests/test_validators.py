"""Tests for validators module: validate_rds_connection and validate_openemr_health."""

from unittest.mock import MagicMock, patch

import pytest
from credential_rotation.validators import (
    ValidationError,
    validate_openemr_health,
    validate_rds_connection,
)


class TestValidateRDSConnection:
    def _make_slot(self, **overrides):
        base = {
            "host": "db.example.com",
            "username": "admin",
            "password": "secret",
            "dbname": "openemr",
            "port": "3306",
        }
        base.update(overrides)
        return base

    @patch("credential_rotation.validators.pymysql.connect")
    def test_success(self, mock_connect):
        conn = MagicMock()
        mock_connect.return_value = conn
        cursor = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = (1,)

        validate_rds_connection(self._make_slot())

        mock_connect.assert_called_once()
        conn.close.assert_called_once()

    @patch("credential_rotation.validators.pymysql.connect")
    def test_connection_failure_raises(self, mock_connect):
        mock_connect.side_effect = Exception("Connection refused")

        with pytest.raises(Exception, match="Connection refused"):
            validate_rds_connection(self._make_slot())

    @patch("credential_rotation.validators.pymysql.connect")
    def test_unexpected_query_result_raises(self, mock_connect):
        conn = MagicMock()
        mock_connect.return_value = conn
        cursor = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = (99,)

        with pytest.raises(ValidationError, match="unexpected result"):
            validate_rds_connection(self._make_slot())

    @patch("credential_rotation.validators.pymysql.connect")
    def test_null_query_result_raises(self, mock_connect):
        conn = MagicMock()
        mock_connect.return_value = conn
        cursor = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = None

        with pytest.raises(ValidationError, match="unexpected result"):
            validate_rds_connection(self._make_slot())

    @patch("credential_rotation.validators.pymysql.connect")
    def test_custom_timeout(self, mock_connect):
        conn = MagicMock()
        mock_connect.return_value = conn
        cursor = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = (1,)

        validate_rds_connection(self._make_slot(), connect_timeout=10)

        call_kwargs = mock_connect.call_args[1]
        assert call_kwargs["connect_timeout"] == 10


class TestValidateOpenEMRHealth:
    def test_none_url_is_noop(self):
        validate_openemr_health(None)

    @patch("credential_rotation.validators.requests.get")
    def test_success_200(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200)
        validate_openemr_health("https://example.com/health")
        mock_get.assert_called_once()

    @patch("credential_rotation.validators.requests.get")
    def test_redirect_is_acceptable(self, mock_get):
        mock_get.return_value = MagicMock(status_code=301)
        validate_openemr_health("https://example.com/health")

    @patch("credential_rotation.validators.requests.get")
    def test_network_error_is_non_fatal(self, mock_get, capsys):
        mock_get.side_effect = ConnectionError("unreachable")
        validate_openemr_health("https://example.com/health")
        captured = capsys.readouterr()
        assert "WARNING" in captured.out

    @patch("credential_rotation.validators.requests.get")
    def test_500_prints_warning(self, mock_get, capsys):
        mock_get.return_value = MagicMock(status_code=500)
        validate_openemr_health("https://example.com/health")
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
