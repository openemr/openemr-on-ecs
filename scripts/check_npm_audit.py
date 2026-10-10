#!/usr/bin/env python3
"""Run npm audit with one narrow, expiring bundled-dependency deferral."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ALLOWED_NAME = "brace-expansion"
ALLOWED_VERSION = "5.0.9"
ALLOWED_NODE = "node_modules/aws-cdk-lib/node_modules/brace-expansion"
# aws-cdk-lib bundles brace-expansion, so neither npm overrides nor
# `npm audit fix` can replace it; only an aws-cdk-lib release can.
ALLOWED_ADVISORIES = {
    1240103: "https://github.com/advisories/GHSA-q2hr-2g5m-vwhr",
    1240107: "https://github.com/advisories/GHSA-qhr7-859c-m2p7",
    1240111: "https://github.com/advisories/GHSA-6j4f-fj2g-mc7p",
}
REVIEW_DATE = date(2026, 11, 5)


def _package_versions(root: Path, failures: list[str]) -> tuple[str | None, str | None]:
    """Read the installed and locked versions for the deferred package."""

    package_path = root / ALLOWED_NODE / "package.json"
    lock_path = root / "package-lock.json"
    try:
        installed = json.loads(package_path.read_text(encoding="utf-8")).get("version")
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"cannot verify installed deferred package: {exc}")
        installed = None
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        locked = lock["packages"][ALLOWED_NODE]["version"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        failures.append(f"cannot verify locked deferred package: {exc}")
        locked = None
    if installed != locked:
        failures.append(f"{ALLOWED_NAME} install/lock mismatch: installed {installed}, locked {locked}")
    return installed, locked


def _advisory_identities(via: Any) -> dict[Any, Any] | None:
    """Map advisory source IDs to URLs, or return None for a malformed list."""

    if not isinstance(via, list) or not via or not all(isinstance(item, dict) for item in via):
        return None
    return {item.get("source"): item.get("url") for item in via}


def validate_report(report: dict[str, Any], root: Path, *, today: date) -> list[str]:
    """Return reasons the audit report exceeds the exact temporary deferral."""

    vulnerabilities = report.get("vulnerabilities")
    if not isinstance(vulnerabilities, dict):
        return ["npm audit report has no vulnerabilities object"]
    failures: list[str] = []
    for name, value in vulnerabilities.items():
        if name != ALLOWED_NAME:
            failures.append(f"unexpected vulnerable package: {name}")
            continue
        if not isinstance(value, dict):
            failures.append(f"{ALLOWED_NAME} advisory is not an object")
            continue
        if value.get("severity") != "high" or value.get("isDirect") is not False:
            failures.append(f"{ALLOWED_NAME} severity/directness changed")
        if value.get("nodes") != [ALLOWED_NODE]:
            failures.append(f"{ALLOWED_NAME} affected path changed")
        identities = _advisory_identities(value.get("via"))
        if identities is None or len(identities) != len(value["via"]):
            failures.append(f"{ALLOWED_NAME} advisory set changed")
        elif identities != ALLOWED_ADVISORIES:
            failures.append(f"{ALLOWED_NAME} advisory identity changed")

    installed, locked = _package_versions(root, failures)
    deferred = vulnerabilities.get(ALLOWED_NAME)
    vulnerable_version_present = installed == ALLOWED_VERSION or locked == ALLOWED_VERSION
    if vulnerable_version_present:
        if deferred is None:
            failures.append(f"{ALLOWED_NAME} {ALLOWED_VERSION} is installed but npm audit did not report it")
        if today >= REVIEW_DATE:
            failures.append(f"{ALLOWED_NAME} deferral expired on {REVIEW_DATE.isoformat()}")
    elif deferred is not None:
        failures.append(f"{ALLOWED_NAME} advisory reported for unsupported installed version {installed}")
    return failures


def main() -> int:
    """Run npm audit and enforce the narrow deferral."""

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["npm", "audit", "--json"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        report = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode or 1

    failures = validate_report(
        report,
        root,
        today=datetime.now(timezone.utc).date(),
    )
    if failures:
        print("\n".join(f"- {failure}" for failure in failures), file=sys.stderr)
        return 1

    vulnerabilities = report.get("vulnerabilities") or {}
    if vulnerabilities:
        print(
            f"npm audit: temporarily deferred {', '.join(ALLOWED_ADVISORIES.values())} "
            f"only at {ALLOWED_NODE}; review by {REVIEW_DATE.isoformat()}"
        )
    else:
        print("npm audit: no vulnerabilities")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
