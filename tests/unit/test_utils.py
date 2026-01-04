"""Unit tests for utility functions."""

from openemr_ecs.utils import get_resource_suffix, is_true


class TestIsTrue:
    """Tests for is_true utility function."""

    def test_is_true_with_string_true(self):
        """Test that string 'true' returns True."""
        assert is_true("true") is True
        assert is_true("True") is True
        assert is_true("TRUE") is True
        assert is_true("TrUe") is True

    def test_is_true_with_false_values(self):
        """Test that non-true values return False."""
        assert is_true("false") is False
        assert is_true("False") is False
        assert is_true("FALSE") is False
        assert is_true("") is False
        assert is_true("0") is False
        assert is_true("1") is False
        assert is_true("yes") is False
        assert is_true("no") is False

    def test_is_true_with_none(self):
        """Test that None returns False."""
        assert is_true(None) is False


class TestGetResourceSuffix:
    """Tests for get_resource_suffix utility function."""

    def test_get_resource_suffix_with_value(self):
        """Test that provided suffix is returned."""
        context = {"openemr_resource_suffix": "production"}
        assert get_resource_suffix(context) == "production"

    def test_get_resource_suffix_with_default(self):
        """Test that default suffix is returned when not provided."""
        context = {}
        assert get_resource_suffix(context) == "default"

    def test_get_resource_suffix_with_empty_context(self):
        """Test that default suffix is returned with empty context."""
        context = {"other_key": "value"}
        assert get_resource_suffix(context) == "default"
