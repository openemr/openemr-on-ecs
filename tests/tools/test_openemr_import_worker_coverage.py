"""Coverage for the import worker's archive, version, and SQL validation guards."""

from __future__ import annotations

import base64
import gzip
import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

_VALID_SEVEN_KEY = b"007" + base64.b64encode(b"k" * 112)
_VERSION_PHP = b"<?php $v_major='8'; $v_minor='2'; $v_patch='0'; $v_tag=''; $v_realpatch='0'; $v_database=541;"
_VERSION_ROW = b"INSERT INTO `version` VALUES (8,2,0,0,'',541,13);\n"
_VERSION_SQL = (
    b"CREATE TABLE `version` (`v_major` int, `v_minor` int, `v_patch` int, "
    b"`v_realpatch` int, `v_tag` varchar(31), `v_database` int, `v_acl` int);\n" + _VERSION_ROW
)
_WORKER_PATH = Path(__file__).resolve().parents[2] / "tools" / "openemr-import-worker" / "worker.py"


def _load_worker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("openemr_import_worker", _WORKER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def worker() -> ModuleType:
    return _load_worker()


def _add(
    archive: tarfile.TarFile,
    name: str,
    content: bytes = b"",
    *,
    kind: bytes = tarfile.REGTYPE,
    mode: int = 0o600,
    linkname: str = "",
) -> None:
    member = tarfile.TarInfo(name)
    member.type = kind
    member.mode = mode
    member.linkname = linkname
    if kind == tarfile.REGTYPE:
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    else:
        archive.addfile(member)


# Module import guards


@pytest.mark.parametrize(
    "manifest",
    ("not json", json.dumps({"no-tables": {}}), json.dumps(["tables"])),
)
def test_worker_refuses_to_load_with_invalid_seed_manifest(
    monkeypatch: pytest.MonkeyPatch,
    manifest: str,
) -> None:
    monkeypatch.setattr(Path, "read_text", lambda self, encoding=None: manifest)

    with pytest.raises(RuntimeError, match="invalid fresh-seed manifest"):
        _load_worker()


def test_worker_refuses_to_load_with_unreadable_seed_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable(self: Path, encoding: str | None = None) -> str:
        raise OSError("gone")

    monkeypatch.setattr(Path, "read_text", unreadable)

    with pytest.raises(RuntimeError, match="invalid fresh-seed manifest"):
        _load_worker()


def test_worker_refuses_seed_manifest_that_drifts_from_table_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = json.dumps({"tables": {"globals": {"rows": 1}}})
    monkeypatch.setattr(Path, "read_text", lambda self, encoding=None: manifest)

    with pytest.raises(RuntimeError, match="does not match worker table policy"):
        _load_worker()


# SQL security scanner


def test_scanner_masks_doubled_quotes_inside_quoted_strings(worker: ModuleType) -> None:
    assert worker._SqlSecurityScanner().feed(b"'it''s' x", final=True) == b" " * 7 + b" x"
    assert worker._SqlSecurityScanner(mask_quoted=False).feed(b"'it''s' x", final=True) == b"'it''s' x"


def test_scanner_holds_possible_comment_start_across_chunk_boundary(worker: ModuleType) -> None:
    scanner = worker._SqlSecurityScanner()

    assert scanner.feed(b"SELECT 1 -") == b"SELECT 1 "
    assert scanner.pending == b"-"
    assert scanner.feed(b"- system\nX", final=True) == b" " * 9 + b"\nX"


def test_scanner_holds_double_dash_until_following_whitespace_is_known(worker: ModuleType) -> None:
    scanner = worker._SqlSecurityScanner()

    assert scanner.feed(b"a--") == b"a"
    assert scanner.pending == b"--"
    assert scanner.feed(b" x\n", final=True) == b"    \n"


def test_scanner_blanks_hash_line_comments(worker: ModuleType) -> None:
    assert worker._SqlSecurityScanner().feed(b"a # hidden\nb", final=True) == b"a" + b" " * 9 + b"\nb"


# Path, checksum, and staging guards


@pytest.mark.parametrize(
    "raw_name",
    ("a\x00b", "a\\b", "", "./", "/etc/passwd", "C:/Windows", "a/../b", ".."),
)
def test_safe_path_rejects_unsafe_archive_names(worker: ModuleType, raw_name: str) -> None:
    with pytest.raises(worker.ImportFailure, match="unsafe-archive-path"):
        worker._safe_path(raw_name)


def test_safe_path_strips_repeated_current_directory_prefixes(worker: ModuleType) -> None:
    assert worker._safe_path("././sites/default/x").as_posix() == "sites/default/x"


def test_sha256_and_canonical_fingerprint_are_stable(worker: ModuleType, tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"payload")
    values = {"b": "2", "a": "1"}

    assert worker._sha256(artifact) == hashlib.sha256(b"payload").hexdigest()
    assert worker._canonical_fingerprint(values) == hashlib.sha256(b'{"a":"1","b":"2"}').hexdigest()[:32]


@pytest.mark.parametrize(
    ("owner", "key_arn", "error"),
    (
        ("", "arn:aws:kms:us-east-1:123456789012:key/k", "invalid-staging-bucket-owner"),
        ("12345678901", "arn:aws:kms:us-east-1:12345678901:key/k", "invalid-staging-bucket-owner"),
        ("123456789012", "", "invalid-staging-kms-key"),
        ("123456789012", "arn:aws:kms:us-east-1:999999999999:key/k", "invalid-staging-kms-key"),
        ("123456789012", "arn:aws:kms:us-east-1:123456789012:alias/k", "invalid-staging-kms-key"),
    ),
)
def test_staging_safeguards_require_account_bound_kms_key(
    worker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
    key_arn: str,
    error: str,
) -> None:
    monkeypatch.setenv("IMPORT_STAGING_BUCKET_OWNER", owner)
    monkeypatch.setenv("IMPORT_STAGING_KMS_KEY_ARN", key_arn)

    with pytest.raises(worker.ImportFailure, match=error):
        worker._staging_s3_safeguards()


def test_copy_limited_refuses_before_writing_oversized_chunk(worker: ModuleType) -> None:
    destination = io.BytesIO()

    with pytest.raises(worker.ImportFailure, match="artifact-size-limit"):
        worker._copy_limited(io.BytesIO(b"abcd"), destination, 3)

    assert destination.getvalue() == b""
    assert worker._copy_limited(io.BytesIO(b"abc"), destination, 3) == 3
    assert destination.getvalue() == b"abc"


# Native backup unpacking


def _native(path: Path, members: list[tuple[str, bytes, bytes]]) -> Path:
    with tarfile.open(path, mode="w") as archive:
        for name, content, kind in members:
            _add(archive, name, content, kind=kind, linkname="target" if kind == tarfile.SYMTYPE else "")
    return path


def test_native_unpack_extracts_only_top_level_required_artifacts(worker: ModuleType, tmp_path: Path) -> None:
    source = _native(
        tmp_path / "source.tar",
        [
            ("./", b"", tarfile.DIRTYPE),
            ("docs", b"", tarfile.DIRTYPE),
            ("docs/openemr.sql", b"nested-decoy", tarfile.REGTYPE),
            ("notes.txt", b"ignored", tarfile.REGTYPE),
            ("openemr.sql", b"sql-bytes", tarfile.REGTYPE),
            ("openemr.tar.gz", b"sites-bytes", tarfile.REGTYPE),
        ],
    )
    work = tmp_path / "work"
    work.mkdir()

    sql, sites, hashes = worker._unpack_native_source(source, work)

    assert sql == work / "openemr.sql"
    assert sites == work / "openemr.tar.gz"
    assert sql.read_bytes() == b"sql-bytes"
    assert sites.read_bytes() == b"sites-bytes"
    assert hashes == {
        "sql": hashlib.sha256(b"sql-bytes").hexdigest(),
        "sites": hashlib.sha256(b"sites-bytes").hexdigest(),
    }
    assert sorted(path.name for path in work.iterdir()) == ["openemr.sql", "openemr.tar.gz"]


def test_native_unpack_rejects_malformed_archive(worker: ModuleType, tmp_path: Path) -> None:
    source = tmp_path / "source.tar"
    source.write_bytes(b"definitely not a tar archive" * 40)

    with pytest.raises(worker.ImportFailure, match="malformed-native-backup"):
        worker._unpack_native_source(source, tmp_path)


@pytest.mark.parametrize(
    ("members", "error"),
    (
        ([("a.txt", b"1", tarfile.REGTYPE), ("a.txt", b"2", tarfile.REGTYPE)], "duplicate-archive-member"),
        ([("a.txt", b"1", tarfile.REGTYPE), ("A.TXT", b"2", tarfile.REGTYPE)], "duplicate-archive-member"),
        ([("link", b"", tarfile.SYMTYPE)], "special-archive-member"),
        ([("../openemr.sql", b"x", tarfile.REGTYPE)], "unsafe-archive-path"),
        (
            [("openemr.sql", b"x", tarfile.REGTYPE), ("openemr.sql.gz", b"y", tarfile.REGTYPE)],
            "duplicate-required-artifact",
        ),
        ([("openemr.sql", b"x", tarfile.REGTYPE)], "missing-required-artifact"),
        ([("openemr.tar.gz", b"x", tarfile.REGTYPE)], "missing-required-artifact"),
    ),
)
def test_native_unpack_rejects_unsafe_or_incomplete_archives(
    worker: ModuleType,
    tmp_path: Path,
    members: list[tuple[str, bytes, bytes]],
    error: str,
) -> None:
    work = tmp_path / "work"
    work.mkdir()

    with pytest.raises(worker.ImportFailure, match=error):
        worker._unpack_native_source(_native(tmp_path / "source.tar", members), work)


def test_native_unpack_enforces_member_count_limit(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker, "MAX_MEMBERS", 1)
    source = _native(
        tmp_path / "source.tar",
        [("a", b"1", tarfile.REGTYPE), ("b", b"2", tarfile.REGTYPE)],
    )

    with pytest.raises(worker.ImportFailure, match="native-backup-limit"):
        worker._unpack_native_source(source, tmp_path)


def test_native_unpack_enforces_nested_artifact_size_limit(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker, "MAX_NESTED_ARCHIVE_BYTES", 2)
    monkeypatch.setattr(worker, "CHUNK_SIZE", 1)
    source = _native(tmp_path / "source.tar", [("openemr.sql", b"abc", tarfile.REGTYPE)])
    work = tmp_path / "work"
    work.mkdir()

    with pytest.raises(worker.ImportFailure, match="artifact-size-limit"):
        worker._unpack_native_source(source, work)

    assert (work / "openemr.sql").read_bytes() == b"ab"


def test_native_unpack_rejects_unreadable_required_artifact(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _native(tmp_path / "source.tar", [("openemr.sql", b"x", tarfile.REGTYPE)])
    monkeypatch.setattr(tarfile.TarFile, "extractfile", lambda self, member: None)

    with pytest.raises(worker.ImportFailure, match="unreadable-required-artifact"):
        worker._unpack_native_source(source, tmp_path)


# Version parsing


def _version_php(**overrides: str) -> bytes:
    fields = {
        "v_major": "'8'",
        "v_minor": "'2'",
        "v_patch": "'0'",
        "v_tag": "''",
        "v_realpatch": "'0'",
        "v_database": "541",
    }
    fields.update(overrides)
    return b"<?php " + b" ".join(f"${key}={value};".encode() for key, value in fields.items() if value)


def test_parse_version_includes_realpatch_and_tag(worker: ModuleType) -> None:
    assert worker._parse_version(_version_php(v_realpatch="'3'", v_tag="'.post1'")) == ("8.2.0.3.post1", 541)


@pytest.mark.parametrize(
    ("overrides", "error"),
    (
        ({"v_database": ""}, "missing-source-version"),
        ({"v_major": ""}, "missing-source-version"),
        ({"v_tag": "'-not a version!'"}, "invalid-source-version"),
        ({"v_tag": "'-dev'"}, "prerelease-source"),
        ({"v_tag": "'rc1'"}, "prerelease-source"),
        ({"v_database": "'abc'"}, "invalid-source-database-version"),
        ({"v_database": "0"}, "invalid-source-database-version"),
    ),
)
def test_parse_version_rejects_missing_invalid_or_prerelease_sources(
    worker: ModuleType,
    overrides: dict[str, str],
    error: str,
) -> None:
    with pytest.raises(worker.ImportFailure, match=error):
        worker._parse_version(_version_php(**overrides))


def test_split_sql_values_respects_escaped_quotes(worker: ModuleType) -> None:
    assert worker._split_sql_values(b"'a\\'b,c', 2 ,\"x\"") == (b"'a\\'b,c'", b"2", b'"x"')


def test_split_sql_values_rejects_unterminated_quote(worker: ModuleType) -> None:
    with pytest.raises(worker.ImportFailure, match="invalid-sql-version-row"):
        worker._split_sql_values(b"8,'unterminated")


def test_sql_scalar_unescapes_and_rejects_invalid_utf8(worker: ModuleType) -> None:
    assert worker._sql_scalar(b" 'it\\'s' ") == "it's"
    assert worker._sql_scalar(b"541") == "541"
    with pytest.raises(worker.ImportFailure, match="invalid-sql-version-row"):
        worker._sql_scalar(b"'\xff'")


def _identity(worker: ModuleType, statement: bytes) -> tuple[str, int]:
    match = worker.SQL_VERSION_INSERT.search(statement)
    assert match is not None
    return worker._sql_version_identity(match)


def test_sql_version_identity_honors_explicit_column_order(worker: ModuleType) -> None:
    statement = (
        b"INSERT INTO version (`v_database`, v_major, `V_MINOR`, v_patch, v_realpatch, v_tag) "
        b"VALUES (541, 8, 2, 0, 3, '.post1');"
    )

    assert _identity(worker, statement) == ("8.2.0.3.post1", 541)


@pytest.mark.parametrize(
    "statement",
    (
        b"INSERT INTO version (v_m\xe9jor) VALUES (8);",
        b"INSERT INTO version VALUES (8,2,0);",
        b"INSERT INTO version (v_major, v_minor) VALUES (8, 2);",
        b"INSERT INTO version VALUES (8,2,0,0,'',abc,13);",
        b"INSERT INTO version VALUES (8,2,0,0,'!!',541,13);",
        b"INSERT INTO version VALUES (8,2,0,0,'rc1',541,13);",
        b"INSERT INTO version VALUES (8,2,0,0,'',0,13);",
    ),
)
def test_sql_version_identity_rejects_malformed_rows(worker: ModuleType, statement: bytes) -> None:
    with pytest.raises(worker.ImportFailure, match="invalid-sql-version-row"):
        _identity(worker, statement)


# Site archive validation


def _site_archive(
    path: Path,
    *,
    include_version: bool = True,
    include_sqlconf: bool = True,
    version_php: bytes = _VERSION_PHP,
    extra: tuple[tuple[str, bytes, bytes], ...] = (),
) -> Path:
    with tarfile.open(path, mode="w:gz") as archive:
        if include_version:
            _add(archive, "version.php", version_php)
        if include_sqlconf:
            _add(archive, "sites/default/sqlconf.php", b"source-credential")
        _add(archive, "sites/default/documents/patient.pdf", b"patient")
        _add(archive, "sites/default/documents/logs_and_misc/methods/sevena", _VALID_SEVEN_KEY)
        _add(archive, "sites/default/documents/logs_and_misc/methods/sevenb", _VALID_SEVEN_KEY)
        for name, content, kind in extra:
            _add(archive, name, content, kind=kind, linkname="/etc/passwd" if kind == tarfile.SYMTYPE else "")
    return path


@pytest.fixture
def destination(tmp_path: Path) -> Path:
    path = tmp_path / "import" / "default"
    path.mkdir(parents=True)
    return path


def test_site_extraction_creates_directories_and_skips_target_only_files(
    worker: ModuleType,
    tmp_path: Path,
    destination: Path,
) -> None:
    archive = _site_archive(
        tmp_path / "sites.tar.gz",
        extra=(
            ("./", b"", tarfile.DIRTYPE),
            ("sites/default", b"", tarfile.DIRTYPE),
            ("sites/default/images", b"", tarfile.DIRTYPE),
            ("sites/default/documents/.htaccess", b"Deny from all", tarfile.REGTYPE),
            ("sites/default/documents/Web.Config", b"<configuration/>", tarfile.REGTYPE),
        ),
    )

    assert worker._validate_and_extract_sites(archive, destination) == ("8.2.0", 541)

    assert (destination / "images").is_dir()
    assert (destination / "images").stat().st_mode & 0o777 == 0o750
    assert (destination / "documents" / "patient.pdf").stat().st_mode & 0o777 == 0o640
    assert not (destination / "documents" / ".htaccess").exists()
    assert not (destination / "documents" / "Web.Config").exists()


def test_site_extraction_rejects_malformed_archive(worker: ModuleType, tmp_path: Path, destination: Path) -> None:
    archive = tmp_path / "sites.tar.gz"
    archive.write_bytes(b"\x1f\x8bnot really gzip")

    with pytest.raises(worker.ImportFailure, match="malformed-sites-archive"):
        worker._validate_and_extract_sites(archive, destination)


@pytest.mark.parametrize(
    ("extra", "error"),
    (
        (
            (("sites/default/documents/PATIENT.pdf", b"dup", tarfile.REGTYPE),),
            "duplicate-archive-member",
        ),
        ((("sites/default/documents/link", b"", tarfile.SYMTYPE),), "special-archive-member"),
        ((("sites/default/documents/fifo", b"", tarfile.FIFOTYPE),), "special-archive-member"),
    ),
)
def test_site_extraction_rejects_duplicate_and_special_members(
    worker: ModuleType,
    tmp_path: Path,
    destination: Path,
    extra: tuple[tuple[str, bytes, bytes], ...],
    error: str,
) -> None:
    with pytest.raises(worker.ImportFailure, match=error):
        worker._validate_and_extract_sites(_site_archive(tmp_path / "sites.tar.gz", extra=extra), destination)


def test_site_extraction_enforces_member_limit(
    worker: ModuleType,
    tmp_path: Path,
    destination: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker, "MAX_MEMBERS", 2)

    with pytest.raises(worker.ImportFailure, match="sites-archive-limit"):
        worker._validate_and_extract_sites(_site_archive(tmp_path / "sites.tar.gz"), destination)


def test_site_extraction_rejects_oversized_version_file(
    worker: ModuleType,
    tmp_path: Path,
    destination: Path,
) -> None:
    archive = _site_archive(tmp_path / "sites.tar.gz", version_php=_VERSION_PHP + b" " * (64 * 1024))

    with pytest.raises(worker.ImportFailure, match="invalid-source-version"):
        worker._validate_and_extract_sites(archive, destination)


def test_site_extraction_rejects_unreadable_version_file(
    worker: ModuleType,
    tmp_path: Path,
    destination: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _site_archive(tmp_path / "sites.tar.gz")
    monkeypatch.setattr(tarfile.TarFile, "extractfile", lambda self, member: None)

    with pytest.raises(worker.ImportFailure, match="invalid-source-version"):
        worker._validate_and_extract_sites(archive, destination)


def _patch_member_reader(monkeypatch: pytest.MonkeyPatch, name: str, replacement: io.BytesIO | None) -> None:
    original = tarfile.TarFile.extractfile

    def extractfile(self: tarfile.TarFile, member: tarfile.TarInfo) -> io.BytesIO | None:
        if member.name == name:
            return replacement
        return original(self, member)

    monkeypatch.setattr(tarfile.TarFile, "extractfile", extractfile)


def test_site_extraction_rejects_unreadable_data_member(
    worker: ModuleType,
    tmp_path: Path,
    destination: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _site_archive(tmp_path / "sites.tar.gz")
    _patch_member_reader(monkeypatch, "sites/default/documents/patient.pdf", None)

    with pytest.raises(worker.ImportFailure, match="unreadable-sites-member"):
        worker._validate_and_extract_sites(archive, destination)


def test_site_extraction_rejects_truncated_data_member(
    worker: ModuleType,
    tmp_path: Path,
    destination: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _site_archive(tmp_path / "sites.tar.gz")
    _patch_member_reader(monkeypatch, "sites/default/documents/patient.pdf", io.BytesIO(b"pat"))

    with pytest.raises(worker.ImportFailure, match="sites-member-size-mismatch"):
        worker._validate_and_extract_sites(archive, destination)


def test_site_extraction_refuses_to_follow_symlinks_out_of_destination(
    worker: ModuleType,
    tmp_path: Path,
    destination: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (destination / "documents").symlink_to(outside, target_is_directory=True)

    with pytest.raises(worker.ImportFailure, match="unsafe-archive-path"):
        worker._validate_and_extract_sites(_site_archive(tmp_path / "sites.tar.gz"), destination)

    assert list(outside.iterdir()) == []


def test_site_extraction_enforces_compression_ratio(
    worker: ModuleType,
    tmp_path: Path,
    destination: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker, "MAX_COMPRESSION_RATIO", 0)

    with pytest.raises(worker.ImportFailure, match="sites-compression-ratio"):
        worker._validate_and_extract_sites(_site_archive(tmp_path / "sites.tar.gz"), destination)


@pytest.mark.parametrize(
    ("options", "error"),
    (
        ({"include_version": False}, "missing-source-version"),
        ({"include_sqlconf": False}, "incomplete-default-site"),
    ),
)
def test_site_extraction_requires_version_and_complete_default_site(
    worker: ModuleType,
    tmp_path: Path,
    destination: Path,
    options: dict[str, bool],
    error: str,
) -> None:
    with pytest.raises(worker.ImportFailure, match=error):
        worker._validate_and_extract_sites(_site_archive(tmp_path / "sites.tar.gz", **options), destination)


@pytest.mark.parametrize(
    ("content", "error"),
    (
        (b"007!!!not-base64!!!", "invalid-document-encryption-keys"),
        (b"007" + base64.b64encode(b"k" * 95), "invalid-document-encryption-keys"),
        (b"x" * 4097, "missing-document-encryption-keys"),
    ),
)
def test_drive_key_validation_rejects_invalid_key_material(
    worker: ModuleType,
    destination: Path,
    content: bytes,
    error: str,
) -> None:
    methods = destination / "documents" / "logs_and_misc" / "methods"
    methods.mkdir(parents=True)
    (methods / "sevena").write_bytes(content)
    (methods / "sevenb").write_bytes(_VALID_SEVEN_KEY)

    with pytest.raises(worker.ImportFailure, match=error):
        worker._validate_drive_key_files(destination)


def test_drive_key_validation_rejects_symlinked_keys(worker: ModuleType, tmp_path: Path, destination: Path) -> None:
    methods = destination / "documents" / "logs_and_misc" / "methods"
    methods.mkdir(parents=True)
    real_key = tmp_path / "real-key"
    real_key.write_bytes(_VALID_SEVEN_KEY)
    (methods / "sevena").symlink_to(real_key)
    (methods / "sevenb").write_bytes(_VALID_SEVEN_KEY)

    with pytest.raises(worker.ImportFailure, match="missing-document-encryption-keys"):
        worker._validate_drive_key_files(destination)


# SQL dump validation


def test_sql_validation_enforces_expanded_size_limit(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker, "MAX_EXPANDED_BYTES", 10)
    source = tmp_path / "openemr.sql"
    source.write_bytes(b"-- MySQL dump\n" + _VERSION_SQL)

    with pytest.raises(worker.ImportFailure, match="sql-size-limit"):
        worker._validate_sql(source, tmp_path / "validated.sql")


def test_sql_validation_rejects_version_row_whose_quoted_value_hides_terminator(
    worker: ModuleType,
    tmp_path: Path,
) -> None:
    source = tmp_path / "openemr.sql"
    source.write_bytes(b"-- MySQL dump\nINSERT INTO `version` VALUES ('x);',2,0,0,'',541,13);\n")

    with pytest.raises(worker.ImportFailure, match="malformed-sql-version-row"):
        worker._validate_sql(source, tmp_path / "validated.sql")


def test_sql_validation_rejects_conflicting_version_rows(worker: ModuleType, tmp_path: Path) -> None:
    source = tmp_path / "openemr.sql"
    source.write_bytes(b"-- MySQL dump\n" + _VERSION_SQL + b"INSERT INTO `version` VALUES (8,2,1,0,'',541,13);\n")

    with pytest.raises(worker.ImportFailure, match="conflicting-sql-version-rows"):
        worker._validate_sql(source, tmp_path / "validated.sql")


@pytest.mark.parametrize(
    "payload",
    (b"not gzip at all", b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff\xff" + b"\x00" * 32),
    ids=("bad-magic", "corrupt-deflate-body"),
)
def test_sql_validation_rejects_corrupt_gzip(worker: ModuleType, tmp_path: Path, payload: bytes) -> None:
    source = tmp_path / "openemr.sql.gz"
    source.write_bytes(payload)

    with pytest.raises(worker.ImportFailure, match="malformed-sql-artifact"):
        worker._validate_sql(source, tmp_path / "validated.sql")


def test_sql_validation_enforces_gzip_compression_ratio(
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker, "MAX_COMPRESSION_RATIO", 0)
    source = tmp_path / "openemr.sql.gz"
    source.write_bytes(gzip.compress(b"-- MySQL dump\n" + _VERSION_SQL))

    with pytest.raises(worker.ImportFailure, match="sql-compression-ratio"):
        worker._validate_sql(source, tmp_path / "validated.sql")


def test_sql_validation_detects_client_command_completed_by_final_flush(
    worker: ModuleType,
    tmp_path: Path,
) -> None:
    source = tmp_path / "openemr.sql"
    source.write_bytes(b"-- MySQL dump\n" + _VERSION_SQL + b"system#")

    with pytest.raises(worker.ImportFailure, match="unsafe-sql-client-command"):
        worker._validate_sql(source, tmp_path / "validated.sql")


def test_sql_validation_final_scan_rechecks_stored_code_at_carry_boundary(
    worker: ModuleType,
    tmp_path: Path,
) -> None:
    statement = b"CREATE PROCEDURE p() SELECT 1;"
    tail = statement + b" " * (511 - len(statement)) + b"\n"
    assert len(tail) == 512
    source = tmp_path / "openemr.sql"
    source.write_bytes(b"-- MySQL dump\n" + _VERSION_SQL + b"x" + tail)

    with pytest.raises(worker.ImportFailure, match="unsupported-sql-stored-code"):
        worker._validate_sql(source, tmp_path / "validated.sql")


def _window_edge_dump(statement: bytes) -> bytes:
    window = 64 * 1024
    tail = statement + b" " * (window - len(statement) - 1) + b"\n"
    assert len(tail) == window
    return b"-- MySQL dump\n" + _VERSION_SQL + b"x" + tail


@pytest.mark.parametrize(
    ("statement", "error"),
    (
        (b"INSERT INTO `version` VALUES (8,2,1,0,'',541,13);", "conflicting-sql-version-rows"),
        (b"INSERT INTO `version` VALUES ('x);',2,0,0,'',541,13);", "malformed-sql-version-row"),
    ),
)
def test_sql_validation_final_scan_rechecks_version_rows_at_window_edge(
    worker: ModuleType,
    tmp_path: Path,
    statement: bytes,
    error: str,
) -> None:
    source = tmp_path / "openemr.sql"
    source.write_bytes(_window_edge_dump(statement))

    with pytest.raises(worker.ImportFailure, match=error):
        worker._validate_sql(source, tmp_path / "validated.sql")


@pytest.mark.parametrize(
    "content",
    (
        _VERSION_SQL,
        b"-- MySQL dump\n\x00" + _VERSION_SQL,
        b"-- MariaDB dump\nSELECT 1;\n",
    ),
)
def test_sql_validation_rejects_unrecognized_dumps(worker: ModuleType, tmp_path: Path, content: bytes) -> None:
    source = tmp_path / "openemr.sql"
    source.write_bytes(content)

    with pytest.raises(worker.ImportFailure, match="unrecognized-sql-dump"):
        worker._validate_sql(source, tmp_path / "validated.sql")
