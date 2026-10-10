"""Offline source adapters and full pre-mutation import inspection."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import tarfile
import tempfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterator, cast

from packaging.version import InvalidVersion, Version

from tools._shared import ToolError, fingerprint

from .archive import _safe_member_name, scan_sites_archive
from .models import SCHEMA_VERSION, ArchiveLimits, SourceInspection

_SQL_NAMES = ("openemr.sql.gz", "openemr.sql")
# Execution intentionally supports only OpenEMR's canonical native-backup member.
# Keeping inspection aligned prevents a plan from passing offline and then failing
# only after the target service has been stopped.
_SITE_NAMES = ("openemr.tar.gz",)
_SQL_VERSION_INSERT = re.compile(
    rb"(?:^|\n)[ \t]*INSERT\s+INTO\s+(?:`version`|version)\s*"
    rb"(?:\((?P<columns>[^)]{1,2048})\))?\s*"
    rb"VALUES\s*\((?P<values>.{1,8192}?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_DEFAULT_VERSION_COLUMNS = (
    "v_major",
    "v_minor",
    "v_patch",
    "v_realpatch",
    "v_tag",
    "v_database",
    "v_acl",
)
_SQL_IDENTITY_BUFFER_BYTES = 64 * 1024
_SQL_CLIENT_COMMAND_PATTERN = re.compile(
    rb"(?:^|[\n;])[ \t]*(?:\\[!.ePrRtT]|(?:edit|pager|prompt|source|system|tee)(?=[ \t\r\n;]))",
    re.IGNORECASE,
)


class _SqlSecurityScanner:
    """Remove SQL comments and quoted content across chunk boundaries."""

    def __init__(
        self,
        *,
        mask_quoted: bool = True,
        mask_backticks: bool | None = None,
    ) -> None:
        self.state = "normal"
        self.escaped = False
        self.executable_comment = False
        self.mask_quoted = mask_quoted
        self.mask_backticks = mask_quoted if mask_backticks is None else mask_backticks
        self.pending = b""

    def feed(self, chunk: bytes, *, final: bool = False) -> bytes:
        data = self.pending + chunk
        self.pending = b""
        output = bytearray()
        index = 0
        while index < len(data):
            character = data[index]
            following = data[index + 1] if index + 1 < len(data) else None
            third = data[index + 2] if index + 2 < len(data) else None
            if self.state == "line-comment":
                if character in {10, 13}:
                    self.state = "normal"
                    output.append(character)
                else:
                    output.append(32)
                index += 1
                continue
            if self.state == "block-comment":
                if character == 42 and following == 47:
                    output.extend(b"  ")
                    self.state = "normal"
                    index += 2
                else:
                    output.append(character if character in {10, 13} else 32)
                    index += 1
                continue
            if self.state in {"single-quote", "double-quote", "backtick"}:
                quote = {
                    "single-quote": 39,
                    "double-quote": 34,
                    "backtick": 96,
                }[self.state]
                mask_current = self.mask_backticks if self.state == "backtick" else self.mask_quoted
                output.append(character if not mask_current or character in {10, 13} else 32)
                if self.escaped:
                    self.escaped = False
                elif character == 92:
                    self.escaped = True
                elif character == quote:
                    if following == quote:
                        output.append(following if not mask_current else 32)
                        index += 1
                    else:
                        self.state = "normal"
                index += 1
                continue
            if (
                not final
                and following is None
                and character in ({35, 42, 45, 47} if self.executable_comment else {35, 45, 47})
            ):
                self.pending = data[index:]
                break
            if not final and third is None and character == 45 and following == 45:
                self.pending = data[index:]
                break
            if character == 35:
                self.state = "line-comment"
                output.append(32)
                index += 1
                continue
            if self.executable_comment and character == 42 and following == 47:
                self.executable_comment = False
                output.extend(b"  ")
                index += 2
                continue
            if character == 45 and following == 45 and third is not None and chr(third).isspace():
                self.state = "line-comment"
                output.extend(b"  ")
                index += 2
                continue
            if character == 47 and following == 42 and third == 33:
                self.executable_comment = True
                output.extend(b"   ")
                index += 3
                while index < len(data) and 48 <= data[index] <= 57:
                    output.append(32)
                    index += 1
                continue
            if character == 47 and following == 42:
                self.state = "block-comment"
                output.extend(b"  ")
                index += 2
                continue
            if character in {39, 34, 96}:
                self.state = {
                    39: "single-quote",
                    34: "double-quote",
                    96: "backtick",
                }[character]
                mask_current = self.mask_backticks if character == 96 else self.mask_quoted
                output.append(character if not mask_current else 32)
                index += 1
                continue
            output.append(character)
            index += 1
        return bytes(output)


_SQL_STORED_CODE_PATTERN = re.compile(
    rb"(?:\bDEFINER\s*=|"
    rb"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:PROCEDURE|FUNCTION|TRIGGER|EVENT)\b|"
    rb"\bALTER\s+(?:PROCEDURE|FUNCTION|EVENT)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Artifacts:
    source_kind: str
    source_bytes: int
    source_sha256: str
    sql: IO[bytes]
    sql_name: str
    sql_bytes: int
    sql_sha256: str
    sites: IO[bytes]
    sites_name: str
    sites_bytes: int
    sites_sha256: str
    manifest_version: str | None = None
    extra_member_count: int = 0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_bounded(source: IO[bytes], destination: IO[bytes], limit: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ToolError("Nested backup artifact exceeds the configured size limit")
        digest.update(chunk)
        destination.write(chunk)
    destination.seek(0)
    return total, digest.hexdigest()


def _split_sql_values(raw: bytes) -> tuple[bytes, ...]:
    values: list[bytes] = []
    current = bytearray()
    quote: int | None = None
    escaped = False
    for character in raw:
        if quote is not None:
            current.append(character)
            if escaped:
                escaped = False
            elif character == ord("\\"):
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {ord("'"), ord('"')}:
            quote = character
            current.append(character)
        elif character == ord(","):
            values.append(bytes(current).strip())
            current.clear()
        else:
            current.append(character)
    if quote is not None:
        raise ToolError("SQL version row contains an unterminated quoted value")
    values.append(bytes(current).strip())
    return tuple(values)


def _sql_scalar(raw: bytes) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[:1] == value[-1:] and value[:1] in {b"'", b'"'}:
        value = value[1:-1]
        value = value.replace(b"\\'", b"'").replace(b'\\"', b'"').replace(b"\\\\", b"\\")
    return value.decode("utf-8", errors="strict")


def _sql_version_identity(match: re.Match[bytes]) -> tuple[str, int]:
    raw_columns = match.group("columns")
    columns: tuple[str, ...]
    if raw_columns is None:
        columns = _DEFAULT_VERSION_COLUMNS
    else:
        columns = tuple(
            value.decode("ascii", errors="strict").strip().strip("`").lower() for value in raw_columns.split(b",")
        )
    values = _split_sql_values(match.group("values"))
    if len(columns) != len(values):
        raise ToolError("SQL version row column/value count does not match")
    row = {column: _sql_scalar(value) for column, value in zip(columns, values)}
    required = {"v_major", "v_minor", "v_patch", "v_database"}
    if not required.issubset(row):
        raise ToolError("SQL dump version row is missing required fields")
    version = f"{row['v_major']}.{row['v_minor']}.{row['v_patch']}"
    realpatch = row.get("v_realpatch", "")
    if realpatch and realpatch != "0":
        version = f"{version}.{realpatch}"
    tag = row.get("v_tag", "")
    if tag:
        version = f"{version}{tag}"
    try:
        parsed = Version(version)
        database_version = int(row["v_database"])
    except (InvalidVersion, ValueError) as exc:
        raise ToolError("SQL dump version row contains invalid version data") from exc
    if parsed.is_prerelease or parsed.is_devrelease or database_version < 1:
        raise ToolError("SQL dump version row is not a supported stable schema")
    return str(parsed), database_version


def _resolve_manifest_artifact(directory: Path, value: object) -> tuple[Path, str]:
    if not isinstance(value, dict):
        raise ToolError("Import manifest artifact entries must be objects")
    requested = value.get("path")
    expected_sha = value.get("sha256")
    if not isinstance(requested, str) or not isinstance(expected_sha, str):
        raise ToolError("Import manifest artifacts require path and sha256 strings")
    relative = Path(requested)
    if relative.is_absolute() or ".." in relative.parts:
        raise ToolError("Import manifest artifact path escapes the source directory")
    candidate = directory
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            raise ToolError("Import manifest artifacts may not be symlinks")
    path = candidate.resolve()
    try:
        path.relative_to(directory.resolve())
    except ValueError as exc:
        raise ToolError("Import manifest artifact path escapes the source directory") from exc
    if path.is_symlink() or not path.is_file():
        raise ToolError("Import manifest artifact is not a regular local file")
    actual_sha = _sha256_file(path)
    if actual_sha.lower() != expected_sha.lower():
        raise ToolError("Import manifest artifact checksum does not match")
    return path, actual_sha


def _directory_artifact_paths(
    directory: Path,
) -> tuple[str, Path, str, Path, str | None]:
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink():
        raise ToolError("Import manifest may not be a symlink")
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ToolError("Import manifest is not valid UTF-8 JSON") from exc
        if manifest.get("schema_version") != 1:
            raise ToolError("Unsupported import manifest schema version")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ToolError("Import manifest requires an artifacts object")
        sql, _ = _resolve_manifest_artifact(directory, artifacts.get("sql"))
        sites, _ = _resolve_manifest_artifact(directory, artifacts.get("sites"))
        source_version = manifest.get("source_openemr_version")
        if source_version is not None and not isinstance(source_version, str):
            raise ToolError("Manifest source_openemr_version must be a string")
        return "manifest-bundle", sql, sites.name, sites, source_version

    sql_candidates = [directory / name for name in _SQL_NAMES if (directory / name).is_file()]
    site_candidates = [directory / name for name in _SITE_NAMES if (directory / name).is_file()]
    if len(sql_candidates) != 1 or len(site_candidates) != 1:
        raise ToolError("Directory sources require exactly one openemr.sql[.gz] and one supported sites archive")
    sql = sql_candidates[0]
    sites = site_candidates[0]
    if sql.is_symlink() or sites.is_symlink():
        raise ToolError("Import source artifacts may not be symlinks")
    return "sql-and-sites", sql, sites.name, sites, None


@contextmanager
def _from_directory(
    directory: Path,
    limits: ArchiveLimits,
) -> Iterator[_Artifacts]:
    source_kind, sql_path, sites_name, sites_path, manifest_version = _directory_artifact_paths(directory)
    sql_size = sql_path.stat().st_size
    sites_size = sites_path.stat().st_size
    if sql_size > limits.max_nested_archive_bytes or sites_size > limits.max_nested_archive_bytes:
        raise ToolError("Import source artifact exceeds the configured compressed-size limit")
    sql_sha = _sha256_file(sql_path)
    sites_sha = _sha256_file(sites_path)
    source_sha = hashlib.sha256(f"{sql_sha}:{sites_sha}".encode("ascii")).hexdigest()
    with sql_path.open("rb") as sql_handle, sites_path.open("rb") as sites_handle:
        yield _Artifacts(
            source_kind=source_kind,
            source_bytes=sql_size + sites_size,
            source_sha256=source_sha,
            sql=sql_handle,
            sql_name=sql_path.name,
            sql_bytes=sql_size,
            sql_sha256=sql_sha,
            sites=sites_handle,
            sites_name=sites_name,
            sites_bytes=sites_size,
            sites_sha256=sites_sha,
            manifest_version=manifest_version,
        )


@contextmanager
def _from_native_backup(path: Path, limits: ArchiveLimits) -> Iterator[_Artifacts]:
    source_size = path.stat().st_size
    if source_size > limits.max_expanded_bytes:
        raise ToolError("Native backup exceeds the configured source-size limit")
    source_sha = _sha256_file(path)
    sql_spool = tempfile.SpooledTemporaryFile(max_size=32 * 1024**2)
    sites_spool = tempfile.SpooledTemporaryFile(max_size=32 * 1024**2)
    selected: dict[str, tuple[int, str, str]] = {}
    extras = 0
    seen: set[str] = set()
    casefolded: set[str] = set()
    try:
        try:
            archive = tarfile.open(path, mode="r:*")
        except (tarfile.TarError, OSError) as exc:
            raise ToolError("Source file is not a supported native OpenEMR backup") from exc
        with archive:
            members = 0
            expanded = 0
            for member in archive:
                if member.isdir() and member.name in {".", "./"}:
                    continue
                member_path = _safe_member_name(member.name)
                normalized = member_path.as_posix()
                folded = normalized.casefold()
                if normalized in seen or folded in casefolded:
                    raise ToolError("Native backup contains duplicate or case-colliding paths")
                seen.add(normalized)
                casefolded.add(folded)
                members += 1
                expanded += member.size
                if members > limits.max_members or expanded > limits.max_expanded_bytes:
                    raise ToolError("Native backup archive limits exceeded")
                if not member.isfile():
                    if member.isdir():
                        continue
                    raise ToolError("Native backup contains a link or special file")
                basename = member_path.name
                target: IO[bytes] | None = None
                artifact_kind = ""
                if len(member_path.parts) == 1 and basename in _SQL_NAMES:
                    target = sql_spool
                    artifact_kind = "sql"
                elif len(member_path.parts) == 1 and basename in _SITE_NAMES:
                    target = sites_spool
                    artifact_kind = "sites"
                else:
                    extras += 1
                    continue
                if artifact_kind in selected:
                    raise ToolError("Native backup contains duplicate required artifacts")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ToolError("Native backup artifact could not be read")
                assert target is not None  # every branch that sets artifact_kind also sets target
                size, sha = _copy_bounded(
                    extracted,
                    target,
                    limits.max_nested_archive_bytes,
                )
                selected[artifact_kind] = (size, sha, basename)
        if source_size < 1 or expanded > source_size * limits.max_compression_ratio:
            raise ToolError("Native backup compression-ratio limit exceeded")
        if set(selected) != {"sql", "sites"}:
            raise ToolError("Native backup must contain openemr.sql[.gz] and openemr.tar.gz")
        yield _Artifacts(
            source_kind="native-openemr-backup",
            source_bytes=source_size,
            source_sha256=source_sha,
            sql=sql_spool,
            sql_name=selected["sql"][2],
            sql_bytes=selected["sql"][0],
            sql_sha256=selected["sql"][1],
            sites=sites_spool,
            sites_name=selected["sites"][2],
            sites_bytes=selected["sites"][0],
            sites_sha256=selected["sites"][1],
            extra_member_count=extras,
        )
    finally:
        sql_spool.close()
        sites_spool.close()


def _inspect_sql(
    handle: IO[bytes],
    *,
    name: str,
    compressed_bytes: int,
    limits: ArchiveLimits,
) -> tuple[str, int, bool, str, int]:
    handle.seek(0)
    compressed = name.lower().endswith(".gz")
    stream = handle
    try:
        if compressed:
            stream = cast(IO[bytes], gzip.GzipFile(fileobj=handle, mode="rb"))
        total = 0
        prefix = bytearray()
        identity_structure_buffer = b""
        identity_value_buffer = b""
        identity_structure_scanner = _SqlSecurityScanner(mask_backticks=False)
        identity_value_scanner = _SqlSecurityScanner(mask_quoted=False)
        security_scanner = _SqlSecurityScanner()
        security_carry = b""
        identity: tuple[str, int] | None = None
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limits.max_expanded_bytes:
                raise ToolError("SQL dump expanded-size limit exceeded")
            if len(prefix) < 2 * 1024 * 1024:
                prefix.extend(chunk[: 2 * 1024 * 1024 - len(prefix)])
            identity_structure = identity_structure_buffer + identity_structure_scanner.feed(chunk)
            identity_values = identity_value_buffer + identity_value_scanner.feed(chunk)
            for structural_match in _SQL_VERSION_INSERT.finditer(identity_structure):
                value_match = _SQL_VERSION_INSERT.match(
                    identity_values,
                    structural_match.start(),
                    structural_match.end(),
                )
                if value_match is None or value_match.end() != structural_match.end():
                    raise ToolError("SQL dump contains a malformed OpenEMR version row")
                candidate = _sql_version_identity(value_match)
                if identity is not None and candidate != identity:
                    raise ToolError("SQL dump contains conflicting OpenEMR version rows")
                identity = candidate
            identity_structure_buffer = identity_structure[-_SQL_IDENTITY_BUFFER_BYTES:]
            identity_value_buffer = identity_values[-_SQL_IDENTITY_BUFFER_BYTES:]
            security_scan = security_carry + security_scanner.feed(chunk)
            if _SQL_CLIENT_COMMAND_PATTERN.search(security_scan):
                raise ToolError("SQL dump contains an unsafe client command")
            if _SQL_STORED_CODE_PATTERN.search(security_scan):
                raise ToolError("SQL dump contains unsupported stored executable code")
            security_carry = security_scan[-512:]
        final_security_scan = security_carry + security_scanner.feed(
            b"",
            final=True,
        )
        if _SQL_CLIENT_COMMAND_PATTERN.search(final_security_scan):
            raise ToolError("SQL dump contains an unsafe client command")
        if _SQL_STORED_CODE_PATTERN.search(final_security_scan):
            raise ToolError("SQL dump contains unsupported stored executable code")
        final_identity_structure = identity_structure_buffer + identity_structure_scanner.feed(b"", final=True)
        final_identity_values = identity_value_buffer + identity_value_scanner.feed(b"", final=True)
        for structural_match in _SQL_VERSION_INSERT.finditer(final_identity_structure):
            value_match = _SQL_VERSION_INSERT.match(
                final_identity_values,
                structural_match.start(),
                structural_match.end(),
            )
            if value_match is None or value_match.end() != structural_match.end():
                raise ToolError("SQL dump contains a malformed OpenEMR version row")
            candidate = _sql_version_identity(value_match)
            if identity is not None and candidate != identity:
                raise ToolError("SQL dump contains conflicting OpenEMR version rows")
            identity = candidate
    except (gzip.BadGzipFile, EOFError, OSError, zlib.error) as exc:
        raise ToolError("SQL dump compression is malformed") from exc
    finally:
        handle.seek(0)
    if compressed and compressed_bytes and total > (compressed_bytes * limits.max_compression_ratio):
        raise ToolError("SQL dump compression-ratio limit exceeded")
    sample = bytes(prefix).lower()
    if b"\x00" in sample:
        raise ToolError("SQL dump appears to contain binary data")
    has_mysql_header = b"mysql dump" in sample or b"mariadb dump" in sample
    has_sql_structure = b"create table" in sample or b"insert into" in sample or b"lock tables" in sample
    if not has_mysql_header or not has_sql_structure:
        raise ToolError("SQL artifact is not a recognizable MySQL or MariaDB dump")
    if identity is None:
        raise ToolError("SQL dump does not contain an authoritative OpenEMR version row")
    database_type = "mariadb" if b"mariadb" in sample else "mysql"
    return database_type, total, compressed, identity[0], identity[1]


def _normalized_version(
    detected: str | None,
    declared: str | None,
    sql_detected: str,
) -> str | None:
    candidates = [value for value in (detected, declared, sql_detected) if value]
    try:
        normalized = {Version(value) for value in candidates}
    except InvalidVersion as exc:
        raise ToolError("Source OpenEMR version is not valid semantic version data") from exc
    if len(normalized) > 1:
        raise ToolError("SQL, version.php, and declared source versions do not match")
    value = detected or declared or sql_detected
    if value is None:
        return None
    try:
        parsed = Version(value)
    except InvalidVersion as exc:
        raise ToolError("Source OpenEMR version is not a valid version") from exc
    if parsed.is_prerelease or parsed.is_devrelease:
        raise ToolError("Prerelease OpenEMR sources are not supported")
    return str(parsed)


def inspect_source(
    source: Path,
    *,
    source_version: str | None = None,
    limits: ArchiveLimits | None = None,
) -> SourceInspection:
    """Fully inspect a source without extracting or mutating it."""

    limits = limits or ArchiveLimits()
    source = source.expanduser()
    if source.is_symlink():
        raise ToolError("Import source may not be a symlink")
    if not source.exists():
        raise ToolError("Import source does not exist")
    adapter = _from_directory(source, limits) if source.is_dir() else _from_native_backup(source, limits)
    with adapter as artifacts:
        (
            database_type,
            sql_expanded_bytes,
            sql_compressed,
            sql_openemr_version,
            sql_database_version,
        ) = _inspect_sql(
            artifacts.sql,
            name=artifacts.sql_name,
            compressed_bytes=artifacts.sql_bytes,
            limits=limits,
        )
        artifacts.sites.seek(0)
        archive_scan = scan_sites_archive(
            artifacts.sites,
            format_hint=artifacts.sites_name,
            limits=limits,
        )
        artifacts.sites.seek(0)
        if (
            artifacts.sites_name.lower().endswith((".gz", ".tgz", ".zip"))
            and artifacts.sites_bytes
            and archive_scan.expanded_bytes > artifacts.sites_bytes * limits.max_compression_ratio
        ):
            raise ToolError("Sites archive compression-ratio limit exceeded")
        version = _normalized_version(
            archive_scan.openemr_version,
            source_version or artifacts.manifest_version,
            sql_openemr_version,
        )
        unsupported = list(archive_scan.unsupported_content)
        manual_review = list(archive_scan.manual_review)
        if artifacts.extra_member_count:
            manual_review.append("Native backup contained additional top-level artifacts that will be ignored")
        if artifacts.source_kind == "native-openemr-backup" and archive_scan.openemr_version is None:
            unsupported.append(
                "Native backup must contain version.php; --source-version cannot authorize automatic execution"
            )
        if version is None:
            unsupported.append("Source OpenEMR version was not detected")
        if archive_scan.database_version is None:
            unsupported.append("version.php does not declare the OpenEMR database schema version")
        elif archive_scan.database_version != sql_database_version:
            unsupported.append("SQL and version.php database schema versions do not match")
        component_hashes = {
            "source": f"sha256:{artifacts.source_sha256}",
            "sql": f"sha256:{artifacts.sql_sha256}",
            "sites": f"sha256:{artifacts.sites_sha256}",
        }
        source_fingerprint = fingerprint(component_hashes, length=32)
        return SourceInspection(
            schema_version=SCHEMA_VERSION,
            source_kind=artifacts.source_kind,
            source_fingerprint=source_fingerprint,
            source_bytes=artifacts.source_bytes,
            source_openemr_version=version,
            source_database_version=(
                sql_database_version if archive_scan.database_version == sql_database_version else None
            ),
            database_type=database_type,
            sql_compressed=sql_compressed,
            sql_bytes=sql_expanded_bytes,
            sites_archive_bytes=artifacts.sites_bytes,
            expanded_site_bytes=archive_scan.expanded_bytes,
            archive_member_count=archive_scan.member_count,
            sites=archive_scan.sites,
            ignored_application_file_count=archive_scan.ignored_application_file_count,
            nested_archive_count=archive_scan.nested_archive_count,
            custom_code_detected=archive_scan.custom_code_detected,
            unsupported_content=tuple(unsupported),
            manual_review=tuple(manual_review),
            checksums=component_hashes,
        )
