"""Unit tests for version module."""

from pathlib import Path
from unittest.mock import patch

from openemr_ecs.version import VERSION_FILE, __version__, _read_version


class TestVersion:
    """Tests for version module."""

    def test_version_is_string(self):
        """Test that __version__ is a string."""
        assert isinstance(__version__, str)
        assert len(__version__) > 0

    def test_version_format(self):
        """Test that version follows semantic versioning format."""
        version_parts = __version__.split(".")
        assert len(version_parts) >= 2  # At least major.minor
        # Check that all parts are numeric or contain valid version info
        for part in version_parts[:2]:  # At least first two should be numeric
            assert part.isdigit()

    def test_read_version_with_existing_file(self):
        """Test that _read_version reads from existing VERSION file."""
        # The VERSION file should exist in the project
        if VERSION_FILE.exists():
            version = _read_version()
            assert isinstance(version, str)
            assert len(version) > 0
            # Should match the actual version in the file
            actual_version = VERSION_FILE.read_text().strip()
            assert version == actual_version

    @patch.object(Path, "exists")
    def test_read_version_fallback_when_file_missing(self, mock_exists):
        """Test that _read_version falls back to 0.0.0 when VERSION file doesn't exist."""
        mock_exists.return_value = False
        version = _read_version()
        assert version == "0.0.0"
