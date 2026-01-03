"""Version information for OpenEMR on ECS CDK stack.

This module automatically reads the version from the VERSION file in the project root.
"""

from pathlib import Path

# Get the project root (2 levels up from this file: openemr_ecs/version.py)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = PROJECT_ROOT / "VERSION"


def _read_version() -> str:
    """Read version from VERSION file."""
    if VERSION_FILE.exists():
        version = VERSION_FILE.read_text().strip()
        return version
    else:
        # Fallback if VERSION file doesn't exist
        return "0.0.0"


__version__ = _read_version()
