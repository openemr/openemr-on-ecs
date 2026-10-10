"""Build ephemeral native-backup fixtures from a live OpenEMR TLS compose stack."""

from __future__ import annotations

import argparse
import base64
import gzip
import io
import json
import os
import re
import shutil
import tarfile
from pathlib import Path

import worker as import_worker

_VALID_SEVEN_KEY = b"007" + base64.b64encode(b"k" * 112)
_EXPECTED_OPENEMR_VERSION = "8.4.1"
_EXPECTED_DATABASE_VERSION = 543
_VERSION_PHP = b"<?php $v_major='8'; $v_minor='4'; $v_patch='1'; $v_tag=''; $v_realpatch='0'; $v_database='543';\n"
_MARKER = b"ci-import-marker-document"


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True), flush=True)


def _add(archive: tarfile.TarFile, name: str, content: bytes, *, mode: int = 0o600) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mode = mode
    archive.addfile(member, io.BytesIO(content))


def _prune_fresh_sites(raw_sites: Path, destination: Path) -> None:
    """Copy only paths allowed by worker._assert_fresh_efs_target."""

    source_default = raw_sites / "default"
    if not source_default.is_dir():
        raise RuntimeError("raw sites are missing default/")
    target_default = destination / "default"
    if destination.exists():
        shutil.rmtree(destination)
    target_default.mkdir(parents=True, mode=0o755)

    sqlconf = source_default / "sqlconf.php"
    if not sqlconf.is_file():
        raise RuntimeError("raw sites are missing sqlconf.php")
    shutil.copy2(sqlconf, target_default / "sqlconf.php")

    mysql_ca = source_default / "documents" / "certificates" / "mysql-ca"
    if mysql_ca.is_file():
        ca_dest = target_default / "documents" / "certificates" / "mysql-ca"
        ca_dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(mysql_ca, ca_dest)

    methods_src = source_default / "documents" / "logs_and_misc" / "methods"
    methods_dest = target_default / "documents" / "logs_and_misc" / "methods"
    methods_dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    key_name = re.compile(r"(?:one|two|three|four|five|six|seven)(?:a|b)?")
    if methods_src.is_dir():
        for path in methods_src.iterdir():
            if path.is_file() and key_name.fullmatch(path.name):
                shutil.copy2(path, methods_dest / path.name)
    # Guarantee worker-accepted drive-key framing regardless of upstream encoding.
    for name in ("sevena", "sevenb"):
        (methods_dest / name).write_bytes(_VALID_SEVEN_KEY)
        (methods_dest / name).chmod(0o600)

    for filename in import_worker.TARGET_ONLY_FILENAMES:
        candidate = source_default / filename
        if candidate.is_file():
            shutil.copy2(candidate, target_default / filename)

    documents = target_default / "documents"
    documents.mkdir(parents=True, exist_ok=True, mode=0o755)


