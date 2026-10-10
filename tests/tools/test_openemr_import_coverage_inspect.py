"""Fail-closed source inspection: SQL scanning, manifests, and native backups."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from tools._shared import ToolError
from tools.openemr_import import inspect as inspect_module
from tools.openemr_import.inspect import (
    _copy_bounded,
    _inspect_sql,
    _normalized_version,
    _resolve_manifest_artifact,
    _split_sql_values,
    _SqlSecurityScanner,
    inspect_source,
)
from tools.openemr_import.models import ArchiveLimits
from tools.openemr_import.plan import TARGET_OPENEMR_VERSION

_MAJOR, _MINOR, _PATCH = TARGET_OPENEMR_VERSION.split(".")
_VERSION_ROW = f"INSERT INTO `version` VALUES ({_MAJOR},{_MINOR},{_PATCH},0,'',541,13);\n".encode()


def _sql_dump(*, version_row: bytes = _VERSION_ROW, extra: bytes = b"") -> bytes:
    return (
        b"-- MySQL dump 10.13\n"
        b"CREATE TABLE `version` (`v_major` int);\n"
        + version_row
        + b"INSERT INTO `notes` VALUES (1,'private');\n"
        + extra
    )


def _add(archive: tarfile.TarFile, name: str, content: bytes, *, kind: bytes = tarfile.REGTYPE) -> None:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.mode = 0o600
    if kind == tarfile.REGTYPE:
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    else:
        archive.addfile(info)


def _sites_archive(*, database_version: int = 541, include_version: bool = True, padding: int = 0) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        if include_version:
            _add(
                archive,
                "version.php",
                (
                    f"$v_major = '{_MAJOR}'; $v_minor = '{_MINOR}'; $v_patch = '{_PATCH}'; "
                    f"$v_database = {database_version};"
                ).encode(),
            )
        _add(archive, "sites/default/sqlconf.php", b"<?php ?>")
        _add(archive, "sites/default/documents/1/a.pdf", b"%PDF")
        _add(
            archive,
            "sites/default/documents/logs_and_misc/methods/sevena",
            b"007" + base64.b64encode(b"a" * 96),
        )
        _add(
            archive,
            "sites/default/documents/logs_and_misc/methods/sevenb",
            b"007" + base64.b64encode(b"b" * 96),
        )
        if padding:
            _add(archive, "sites/default/documents/zeros.bin", b"\0" * padding)
    return output.getvalue()


def _native_backup(path: Path, members: list[tuple[str, bytes]] | None = None, **kinds: bytes) -> Path:
    if members is None:
        members = [("openemr.sql.gz", gzip.compress(_sql_dump())), ("openemr.tar.gz", _sites_archive())]
    with tarfile.open(path, mode="w") as archive:
        for name, content in members:
            _add(archive, name, content, kind=kinds.get(name, tarfile.REGTYPE))
    return path


def _scan(
    dump: bytes, *, name: str = "openemr.sql", limits: ArchiveLimits | None = None
) -> tuple[str, int, bool, str, int]:
    return _inspect_sql(io.BytesIO(dump), name=name, compressed_bytes=len(dump), limits=limits or ArchiveLimits())


def test_scanner_masks_doubled_quotes_and_comments_split_across_chunks() -> None:
    scanner = _SqlSecurityScanner()

    pieces = [
        scanner.feed(b"SELECT 'it''s';\n#"),
        scanner.feed(b" CREATE PROCEDURE hidden\n-"),
        scanner.feed(b"- system hidden\n--"),
        scanner.feed(b" also hidden\nSELECT 1;"),
        scanner.feed(b"", final=True),
    ]
    output = b"".join(pieces)

    assert b"it" not in output
    assert b"PROCEDURE" not in output
    assert b"system" not in output
    assert b"also hidden" not in output
    assert output.endswith(b"SELECT 1;")


def test_scanner_keeps_doubled_quote_when_unmasked() -> None:
    scanner = _SqlSecurityScanner(mask_quoted=False)

    assert scanner.feed(b"'a''b'", final=True) == b"'a''b'"


def test_copy_bounded_refuses_oversized_nested_artifact() -> None:
    with pytest.raises(ToolError, match="exceeds the configured size limit"):
        _copy_bounded(io.BytesIO(b"12345"), io.BytesIO(), 4)


def test_split_sql_values_handles_escapes_and_rejects_unterminated_quotes() -> None:
    assert _split_sql_values(b"1, 'a\\'b', \"c,d\"") == (b"1", b"'a\\'b'", b'"c,d"')
    with pytest.raises(ToolError, match="unterminated quoted value"):
        _split_sql_values(b"1, 'open")


def test_version_row_with_explicit_columns_realpatch_and_tag_is_accepted() -> None:
    row = (
        b"INSERT INTO version (`v_major`, v_minor, v_patch, v_realpatch, v_tag, v_database) "
        b"VALUES (8, 1, 0, '2', '.post1', 500);\n"
    )

    database_type, total, compressed, version, schema = _scan(_sql_dump(version_row=row))

    assert (database_type, compressed, version, schema) == ("mysql", False, "8.1.0.2.post1", 500)
    assert total > 0


@pytest.mark.parametrize(
    ("row", "message"),
    (
        (b"INSERT INTO version (v_major, v_minor) VALUES (8);\n", "column/value count"),
        (b"INSERT INTO version (v_major, v_minor) VALUES (8, 2);\n", "missing required fields"),
        (b"INSERT INTO version VALUES (8,2,0,0,'',abc,1);\n", "invalid version data"),
        (b"INSERT INTO version VALUES (8,2,0,0,'rc1',541,1);\n", "not a supported stable schema"),
        (b"INSERT INTO version VALUES (8,2,0,0,'',0,1);\n", "not a supported stable schema"),
        (b"INSERT INTO version VALUES ('x);',8,2,0,'',541,1);\n", "malformed OpenEMR version row"),
    ),
    ids=("count-mismatch", "missing-fields", "invalid-data", "prerelease", "zero-schema", "quoted-terminator"),
)
def test_malformed_version_rows_fail_closed(row: bytes, message: str) -> None:
    with pytest.raises(ToolError, match=message):
        _scan(_sql_dump(version_row=row))


def test_conflicting_version_rows_fail_closed() -> None:
    other = _VERSION_ROW.replace(b",541,", b",542,")

    with pytest.raises(ToolError, match="conflicting OpenEMR version rows"):
        _scan(_sql_dump(extra=other))


def test_final_pass_rechecks_retained_identity_buffer_for_conflicts() -> None:
    # A row glued to the previous token is not matched in the streaming pass, but
    # the final pass re-scans the retained 64 KiB tail anchored at its start.
    conflicting = _VERSION_ROW.replace(b",541,", b",542,").rstrip(b"\n")
    tail = conflicting + b"\n" + b" " * (64 * 1024 - len(conflicting) - 1)
    dump = _sql_dump() + b"x" + tail

    with pytest.raises(ToolError, match="conflicting OpenEMR version rows"):
        _scan(dump)


def test_final_pass_rechecks_retained_identity_buffer_for_malformed_rows() -> None:
    malformed = b"INSERT INTO version VALUES ('x);',8,2,0,'',541,1);"
    tail = malformed + b"\n" + b" " * (64 * 1024 - len(malformed) - 1)

    with pytest.raises(ToolError, match="malformed OpenEMR version row"):
        _scan(_sql_dump() + b"x" + tail)


@pytest.mark.parametrize(
    ("statement", "message"),
    ((b"system ", "unsafe client command"), (b"CREATE PROCEDURE p", "stored executable code")),
)
def test_final_pass_rechecks_security_carry(statement: bytes, message: str) -> None:
    tail = statement + b"a" * (512 - len(statement))

    with pytest.raises(ToolError, match=message):
        _scan(_sql_dump() + b"x" + tail)


def test_sql_expanded_size_limit_is_enforced() -> None:
    with pytest.raises(ToolError, match="expanded-size limit"):
        _scan(_sql_dump(), limits=ArchiveLimits(max_expanded_bytes=10))


def test_sql_compression_ratio_limit_is_enforced() -> None:
    compressed = gzip.compress(_sql_dump(extra=b"-- " + b"a" * 100_000 + b"\n"))

    with pytest.raises(ToolError, match="SQL dump compression-ratio"):
        _inspect_sql(
            io.BytesIO(compressed),
            name="openemr.sql.gz",
            compressed_bytes=len(compressed),
            limits=ArchiveLimits(max_compression_ratio=2),
        )


def _corrupt_deflate_body(payload: bytes) -> bytes:
    """Keep the gzip header valid but make the first deflate block invalid, which raises zlib.error."""
    return payload[:10] + b"\xff" + payload[11:]


@pytest.mark.parametrize(
    "payload",
    (b"not gzip at all", gzip.compress(_sql_dump())[:30], _corrupt_deflate_body(gzip.compress(_sql_dump()))),
    ids=("bad-magic", "truncated", "corrupt-deflate-body"),
)
def test_malformed_gzip_sql_is_refused_and_handle_is_rewound(payload: bytes) -> None:
    handle = io.BytesIO(payload)

    with pytest.raises(ToolError, match="compression is malformed"):
        _inspect_sql(handle, name="openemr.sql.gz", compressed_bytes=len(payload), limits=ArchiveLimits())
    assert handle.tell() == 0


def test_binary_sql_and_unrecognized_dumps_are_refused() -> None:
    with pytest.raises(ToolError, match="binary data"):
        _scan(_sql_dump(extra=b"\x00"))
    with pytest.raises(ToolError, match="not a recognizable MySQL or MariaDB dump"):
        _scan(b"CREATE TABLE x (id int);\n" + _VERSION_ROW)


def test_mariadb_dump_is_detected() -> None:
    dump = _sql_dump().replace(b"-- MySQL dump 10.13", b"-- MariaDB dump 10.19")

    assert _scan(dump)[0] == "mariadb"


def test_version_normalization_rejects_invalid_and_prerelease_values() -> None:
    assert _normalized_version(None, None, None) is None  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="not valid semantic version data"):
        _normalized_version("abc", None, "8.2.0")
    with pytest.raises(ToolError, match="not a valid version"):
        _normalized_version(None, None, "")
    with pytest.raises(ToolError, match="Prerelease"):
        _normalized_version("8.2.0rc1", None, "8.2.0rc1")


def _write_manifest(directory: Path, manifest: object) -> None:
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _artifact(path: Path, *, sha: str | None = None) -> dict[str, str]:
    return {"path": path.name, "sha256": sha or hashlib.sha256(path.read_bytes()).hexdigest()}


def test_manifest_bundle_is_inspected_with_declared_version(tmp_path: Path) -> None:
    source = tmp_path / "bundle"
    source.mkdir()
    sql = source / "dump.sql"
    sql.write_bytes(_sql_dump())
    sites = source / "sites.tar.gz"
    sites.write_bytes(_sites_archive())
    _write_manifest(
        source,
        {
            "schema_version": 1,
            "artifacts": {"sql": _artifact(sql), "sites": _artifact(sites)},
            "source_openemr_version": TARGET_OPENEMR_VERSION,
        },
    )

    inspection = inspect_source(source)

    assert inspection.source_kind == "manifest-bundle"
    assert inspection.source_openemr_version == TARGET_OPENEMR_VERSION
    assert inspection.source_database_version == 541


@pytest.mark.parametrize(
    ("manifest", "message"),
    (
        ({"schema_version": 2}, "Unsupported import manifest schema"),
        ({"schema_version": 1, "artifacts": []}, "requires an artifacts object"),
        ({"schema_version": 1, "artifacts": {"sql": "x"}}, "entries must be objects"),
        ({"schema_version": 1, "artifacts": {"sql": {"path": 1}}}, "require path and sha256 strings"),
        (
            {"schema_version": 1, "artifacts": {"sql": {"path": "../x", "sha256": "0"}}},
            "escapes the source directory",
        ),
        (
            {"schema_version": 1, "artifacts": {"sql": {"path": "/etc/passwd", "sha256": "0"}}},
            "escapes the source directory",
        ),
        (
            {"schema_version": 1, "artifacts": {"sql": {"path": "missing.sql", "sha256": "0"}}},
            "not a regular local file",
        ),
        (
            {"schema_version": 1, "artifacts": {"sql": {"path": "dump.sql", "sha256": "0" * 64}}},
            "checksum does not match",
        ),
    ),
    ids=(
        "schema",
        "artifacts-type",
        "entry-type",
        "entry-fields",
        "parent-escape",
        "absolute",
        "missing",
        "checksum",
    ),
)
def test_manifest_validation_fails_closed(tmp_path: Path, manifest: dict[str, object], message: str) -> None:
    (tmp_path / "dump.sql").write_bytes(_sql_dump())
    _write_manifest(tmp_path, manifest)

    with pytest.raises(ToolError, match=message):
        inspect_source(tmp_path)


def test_manifest_rejects_non_string_declared_version(tmp_path: Path) -> None:
    sql = tmp_path / "dump.sql"
    sql.write_bytes(_sql_dump())
    sites = tmp_path / "sites.tar.gz"
    sites.write_bytes(_sites_archive())
    _write_manifest(
        tmp_path,
        {
            "schema_version": 1,
            "artifacts": {"sql": _artifact(sql), "sites": _artifact(sites)},
            "source_openemr_version": 8,
        },
    )

    with pytest.raises(ToolError, match="source_openemr_version must be a string"):
        inspect_source(tmp_path)


def test_manifest_must_be_utf8_json_and_not_a_symlink(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_bytes(b"\xff\xfe")
    with pytest.raises(ToolError, match="not valid UTF-8 JSON"):
        inspect_source(tmp_path)

    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    (tmp_path / "manifest.json").unlink()
    (tmp_path / "manifest.json").symlink_to(real)
    with pytest.raises(ToolError, match="manifest may not be a symlink"):
        inspect_source(tmp_path)


def test_manifest_artifact_resolving_outside_directory_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path / "outside.sql"
    outside.write_bytes(b"x")
    source = tmp_path / "source"
    source.mkdir()
    original_resolve = Path.resolve
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda self, strict=False: outside if self.name == "dump.sql" else original_resolve(self, strict),
    )

    with pytest.raises(ToolError, match="escapes the source directory"):
        _resolve_manifest_artifact(source, {"path": "dump.sql", "sha256": "0"})


def test_directory_requires_exactly_one_artifact_of_each_kind(tmp_path: Path) -> None:
    (tmp_path / "openemr.sql").write_bytes(_sql_dump())
    (tmp_path / "openemr.sql.gz").write_bytes(gzip.compress(_sql_dump()))
    (tmp_path / "openemr.tar.gz").write_bytes(_sites_archive())

    with pytest.raises(ToolError, match="exactly one openemr.sql"):
        inspect_source(tmp_path)


def test_directory_artifact_symlinks_are_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside.sql"
    outside.write_bytes(_sql_dump())
    source = tmp_path / "source"
    source.mkdir()
    (source / "openemr.sql").symlink_to(outside)
    (source / "openemr.tar.gz").write_bytes(_sites_archive())

    with pytest.raises(ToolError, match="may not be symlinks"):
        inspect_source(source)


def test_directory_artifacts_respect_compressed_size_limit(tmp_path: Path) -> None:
    (tmp_path / "openemr.sql").write_bytes(_sql_dump())
    (tmp_path / "openemr.tar.gz").write_bytes(_sites_archive())

    with pytest.raises(ToolError, match="compressed-size limit"):
        inspect_source(tmp_path, limits=ArchiveLimits(max_nested_archive_bytes=10))


def test_sites_archive_compression_ratio_is_enforced(tmp_path: Path) -> None:
    (tmp_path / "openemr.sql").write_bytes(_sql_dump())
    (tmp_path / "openemr.tar.gz").write_bytes(_sites_archive(padding=200_000))

    with pytest.raises(ToolError, match="Sites archive compression-ratio"):
        inspect_source(tmp_path, limits=ArchiveLimits(max_compression_ratio=50))


def test_schema_mismatch_between_sql_and_version_php_blocks(tmp_path: Path) -> None:
    (tmp_path / "openemr.sql").write_bytes(_sql_dump())
    (tmp_path / "openemr.tar.gz").write_bytes(_sites_archive(database_version=540))

    inspection = inspect_source(tmp_path)

    assert inspection.source_database_version is None
    assert "SQL and version.php database schema versions do not match" in inspection.unsupported_content


def test_missing_version_php_schema_and_undetected_version_are_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "openemr.sql").write_bytes(_sql_dump())
    (tmp_path / "openemr.tar.gz").write_bytes(_sites_archive(include_version=False))
    monkeypatch.setattr(inspect_module, "_normalized_version", lambda *args: None)

    inspection = inspect_source(tmp_path)

    assert inspection.source_openemr_version is None
    assert "Source OpenEMR version was not detected" in inspection.unsupported_content
    assert "version.php does not declare the OpenEMR database schema version" in inspection.unsupported_content


def test_source_symlink_and_missing_source_are_refused(tmp_path: Path) -> None:
    real = _native_backup(tmp_path / "real.tar")
    link = tmp_path / "link.tar"
    link.symlink_to(real)

    with pytest.raises(ToolError, match="may not be a symlink"):
        inspect_source(link)
    with pytest.raises(ToolError, match="does not exist"):
        inspect_source(tmp_path / "absent.tar")


def test_native_backup_size_and_format_are_checked(tmp_path: Path) -> None:
    source = _native_backup(tmp_path / "backup.tar")
    with pytest.raises(ToolError, match="source-size limit"):
        inspect_source(source, limits=ArchiveLimits(max_expanded_bytes=10))

    garbage = tmp_path / "garbage.tar"
    garbage.write_bytes(b"definitely not a tar archive" * 40)
    with pytest.raises(ToolError, match="not a supported native OpenEMR backup"):
        inspect_source(garbage)


def test_native_backup_skips_directories_and_root_entries(tmp_path: Path) -> None:
    source = tmp_path / "backup.tar"
    with tarfile.open(source, mode="w") as archive:
        _add(archive, ".", b"", kind=tarfile.DIRTYPE)
        _add(archive, "extras", b"", kind=tarfile.DIRTYPE)
        _add(archive, "openemr.sql.gz", gzip.compress(_sql_dump()))
        _add(archive, "openemr.tar.gz", _sites_archive())

    inspection = inspect_source(source)

    assert inspection.source_kind == "native-openemr-backup"
    assert inspection.unsupported_content == ()


@pytest.mark.parametrize(
    ("members", "kinds", "message"),
    (
        ([("openemr.sql.gz", b"a"), ("OPENEMR.SQL.GZ", b"b")], {}, "duplicate or case-colliding"),
        ([("evil", b"")], {"evil": tarfile.SYMTYPE}, "link or special file"),
        ([("openemr.sql", _sql_dump()), ("openemr.sql.gz", b"x")], {}, "duplicate required artifacts"),
        ([("openemr.sql", _sql_dump())], {}, "must contain openemr.sql"),
    ),
    ids=("case-collision", "symlink", "duplicate-sql", "missing-sites"),
)
def test_native_backup_structure_fails_closed(
    tmp_path: Path,
    members: list[tuple[str, bytes]],
    kinds: dict[str, bytes],
    message: str,
) -> None:
    source = _native_backup(tmp_path / "backup.tar", members, **kinds)

    with pytest.raises(ToolError, match=message):
        inspect_source(source)


def test_native_backup_unreadable_artifact_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _native_backup(tmp_path / "backup.tar")
    monkeypatch.setattr(tarfile.TarFile, "extractfile", lambda self, member: None)

    with pytest.raises(ToolError, match="could not be read"):
        inspect_source(source)
