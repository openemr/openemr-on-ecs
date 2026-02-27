"""Tests for openemr_ecs/constants.py.

Validates that constant values have correct formats, types, and ranges.
"""

import re

from openemr_ecs.constants import StackConstants


class TestNetworkConstants:
    def test_default_cidr_is_valid(self):
        pattern = r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}$"
        assert re.match(pattern, StackConstants.DEFAULT_CIDR)

    def test_default_cidr_private_range(self):
        """CIDR should be in a private range (10.x, 172.16-31.x, or 192.168.x)."""
        assert StackConstants.DEFAULT_CIDR.startswith(("10.", "172.", "192.168."))

    def test_ssl_regeneration_days_positive(self):
        assert isinstance(StackConstants.DEFAULT_SSL_REGENERATION_DAYS, int)
        assert StackConstants.DEFAULT_SSL_REGENERATION_DAYS > 0


class TestPortConstants:
    def test_mysql_port(self):
        assert StackConstants.MYSQL_PORT == 3306

    def test_valkey_port(self):
        assert StackConstants.VALKEY_PORT == 6379

    def test_container_port_https(self):
        assert StackConstants.CONTAINER_PORT == 443

    def test_ports_are_integers(self):
        for port in [StackConstants.MYSQL_PORT, StackConstants.VALKEY_PORT, StackConstants.CONTAINER_PORT]:
            assert isinstance(port, int)
            assert 1 <= port <= 65535


class TestVersionConstants:
    def test_emr_release_label_format(self):
        """EMR release labels follow the pattern emr-X.Y.Z."""
        assert re.match(r"^emr-\d+\.\d+\.\d+$", StackConstants.EMR_SERVERLESS_RELEASE_LABEL)

    def test_openemr_version_format(self):
        assert re.match(r"^\d+\.\d+\.\d+$", StackConstants.OPENEMR_VERSION)

    def test_credential_rotation_python_version_format(self):
        assert re.match(r"^\d+\.\d+$", StackConstants.CREDENTIAL_ROTATION_PYTHON_VERSION)

    def test_aurora_engine_version_is_set(self):
        assert StackConstants.AURORA_MYSQL_ENGINE_VERSION is not None

    def test_lambda_runtime_is_set(self):
        assert StackConstants.LAMBDA_PYTHON_RUNTIME is not None
        runtime_name = getattr(StackConstants.LAMBDA_PYTHON_RUNTIME, "name", str(StackConstants.LAMBDA_PYTHON_RUNTIME))
        assert "python" in runtime_name.lower()