def _dump_database(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    import_worker._dump_target_database(output)
    if output.stat().st_size < 1024:
        raise RuntimeError("database dump is unexpectedly small")


def _force_fresh_clinical_state() -> None:
    """Align a compose-initialized DB with the worker fresh-target emptiness policy."""

    database = os.environ.get("MYSQL_DATABASE", "openemr")
    tables = set(
        line.strip()
        for line in import_worker._run_mysql(database, "--execute=SHOW TABLES").splitlines()
        if line.strip()
    )
    zero_row_tables = (
        "ar_activity",
        "billing",
        "claims",
        "documents",
        "form_encounter",
        "immunizations",
        "insurance_data",
        "issue_encounter",
        "lists",
        "openemr_postcalendar_events",
        "patient_data",
        "payments",
        "prescriptions",
        "procedure_order",
        "transactions",
    )
    for table in zero_row_tables:
        if table in tables:
            # Table names come from the fixed allowlist above.
            import_worker._run_mysql(database, f"--execute=DELETE FROM `{table}`")  # noqa: S608


def _insert_synthetic_patient() -> None:
    database = os.environ.get("MYSQL_DATABASE", "openemr")
    # Keep values synthetic and non-PHI. Columns match OpenEMR 8.2 patient_data.
    statements = (
        "INSERT INTO `patient_data` "
        "(`pid`, `pubpid`, `fname`, `lname`, `DOB`, `sex`, `status`, `date`) "
        "VALUES (900001, 'ci-import-1', 'Ci', 'Import', '1990-01-01', 'Female', '', NOW())",
        # Fallback if the image's schema omits `date` or uses different nullability.
        "INSERT INTO `patient_data` "
        "(`pid`, `pubpid`, `fname`, `lname`, `DOB`, `sex`, `status`) "
        "VALUES (900001, 'ci-import-1', 'Ci', 'Import', '1990-01-01', 'Female', '')",
    )
    last_error: Exception | None = None
    for statement in statements:
        try:
            import_worker._run_mysql(database, f"--execute={statement}")
            return
        except import_worker.ImportFailure as exc:
            last_error = exc
    raise RuntimeError("failed to insert synthetic patient") from last_error


def _build_sites_archive(path: Path) -> None:
    with tarfile.open(path, mode="w:gz") as archive:
        _add(archive, "version.php", _VERSION_PHP)
        _add(archive, "sites/default/sqlconf.php", b"source-credential-must-not-land")
        _add(archive, "sites/default/documents/ci-import-marker.txt", _MARKER)
        _add(
            archive,
            "sites/default/documents/logs_and_misc/methods/sevena",
            _VALID_SEVEN_KEY,
        )
        _add(
            archive,
            "sites/default/documents/logs_and_misc/methods/sevenb",
            _VALID_SEVEN_KEY,
        )


def _build_source_tar(*, sql_dump: Path, sites_archive: Path, output: Path) -> None:
    compressed = gzip.compress(sql_dump.read_bytes(), compresslevel=1)
    with tarfile.open(output, mode="w") as archive:
        _add(archive, "openemr.sql.gz", compressed)
        _add(archive, "openemr.tar.gz", sites_archive.read_bytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare CI import-worker MySQL fixtures")
    parser.add_argument("--raw-sites", type=Path, required=True)
    parser.add_argument("--fixtures-dir", type=Path, required=True)
    parser.add_argument("--sites-mount", type=Path, required=True)
    args = parser.parse_args(argv)

    required = (
        "MYSQL_HOST",
        "MYSQL_PORT",
        "MYSQL_USERNAME",
        "MYSQL_PASSWORD",
        "MYSQL_SSL_CA",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        _emit({"status": "failed", "error": f"missing-env:{','.join(missing)}"})
        return 1

    fixtures = args.fixtures_dir.resolve()
    fixtures.mkdir(parents=True, exist_ok=True)
    work = fixtures / ".prepare-work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, mode=0o700)

    try:
        tables = import_worker._run_mysql(
            os.environ.get("MYSQL_DATABASE", "openemr"),
            "--execute=SHOW TABLES",
        )
        table_names = {line.strip() for line in tables.splitlines() if line.strip()}
        required_tables = {"documents", "form_encounter", "patient_data", "users"}
        if not required_tables.issubset(table_names):
            raise RuntimeError("openemr schema is not initialized")
        if len(table_names) < 50:
            raise RuntimeError("openemr schema has fewer than 50 tables")

        _force_fresh_clinical_state()
        import_worker._assert_empty_target(
            _EXPECTED_OPENEMR_VERSION,
            _EXPECTED_DATABASE_VERSION,
        )

        fresh_sites = fixtures / "fresh-sites"
        _prune_fresh_sites(args.raw_sites.resolve(), fresh_sites)

        empty_baseline = fixtures / "empty-baseline.sql"
        _dump_database(empty_baseline)

        _insert_synthetic_patient()
        if (
            int(
                import_worker._run_mysql(
                    os.environ.get("MYSQL_DATABASE", "openemr"),
                    "--execute=SELECT COUNT(*) FROM `patient_data`",
                ).splitlines()[-1]
            )
            < 1
        ):
            raise RuntimeError("failed to insert synthetic patient")

        source_sql = work / "source.sql"
        _dump_database(source_sql)
        # Ensure dump header is recognized even if tooling wording differs.
        text = source_sql.read_bytes()
        if b"mysql dump" not in text.lower() and b"mariadb dump" not in text.lower():
            source_sql.write_bytes(b"-- MariaDB dump\n" + text)

        sites_archive = work / "openemr.tar.gz"
        _build_sites_archive(sites_archive)
        source_tar = fixtures / "source.tar"
        _build_source_tar(sql_dump=source_sql, sites_archive=sites_archive, output=source_tar)

        # Restore empty target DB for the harness.
        import_worker._restore_baseline_database(empty_baseline)
        import_worker._assert_empty_target(
            _EXPECTED_OPENEMR_VERSION,
            _EXPECTED_DATABASE_VERSION,
        )

        sites_mount = args.sites_mount.resolve()
        # sites_mount is a Docker bind-mount point — never rmtree the mount itself.
        sites_mount.mkdir(parents=True, exist_ok=True)
        for child in list(sites_mount.iterdir()):
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        shutil.copytree(fresh_sites, sites_mount, dirs_exist_ok=True)
        # Ensure importer uid can mutate the mount inside the worker container.
        for path in [sites_mount, *sites_mount.rglob("*")]:
            try:
                os.chmod(path, 0o755 if path.is_dir() else 0o644)
            except OSError:
                pass

        _emit(
            {
                "status": "passed",
                "tables": len(table_names),
                "source_tar": str(source_tar),
                "sites_mount": str(sites_mount),
            }
        )
        return 0
    except Exception as exc:
        _emit({"status": "failed", "error": f"{type(exc).__name__}:{exc}"})
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
