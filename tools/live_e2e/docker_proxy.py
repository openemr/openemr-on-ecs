#!/usr/bin/env python3
"""Transparent Docker proxy that records only bounded timing metadata."""

from __future__ import annotations

import fcntl
import json
import os

# A subprocess is required for this transparent fixed-executable proxy.
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    """Forward Docker arguments and append a credential-free timing record."""

    real_docker = Path(os.environ.get("OPENEMR_E2E_REAL_DOCKER", ""))
    timing_path = Path(os.environ.get("OPENEMR_E2E_DOCKER_TIMINGS", ""))
    if (
        not real_docker.is_absolute()
        or not real_docker.is_file()
        or not os.access(real_docker, os.X_OK)
        or not timing_path.is_absolute()
    ):
        print("live E2E Docker proxy is not safely configured", file=sys.stderr)
        return 126

    started = time.monotonic()
    try:
        # The executable is a validated absolute Docker path; arguments are
        # forwarded as an argv list without a shell.
        completed = subprocess.run(
            [str(real_docker), *sys.argv[1:]],
            check=False,
        )
        returncode = completed.returncode
    except KeyboardInterrupt:
        returncode = 130
    duration = round(time.monotonic() - started, 3)
    _append_record(
        timing_path,
        {
            "schema_version": 1,
            "category": _category(sys.argv[1:]),
            "duration_seconds": duration,
            "returncode": returncode,
        },
    )
    return returncode


def _category(arguments: list[str]) -> str:
    if arguments and arguments[0] == "build":
        return "build"
    if len(arguments) >= 2 and arguments[0] == "buildx" and arguments[1] == "build":
        return "build"
    if arguments and arguments[0] == "push":
        return "publish"
    return "other"


def _append_record(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        handle = os.fdopen(descriptor, "a", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise
    with handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(value, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    raise SystemExit(main())
