"""Atomic EFS file editing utilities."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Dict

_SQLCONF_KEY_TO_VAR = {
    "host": "host",
    "port": "port",
    "username": "login",
    "password": "pass",
    "dbname": "dbase",
}


def parse_sqlconf(content: str) -> Dict[str, str]:
    """Parse key connection fields from OpenEMR `sqlconf.php` content."""

    values: Dict[str, str] = {}
    patterns = {
        "host": r"^\$host\s*=\s*['\"]([^'\"]+)['\"]\s*;",
        "port": r"^\$port\s*=\s*['\"]([^'\"]+)['\"]\s*;",
        "username": r"^\$login\s*=\s*['\"]([^'\"]+)['\"]\s*;",
        "password": r"^\$pass\s*=\s*['\"]([^'\"]+)['\"]\s*;",
        "dbname": r"^\$dbase\s*=\s*['\"]([^'\"]+)['\"]\s*;",
    }

    for key, pattern in patterns.items():
        m = re.search(pattern, content, flags=re.MULTILINE)
        if m:
            values[key] = m.group(1)

    return values


def render_sqlconf(content: str, slot: Dict[str, str]) -> str:
    """Render updated sqlconf content by replacing key DB fields."""

    updated = content
    replacements = {
        "host": slot["host"],
        "port": str(slot.get("port", "3306")),
        "username": slot["username"],
        "password": slot["password"],
        "dbname": slot["dbname"],
    }

    for key, value in replacements.items():
        php_var = _SQLCONF_KEY_TO_VAR[key]
        pattern = rf"^(\${php_var}\s*=\s*['\"])([^'\"]+)(['\"]\s*;)"
        updated, count = re.subn(pattern, rf"\g<1>{value}\g<3>", updated, count=1, flags=re.MULTILINE)
        if count != 1:
            raise ValueError(f"Unable to locate ${php_var} assignment in sqlconf.php")

    return updated


_APACHE_UID = 1000
_APACHE_GID = 101


def atomic_write(path: Path, content: str) -> None:
    """Atomically write content on EFS: temp file, fsync, set permissions, rename.

    The file is chown'd to apache (UID 1000, GID 101) with mode 0o644.
    OpenEMR's startup script later tightens this to 400 owned by apache;
    because the owner already matches, Apache can still read the file.
    Parent directories are set to 755 so Apache can traverse to the file.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    for parent in [path.parent, path.parent.parent]:
        if parent.exists():
            try:
                os.chmod(parent, 0o755)
                os.chown(parent, _APACHE_UID, _APACHE_GID)
            except OSError:
                pass
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(path.parent), prefix=f".{path.name}.", delete=False
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_name = tmp.name
        os.fchmod(tmp.fileno(), 0o644)
        os.fchown(tmp.fileno(), _APACHE_UID, _APACHE_GID)

    os.replace(tmp_name, path)
    os.chmod(path, 0o644)
    os.chown(path, _APACHE_UID, _APACHE_GID)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")
