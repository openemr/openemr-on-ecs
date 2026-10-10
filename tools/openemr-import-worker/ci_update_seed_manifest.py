#!/usr/bin/env python3
"""Regenerate or verify fresh-seed-manifest.json against a live compose database.

Runs inside the import-worker CI image. scripts/update-seed-manifest.sh
orchestrates the compose stack, image build, and manifest file update; this
module owns the live fingerprint collection and the canonical manifest format.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import worker as import_worker
from packaging.version import Version


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True), flush=True)


def _collect_tables(database: str) -> dict[str, dict[str, object]]:
    """Fingerprint every policy seed table, preserving baseline hash policy."""

    tables: dict[str, dict[str, object]] = {}
    for table in sorted(import_worker.FRESH_SEEDED_TABLES):
        baseline = import_worker.FRESH_SEED_BASELINE[table]
        count_output = import_worker._run_mysql(
            database,
            f"--execute=SELECT COUNT(*) FROM `{table}`",  # noqa: S608
        )
        entry: dict[str, object] = {"rows": int(count_output.splitlines()[-1])}
        entry["sha256"] = (
            None if baseline.get("sha256") is None else import_worker._seed_table_fingerprint(database, table)
        )
        excluded = baseline.get("exclude_columns")
        if excluded:
            entry["exclude_columns"] = excluded
        tables[table] = entry
    return tables


def _render_manifest(manifest: dict[str, object]) -> str:
    """Render the manifest in the repository's canonical compact-table format."""

    tables = manifest["tables"]
    if not isinstance(tables, dict):
        raise ValueError("manifest tables must be a mapping")
    lines = [
        "{",
        f'  "openemr_version": {json.dumps(manifest["openemr_version"])},',
        f'  "database_version": {manifest["database_version"]},',
        '  "tables": {',
    ]
    for index, table in enumerate(sorted(tables)):
        entry = tables[table]
        parts = [f'"rows": {entry["rows"]}', f'"sha256": {json.dumps(entry["sha256"])}']
        excluded = entry.get("exclude_columns")
        if excluded:
            columns = ", ".join(json.dumps(column) for column in excluded)
            parts.append(f'"exclude_columns": [{columns}]')
        comma = "," if index < len(tables) - 1 else ""
        lines.append(f"    {json.dumps(table)}: {{{', '.join(parts)}}}{comma}")
    lines.extend(["  }", "}"])
    return "\n".join(lines) + "\n"


def _generate(args: argparse.Namespace) -> int:
    database = os.environ.get("MYSQL_DATABASE", "openemr")
    version, database_version = import_worker._database_version_identity(database)
    if args.expect_version and Version(version) != Version(args.expect_version):
        _emit(
            {
                "status": "failed",
                "error": f"live database is OpenEMR {version}, expected {args.expect_version}",
            }
        )
        return 1
    manifest: dict[str, object] = {
        "openemr_version": version,
        "database_version": database_version,
        "tables": _collect_tables(database),
    }
    output = Path(args.output)
    output.write_text(_render_manifest(manifest), encoding="utf-8")
    # The manifest is a non-secret checked-in artifact; the caller runs under a
    # different uid than the container and must be able to read it back.
    os.chmod(output, 0o644)
    _emit(
        {
            "status": "passed",
            "database_version": database_version,
            "openemr_version": version,
            "tables": len(manifest["tables"]),
        }
    )
    return 0


def _verify() -> int:
    manifest = import_worker._seed_manifest
    import_worker._assert_empty_target(
        str(manifest["openemr_version"]),
        int(manifest["database_version"]),
    )
    _emit(
        {
            "status": "passed",
            "database_version": manifest["database_version"],
            "openemr_version": manifest["openemr_version"],
            "verified": True,
        }
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run manifest generation or verification."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="/work/fresh-seed-manifest.json",
        help="Path the regenerated manifest is written to (default: /work/fresh-seed-manifest.json).",
    )
    parser.add_argument(
        "--expect-version",
        default="",
        help="Fail unless the live database reports this OpenEMR version.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Validate the loaded manifest against the live database instead of regenerating it.",
    )
    args = parser.parse_args(argv)
    try:
        if args.verify:
            return _verify()
        return _generate(args)
    except (import_worker.ImportFailure, OSError, RuntimeError, ValueError) as exc:
        _emit({"status": "failed", "error": str(exc) or type(exc).__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
