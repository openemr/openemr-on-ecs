"""Isolated fresh-target OpenEMR import worker.

The worker is intentionally standalone so its Docker build context stays small.
It never logs archive member names, SQL content, credentials, or patient data.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import tarfile
import zlib
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import IO, Any

import boto3
from packaging.version import InvalidVersion, Version

MAX_MEMBERS = 100_000
MAX_MEMBER_BYTES = 2 * 1024**3
MAX_EXPANDED_BYTES = 20 * 1024**3
MAX_NESTED_ARCHIVE_BYTES = 4 * 1024**3
MAX_COMPRESSION_RATIO = 200
CHUNK_SIZE = 1024 * 1024
ACTIVE_CONTENT_SUFFIXES = {
    ".asp",
    ".aspx",
    ".bat",
    ".bash",
    ".cgi",
    ".class",
    ".cjs",
    ".cmd",
    ".com",
    ".dll",
    ".dylib",
    ".exe",
    ".htm",
    ".html",
    ".inc",
    ".jar",
    ".js",
    ".jsp",
    ".jspx",
    ".ksh",
    ".mjs",
    ".phar",
    ".php",
    ".php3",
    ".php4",
    ".php5",
    ".php7",
    ".php8",
    ".phtml",
    ".pl",
    ".ps1",
    ".py",
    ".rb",
    ".sh",
    ".shtml",
    ".so",
    ".svg",
    ".tcl",
    ".war",
    ".wasm",
    ".xhtml",
    ".zsh",
}
ACTIVE_CONTENT_PREFIXES = (
    b"#!",
    b"<?",
    b"MZ",
    b"\x00asm",
    b"\x7fELF",
    b"\xca\xfe\xba\xbe",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
)
IMPORTED_DIRECTORY_ROOTS = {"LBF", "documents", "images"}
IMPORTED_TOP_LEVEL_FILES = {
    "clickoptions.txt",
    "faxcover.txt",
    "faxtitle.eps",
    "referral_template.html",
}
TARGET_ONLY_FILENAMES = {
    ".htaccess",
    ".user.ini",
    "web.config",
}
REQUIRED_DRIVE_KEY_FILES = ("sevena", "sevenb")
# OpenEMR 8.2 seeds only reference/configuration tables in a pristine target.
# Any row in any other table is evidence that the target has been used and must
# block destructive replacement. Changes to upstream seed data therefore fail
# closed in the live MySQL integration test until this list is reviewed.
FRESH_SEEDED_TABLES = {
    "automatic_notification",
    "background_services",
    "categories",
    "categories_seq",
    "ccda_components",
    "ccda_sections",
    "clinical_plans",
    "clinical_plans_rules",
    "clinical_rules",
    "codes",
    "code_types",
    "customlists",
    "document_templates",
    "documents_legal_categories",
    "edi_sequences",
    "enc_category_map",
    "facility",
    "fee_sheet_options",
    "form_eye_mag_prefs",
    "gacl_acl",
    "gacl_acl_sections",
    "gacl_acl_seq",
    "gacl_aco",
    "gacl_aco_map",
    "gacl_aco_sections",
    "gacl_aco_sections_seq",
    "gacl_aco_seq",
    "gacl_aro",
    "gacl_aro_groups",
    "gacl_aro_groups_id_seq",
    "gacl_aro_groups_map",
    "gacl_aro_sections",
    "gacl_aro_sections_seq",
    "gacl_aro_seq",
    "gacl_groups_aro_map",
    "gacl_phpgacl",
    "globals",
    "groups",
    "insurance_type_codes",
    "issue_types",
    "lang_constants",
    "lang_definitions",
    "lang_languages",
    "layout_group_properties",
    "layout_options",
    "list_options",
    "medex_icons",
    "module_acl_group_settings",
    "module_acl_sections",
    "modules",
    "notification_settings",
    "openemr_module_vars",
    "openemr_modules",
    "openemr_postcalendar_categories",
    "patient_portal_menu",
    "preference_value_sets",
    "registry",
    "rule_action",
    "rule_action_item",
    "rule_filter",
    "rule_reminder",
    "rule_target",
    "sequences",
    "supported_external_dataloads",
    "user_settings",
    "users",
    "users_secure",
    "version",
}
_SEED_BASELINE_PATH = Path(__file__).with_name("fresh-seed-manifest.json")
try:
    _seed_manifest = json.loads(_SEED_BASELINE_PATH.read_text(encoding="utf-8"))
    FRESH_SEED_BASELINE: dict[str, dict[str, object]] = _seed_manifest["tables"]
except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
    raise RuntimeError("invalid fresh-seed manifest") from exc
if set(FRESH_SEED_BASELINE) != FRESH_SEEDED_TABLES:
    raise RuntimeError("fresh-seed manifest does not match worker table policy")
VERSION_FIELD = re.compile(
    rb"\$(v_major|v_minor|v_patch|v_tag|v_realpatch|v_database)\s*=\s*" rb"(?:['\"]([^'\"]*)['\"]|([0-9]+))"
)
SQL_VERSION_INSERT = re.compile(
    rb"(?:^|\n)[ \t]*INSERT\s+INTO\s+(?:`version`|version)\s*"
    rb"(?:\((?P<columns>[^)]{1,2048})\))?\s*"
    rb"VALUES\s*\((?P<values>.{1,8192}?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
DEFAULT_VERSION_COLUMNS = (
    "v_major",
    "v_minor",
    "v_patch",
    "v_realpatch",
    "v_tag",
    "v_database",
    "v_acl",
)
SQL_CLIENT_COMMANDS = (
    b"\\!",
    b"\\.",
    b"\\e",
    b"\\p",
    b"\\r",
    b"\\t",
    b"edit",
    b"pager",
    b"prompt",
    b"source",
    b"system",
    b"tee",
)
SQL_CLIENT_COMMAND_PATTERN = re.compile(
    rb"(?:^|[\n;])[ \t]*(?:\\[!.ePrRtT]|(?:edit|pager|prompt|source|system|tee)(?=[ \t\r\n;]))",
    re.IGNORECASE,
)
SQL_STORED_CODE_PATTERN = re.compile(
    rb"(?:\bDEFINER\s*=|"
    rb"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:PROCEDURE|FUNCTION|TRIGGER|EVENT)\b|"
    rb"\bALTER\s+(?:PROCEDURE|FUNCTION|EVENT)\b)",
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


class ImportFailure(RuntimeError):
    """A redacted worker failure safe to publish as status."""


def _phase(name: str) -> None:
    print(json.dumps({"phase": name}, sort_keys=True), flush=True)


def _safe_path(raw_name: str) -> PurePosixPath:
    if "\x00" in raw_name or "\\" in raw_name:
        raise ImportFailure("unsafe-archive-path")
    name = raw_name
    while name.startswith("./"):
        name = name[2:]
    if not name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise ImportFailure("unsafe-archive-path")
    path = PurePosixPath(name)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ImportFailure("unsafe-archive-path")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_fingerprint(values: dict[str, str]) -> str:
    payload = json.dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _staging_s3_safeguards() -> tuple[str, str]:
    owner = os.environ.get("IMPORT_STAGING_BUCKET_OWNER", "")
    key_arn = os.environ.get("IMPORT_STAGING_KMS_KEY_ARN", "")
    if not re.fullmatch(r"\d{12}", owner):
        raise ImportFailure("invalid-staging-bucket-owner")
    if not re.fullmatch(
        rf"arn:[a-z0-9-]+:kms:[a-z0-9-]+:{owner}:key/[A-Za-z0-9-]+",
        key_arn,
    ):
        raise ImportFailure("invalid-staging-kms-key")
    return owner, key_arn


def _copy_limited(source: IO[bytes], destination: IO[bytes], limit: int) -> int:
    total = 0
    while True:
        chunk = source.read(CHUNK_SIZE)
        if not chunk:
            return total
        total += len(chunk)
        if total > limit:
            raise ImportFailure("artifact-size-limit")
        destination.write(chunk)


def _unpack_native_source(source: Path, work: Path) -> tuple[Path, Path, dict[str, str]]:
    sites_path = work / "openemr.tar.gz"
    found: dict[str, tuple[Path, str]] = {}
    seen: set[str] = set()
    folded: set[str] = set()
    total = 0
    members = 0
    try:
        archive = tarfile.open(source, mode="r:*")
    except (tarfile.TarError, OSError) as exc:
        raise ImportFailure("malformed-native-backup") from exc
    with archive:
        for member in archive:
            if member.isdir() and member.name in {".", "./"}:
                continue
            path = _safe_path(member.name)
            normalized = path.as_posix()
            if normalized in seen or normalized.casefold() in folded:
                raise ImportFailure("duplicate-archive-member")
            seen.add(normalized)
            folded.add(normalized.casefold())
            members += 1
            total += member.size
            if members > MAX_MEMBERS or member.size > MAX_MEMBER_BYTES or total > MAX_EXPANDED_BYTES:
                raise ImportFailure("native-backup-limit")
            if not member.isfile():
                if member.isdir():
                    continue
                raise ImportFailure("special-archive-member")
            if len(path.parts) != 1:
                continue
            if path.name in {"openemr.sql", "openemr.sql.gz"}:
                kind = "sql"
                destination = work / path.name
            elif path.name == "openemr.tar.gz":
                kind = "sites"
                destination = sites_path
            else:
                continue
            if kind in found:
                raise ImportFailure("duplicate-required-artifact")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ImportFailure("unreadable-required-artifact")
            digest = hashlib.sha256()
            with destination.open("xb") as output:
                copied = 0
                while True:
                    chunk = extracted.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > MAX_NESTED_ARCHIVE_BYTES:
                        raise ImportFailure("artifact-size-limit")
                    digest.update(chunk)
                    output.write(chunk)
            found[kind] = (destination, digest.hexdigest())
    if set(found) != {"sql", "sites"}:
        raise ImportFailure("missing-required-artifact")
    source_bytes = source.stat().st_size
    if source_bytes < 1 or total > source_bytes * MAX_COMPRESSION_RATIO:
        raise ImportFailure("native-backup-compression-ratio")
    return (
        found["sql"][0],
        found["sites"][0],
        {"sql": found["sql"][1], "sites": found["sites"][1]},
    )


def _parse_version(content: bytes) -> tuple[str, int]:
    fields = {
        key.decode("ascii"): (quoted or numeric).decode("utf-8", errors="strict").strip()
        for key, quoted, numeric in VERSION_FIELD.findall(content)
    }
    if not all(field in fields for field in ("v_major", "v_minor", "v_patch", "v_database")):
        raise ImportFailure("missing-source-version")
    value = f"{fields['v_major']}.{fields['v_minor']}.{fields['v_patch']}"
    realpatch = fields.get("v_realpatch", "")
    if realpatch and realpatch != "0":
        value = f"{value}.{realpatch}"
    tag = fields.get("v_tag", "")
    if tag:
        value = f"{value}{tag}"
    try:
        parsed = Version(value)
    except InvalidVersion as exc:
        raise ImportFailure("invalid-source-version") from exc
    if parsed.is_prerelease or parsed.is_devrelease:
        raise ImportFailure("prerelease-source")
    try:
        database_version = int(fields["v_database"])
    except ValueError as exc:
        raise ImportFailure("invalid-source-database-version") from exc
    if database_version < 1:
        raise ImportFailure("invalid-source-database-version")
    return str(parsed), database_version


def _contains_active_bytes(content: bytes) -> bool:
    return (
        b"<?" in content
        or re.search(rb"(?:^|\n)[ \t]*#!", content) is not None
        or re.search(
            rb"<\s*(?:script|iframe|object|embed)\b|" rb"\bon[a-z]{3,32}\s*=",
            content,
            re.IGNORECASE,
        )
        is not None
    )


def _is_active_content(path: PurePosixPath, mode: int, prefix: bytes = b"") -> bool:
    suffixes = {suffix.lower() for suffix in path.suffixes}
    if path.parts[-3:] == (
        "sites",
        "default",
        "referral_template.html",
    ):
        suffixes.discard(".html")
    return (
        bool(mode & 0o111)
        or bool(suffixes & ACTIVE_CONTENT_SUFFIXES)
        or prefix.lstrip().startswith(ACTIVE_CONTENT_PREFIXES)
        or _contains_active_bytes(prefix)
    )


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
        raise ImportFailure("invalid-sql-version-row")
    values.append(bytes(current).strip())
    return tuple(values)


def _sql_scalar(raw: bytes) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[:1] == value[-1:] and value[:1] in {b"'", b'"'}:
        value = value[1:-1]
        value = value.replace(b"\\'", b"'").replace(b'\\"', b'"').replace(b"\\\\", b"\\")
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ImportFailure("invalid-sql-version-row") from exc


def _sql_version_identity(match: re.Match[bytes]) -> tuple[str, int]:
    raw_columns = match.group("columns")
    columns: tuple[str, ...]
    if raw_columns is None:
        columns = DEFAULT_VERSION_COLUMNS
    else:
        try:
            columns = tuple(
                value.decode("ascii", errors="strict").strip().strip("`").lower() for value in raw_columns.split(b",")
            )
        except UnicodeDecodeError as exc:
            raise ImportFailure("invalid-sql-version-row") from exc
    values = _split_sql_values(match.group("values"))
    if len(columns) != len(values):
        raise ImportFailure("invalid-sql-version-row")
    row = {column: _sql_scalar(value) for column, value in zip(columns, values)}
    required = {"v_major", "v_minor", "v_patch", "v_database"}
    if not required.issubset(row):
        raise ImportFailure("invalid-sql-version-row")
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
        raise ImportFailure("invalid-sql-version-row") from exc
    if parsed.is_prerelease or parsed.is_devrelease or database_version < 1:
        raise ImportFailure("invalid-sql-version-row")
    return str(parsed), database_version


def _validate_and_extract_sites(
    archive_path: Path,
    destination: Path,
) -> tuple[str, int]:
    seen: set[str] = set()
    folded: set[str] = set()
    members = 0
    expanded = 0
    version: str | None = None
    database_version: int | None = None
    has_sqlconf = False
    has_documents = False
    try:
        archive = tarfile.open(archive_path, mode="r:*")
    except (tarfile.TarError, OSError) as exc:
        raise ImportFailure("malformed-sites-archive") from exc
    with archive:
        for member in archive:
            if member.isdir() and member.name in {".", "./"}:
                continue
            path = _safe_path(member.name)
            normalized = path.as_posix()
            if normalized in seen or normalized.casefold() in folded:
                raise ImportFailure("duplicate-archive-member")
            seen.add(normalized)
            folded.add(normalized.casefold())
            members += 1
            expanded += member.size
            if members > MAX_MEMBERS or member.size > MAX_MEMBER_BYTES or expanded > MAX_EXPANDED_BYTES:
                raise ImportFailure("sites-archive-limit")
            if not (member.isfile() or member.isdir()):
                raise ImportFailure("special-archive-member")
            if normalized == "version.php" and member.isfile():
                if member.size > 64 * 1024:
                    raise ImportFailure("invalid-source-version")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ImportFailure("invalid-source-version")
                version, database_version = _parse_version(extracted.read(64 * 1024 + 1))
            if len(path.parts) < 2 or path.parts[:2] != ("sites", "default"):
                continue
            relative = path.parts[2:]
            if not relative:
                continue
            if relative == ("sqlconf.php",):
                has_sqlconf = True
                continue
            if not (
                relative[0] in IMPORTED_DIRECTORY_ROOTS
                or (len(relative) == 1 and relative[0] in IMPORTED_TOP_LEVEL_FILES)
            ):
                continue
            if path.name.lower() in TARGET_ONLY_FILENAMES:
                continue
            if relative[0] == "documents":
                has_documents = True
            if member.isfile() and _is_active_content(path, member.mode):
                raise ImportFailure("custom-executable-content")
            output = destination.joinpath(*relative)
            resolved = output.resolve()
            try:
                resolved.relative_to(destination.resolve())
            except ValueError as exc:
                raise ImportFailure("unsafe-archive-path") from exc
            if member.isdir():
                output.mkdir(parents=True, exist_ok=True, mode=0o750)
                output.chmod(0o750)
                continue
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ImportFailure("unreadable-sites-member")
            with output.open("xb") as target:
                copied = _copy_limited(extracted, target, member.size)
            if copied != member.size:
                raise ImportFailure("sites-member-size-mismatch")
            active_carry = b""
            first_chunk = True
            with output.open("rb") as saved:
                while True:
                    chunk = saved.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    active_scan = active_carry + chunk
                    if (first_chunk and chunk.lstrip().startswith(ACTIVE_CONTENT_PREFIXES)) or _contains_active_bytes(
                        active_scan
                    ):
                        raise ImportFailure("custom-executable-content")
                    first_chunk = False
                    active_carry = active_scan[-128:]
            output.chmod(0o640)
    compressed = archive_path.stat().st_size
    if compressed and expanded > compressed * MAX_COMPRESSION_RATIO:
        raise ImportFailure("sites-compression-ratio")
    if not version or database_version is None:
        raise ImportFailure("missing-source-version")
    if not has_sqlconf or not has_documents:
        raise ImportFailure("incomplete-default-site")
    _validate_drive_key_files(destination)
    return version, database_version


def _validate_drive_key_files(default_site: Path) -> None:
    methods = default_site / "documents" / "logs_and_misc" / "methods"
    for label in REQUIRED_DRIVE_KEY_FILES:
        path = methods / label
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            raise ImportFailure("missing-document-encryption-keys")
        content = path.read_bytes().strip()
        if not content.startswith(b"007"):
            raise ImportFailure("invalid-document-encryption-keys")
        try:
            decoded = base64.b64decode(content[3:], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImportFailure("invalid-document-encryption-keys") from exc
        if len(decoded) < 96:
            raise ImportFailure("invalid-document-encryption-keys")


def _validate_sql(sql_artifact: Path, output: Path) -> tuple[str, int]:
    compressed = sql_artifact.name.endswith(".gz")
    prefix = bytearray()
    identity_structure_scanner = _SqlSecurityScanner(mask_backticks=False)
    identity_value_scanner = _SqlSecurityScanner(mask_quoted=False)
    security_scanner = _SqlSecurityScanner()
    security_carry = b""
    identity_structure_buffer = b""
    identity_value_buffer = b""
    identity: tuple[str, int] | None = None
    total = 0
    try:
        source = gzip.open(sql_artifact, "rb") if compressed else sql_artifact.open("rb")
        with source, output.open("xb") as destination:
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_EXPANDED_BYTES:
                    raise ImportFailure("sql-size-limit")
                if len(prefix) < 2 * CHUNK_SIZE:
                    prefix.extend(chunk[: 2 * CHUNK_SIZE - len(prefix)])
                identity_structure = identity_structure_buffer + identity_structure_scanner.feed(chunk)
                identity_values = identity_value_buffer + identity_value_scanner.feed(chunk)
                for structural_match in SQL_VERSION_INSERT.finditer(identity_structure):
                    value_match = SQL_VERSION_INSERT.match(
                        identity_values,
                        structural_match.start(),
                        structural_match.end(),
                    )
                    if value_match is None or value_match.end() != structural_match.end():
                        raise ImportFailure("malformed-sql-version-row")
                    candidate = _sql_version_identity(value_match)
                    if identity is not None and identity != candidate:
                        raise ImportFailure("conflicting-sql-version-rows")
                    identity = candidate
                identity_structure_buffer = identity_structure[-64 * 1024 :]
                identity_value_buffer = identity_values[-64 * 1024 :]
                sanitized = security_scanner.feed(chunk)
                security_scan = security_carry + sanitized
                if SQL_CLIENT_COMMAND_PATTERN.search(security_scan):
                    raise ImportFailure("unsafe-sql-client-command")
                if SQL_STORED_CODE_PATTERN.search(security_scan):
                    raise ImportFailure("unsupported-sql-stored-code")
                security_carry = security_scan[-512:]
                destination.write(chunk)
    except (gzip.BadGzipFile, EOFError, OSError, zlib.error) as exc:
        raise ImportFailure("malformed-sql-artifact") from exc
    if compressed and sql_artifact.stat().st_size and total > (sql_artifact.stat().st_size * MAX_COMPRESSION_RATIO):
        raise ImportFailure("sql-compression-ratio")
    final_security_scan = security_carry + security_scanner.feed(
        b"",
        final=True,
    )
    if SQL_CLIENT_COMMAND_PATTERN.search(final_security_scan):
        raise ImportFailure("unsafe-sql-client-command")
    if SQL_STORED_CODE_PATTERN.search(final_security_scan):
        raise ImportFailure("unsupported-sql-stored-code")
    final_identity_structure = identity_structure_buffer + identity_structure_scanner.feed(b"", final=True)
    final_identity_values = identity_value_buffer + identity_value_scanner.feed(b"", final=True)
    for structural_match in SQL_VERSION_INSERT.finditer(final_identity_structure):
        value_match = SQL_VERSION_INSERT.match(
            final_identity_values,
            structural_match.start(),
            structural_match.end(),
        )
        if value_match is None or value_match.end() != structural_match.end():
            raise ImportFailure("malformed-sql-version-row")
        candidate = _sql_version_identity(value_match)
        if identity is not None and identity != candidate:
            raise ImportFailure("conflicting-sql-version-rows")
        identity = candidate
    sample = bytes(prefix).lower()
    if (
        b"\x00" in sample
        or not (b"mysql dump" in sample or b"mariadb dump" in sample)
        or not (b"create table" in sample or b"insert into" in sample or b"lock tables" in sample)
    ):
        raise ImportFailure("unrecognized-sql-dump")
    if identity is None:
        raise ImportFailure("missing-sql-version-row")
    return identity


def _mysql_command(*extra: str, username: str | None = None) -> list[str]:
    required = (
        "MYSQL_HOST",
        "MYSQL_PORT",
        "MYSQL_USERNAME",
        "MYSQL_PASSWORD",
        "MYSQL_SSL_CA",
    )
    if any(not os.environ.get(name) for name in required):
        raise ImportFailure("missing-database-configuration")
    return [
        "mariadb",
        "--batch",
        "--binary-mode",
        "--local-infile=0",
        "--sandbox",
        "--skip-column-names",
        "--connect-timeout=15",
        f"--host={os.environ['MYSQL_HOST']}",
        f"--port={os.environ['MYSQL_PORT']}",
        f"--user={username or os.environ['MYSQL_USERNAME']}",
        f"--ssl-ca={os.environ['MYSQL_SSL_CA']}",
        "--ssl-verify-server-cert",
        *extra,
    ]


def _run_mysql(
    *extra: str,
    stdin: IO[bytes] | None = None,
    username: str | None = None,
    password: str | None = None,
) -> str:
    env = {
        "HOME": os.environ.get("HOME", "/home/importer"),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "MYSQL_PWD": password or os.environ["MYSQL_PASSWORD"],
    }
    command = _mysql_command(*extra, username=username)
    # subprocess needs a real fileno() for stdin=; BytesIO and similar buffers
    # must be passed via input= instead.
    if stdin is None:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=env,
            timeout=7200,
        )
    else:
        try:
            stdin.fileno()
        except AttributeError, io.UnsupportedOperation, OSError:
            completed = subprocess.run(
                command,
                input=stdin.read(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=env,
                timeout=7200,
            )
        else:
            completed = subprocess.run(
                command,
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=env,
                timeout=7200,
            )
    if completed.returncode != 0:
        raise ImportFailure("database-command-failed")
    return completed.stdout.decode("utf-8").strip() if completed.stdout else ""


def _run_mysql_raw(*extra: str) -> bytes:
    env = {
        "HOME": os.environ.get("HOME", "/home/importer"),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "MYSQL_PWD": os.environ["MYSQL_PASSWORD"],
    }
    completed = subprocess.run(
        _mysql_command(*extra),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=env,
        timeout=7200,
    )
    if completed.returncode != 0:
        raise ImportFailure("database-command-failed")
    return completed.stdout


def _seed_table_fingerprint(database: str, table: str) -> str:
    # Both identifiers are restricted to the manifest/database allowlist before interpolation.
    column_names = _run_mysql(
        "information_schema",
        "--execute=SELECT COLUMN_NAME FROM COLUMNS "  # noqa: S608
        f"WHERE TABLE_SCHEMA = '{database}' AND TABLE_NAME = '{table}' "
        "ORDER BY ORDINAL_POSITION",
    ).splitlines()
    excluded_raw = FRESH_SEED_BASELINE[table].get("exclude_columns", [])
    if not isinstance(excluded_raw, list) or any(
        not isinstance(column, str) or not re.fullmatch(r"[A-Za-z0-9_]+", column) for column in excluded_raw
    ):
        raise ImportFailure("target-seed-manifest-invalid")
    excluded = set(excluded_raw)
    if not excluded.issubset(column_names):
        raise ImportFailure("target-seed-schema-check-failed")
    selected = [column for column in column_names if column not in excluded]
    if not selected:
        raise ImportFailure("target-seed-schema-check-failed")
    selection = ",".join(f"`{column}`" for column in selected)
    ordering = ",".join(f"BINARY `{column}`" for column in selected)
    content = _run_mysql_raw(
        database,
        f"--execute=SELECT {selection} FROM `{table}` "  # noqa: S608
        f"ORDER BY {ordering}",
    )
    return hashlib.sha256(content).hexdigest()


def _dump_target_database(output: Path) -> None:
    database = os.environ.get("MYSQL_DATABASE", "openemr")
    if not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise ImportFailure("invalid-database-name")
    required = (
        "MYSQL_HOST",
        "MYSQL_PORT",
        "MYSQL_USERNAME",
        "MYSQL_PASSWORD",
        "MYSQL_SSL_CA",
    )
    if any(not os.environ.get(name) for name in required):
        raise ImportFailure("missing-database-configuration")
    # The database identifier is restricted to ASCII letters, digits, and underscores above.
    stored_code_count = _run_mysql(
        "information_schema",
        "--execute=SELECT "  # noqa: S608
        f"(SELECT COUNT(*) FROM ROUTINES WHERE ROUTINE_SCHEMA = '{database}') + "
        f"(SELECT COUNT(*) FROM EVENTS WHERE EVENT_SCHEMA = '{database}') + "
        f"(SELECT COUNT(*) FROM TRIGGERS WHERE TRIGGER_SCHEMA = '{database}')",
    )
    if stored_code_count != "0":
        raise ImportFailure("target-baseline-has-stored-code")
    env = {
        "HOME": os.environ.get("HOME", "/home/importer"),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "MYSQL_PWD": os.environ["MYSQL_PASSWORD"],
    }
    command = [
        "mariadb-dump",
        "--single-transaction",
        "--skip-lock-tables",
        "--hex-blob",
        "--skip-routines",
        "--skip-triggers",
        "--skip-events",
        "--no-tablespaces",
        "--default-character-set=utf8mb4",
        f"--host={os.environ['MYSQL_HOST']}",
        f"--port={os.environ['MYSQL_PORT']}",
        f"--user={os.environ['MYSQL_USERNAME']}",
        f"--ssl-ca={os.environ['MYSQL_SSL_CA']}",
        "--ssl-verify-server-cert",
        database,
    ]
    try:
        with output.open("xb") as destination:
            completed = subprocess.run(
                command,
                stdout=destination,
                stderr=subprocess.PIPE,
                check=False,
                env=env,
                timeout=7200,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        output.unlink(missing_ok=True)
        raise ImportFailure("target-baseline-dump-failed") from exc
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size < 1:
        output.unlink(missing_ok=True)
        raise ImportFailure("target-baseline-dump-failed")
    output.chmod(0o600)


def _database_version_identity(database: str) -> tuple[str, int]:
    rows = _run_mysql(
        database,
        "--execute=SELECT v_major,v_minor,v_patch,v_realpatch,v_tag,v_database FROM version;",
    ).splitlines()
    if len(rows) != 1:
        raise ImportFailure("target-version-row-is-not-unique")
    values = rows[0].split("\t")
    if len(values) != 6:
        raise ImportFailure("target-version-row-is-malformed")
    version = f"{values[0]}.{values[1]}.{values[2]}"
    if values[3] not in {"", "0", "NULL"}:
        version = f"{version}.{values[3]}"
    if values[4] not in {"", "NULL"}:
        version = f"{version}{values[4]}"
    try:
        return str(Version(version)), int(values[5])
    except (InvalidVersion, ValueError) as exc:
        raise ImportFailure("target-version-row-is-malformed") from exc


def _assert_empty_target(
    expected_openemr_version: str,
    expected_database_version: int,
) -> None:
    if (
        _seed_manifest.get("openemr_version") != expected_openemr_version
        or _seed_manifest.get("database_version") != expected_database_version
    ):
        raise ImportFailure("target-seed-manifest-version-mismatch")
    database = os.environ.get("MYSQL_DATABASE", "openemr")
    tables = set(_run_mysql(database, "--execute=SHOW TABLES").splitlines())
    required = {"documents", "form_encounter", "patient_data", "users"}
    if not required.issubset(tables):
        raise ImportFailure("target-schema-is-not-initialized")
    for table in sorted(tables):
        if not re.fullmatch(r"[A-Za-z0-9_]+", table):
            raise ImportFailure("target-schema-has-unsafe-table-name")
        value = _run_mysql(
            database,
            f"--execute=SELECT COUNT(*) FROM `{table}`",  # noqa: S608
        )
        try:
            count = int(value.splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise ImportFailure("target-emptiness-check-failed") from exc
        if table not in FRESH_SEEDED_TABLES and count != 0:
            raise ImportFailure("target-is-not-empty")
        if table in FRESH_SEEDED_TABLES:
            baseline = FRESH_SEED_BASELINE[table]
            expected_count = baseline.get("rows")
            if isinstance(expected_count, bool) or not isinstance(expected_count, int) or count != expected_count:
                raise ImportFailure("target-seed-row-count-mismatch")
            expected_sha256 = baseline.get("sha256")
            if expected_sha256 is not None and (
                not isinstance(expected_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
                or _seed_table_fingerprint(database, table) != expected_sha256
            ):
                raise ImportFailure(f"target-seed-content-mismatch:{table}")
    actual_version, actual_database_version = _database_version_identity(database)
    if (
        Version(actual_version) != Version(expected_openemr_version)
        or actual_database_version != expected_database_version
    ):
        raise ImportFailure("target-schema-version-mismatch")


def _assert_efs_mutable(mount_root: Path, migration_id: str) -> None:
    """Prove the worker can create and atomically rename sibling directories."""

    if not mount_root.is_dir() or not (mount_root / "default").is_dir():
        raise ImportFailure("target-default-site-missing")
    probe = mount_root / f".openemr-import-probe-{migration_id}"
    renamed = mount_root / f".openemr-import-probe-renamed-{migration_id}"
    if probe.exists() or renamed.exists():
        raise ImportFailure("migration-probe-path-already-exists")
    try:
        probe.mkdir(mode=0o700)
        (probe / "probe").write_bytes(b"openemr-import-write-test\n")
        os.replace(probe, renamed)
    except OSError as exc:
        raise ImportFailure("target-sites-not-writable") from exc
    finally:
        shutil.rmtree(probe, ignore_errors=True)
        shutil.rmtree(renamed, ignore_errors=True)


def _assert_fresh_efs_target(mount_root: Path) -> None:
    """Reject existing document content while allowing only generated baseline files."""

    default_site = mount_root / "default"
    documents = default_site / "documents"
    if not documents.is_dir():
        raise ImportFailure("target-documents-directory-missing")
    key_name = re.compile(r"(?:one|two|three|four|five|six|seven)(?:a|b)?")
    for path in documents.rglob("*"):
        if path.is_symlink():
            raise ImportFailure("unsafe-target-site-path")
        if not path.is_file():
            continue
        relative = path.relative_to(default_site)
        if path.name.lower() in TARGET_ONLY_FILENAMES:
            continue
        if relative == Path("documents/certificates/mysql-ca"):
            continue
        if (
            relative.parts[:3] == ("documents", "logs_and_misc", "methods")
            and len(relative.parts) == 4
            and key_name.fullmatch(relative.name)
        ):
            continue
        raise ImportFailure("target-site-is-not-empty")


def _import_username(migration_id: str) -> str:
    import_username = f"oe_import_{migration_id.removeprefix('import-')}"
    if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", import_username):
        raise ImportFailure("invalid-migration-identity")
    return import_username


def _drop_import_user(migration_id: str) -> None:
    import_username = _import_username(migration_id)
    _run_mysql(stdin=io.BytesIO(f"DROP USER IF EXISTS '{import_username}'@'%';\n".encode("ascii")))


def _replace_database(sql_path: Path, migration_id: str) -> None:
    database = os.environ.get("MYSQL_DATABASE", "openemr")
    if not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise ImportFailure("invalid-database-name")
    _run_mysql(
        f"--execute=DROP DATABASE `{database}`; "
        f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    import_username = _import_username(migration_id)
    import_password = secrets.token_hex(32)
    account = f"'{import_username}'@'%'"
    setup = (
        f"DROP USER IF EXISTS {account};\n"
        f"CREATE USER {account} IDENTIFIED BY '{import_password}' REQUIRE SSL;\n"
        f"GRANT ALL PRIVILEGES ON `{database}`.* TO {account};\n"
    ).encode("ascii")
    teardown = f"DROP USER IF EXISTS {account};\n".encode("ascii")
    try:
        _run_mysql(stdin=io.BytesIO(setup))
        with sql_path.open("rb") as sql:
            _run_mysql(
                database,
                stdin=sql,
                username=import_username,
                password=import_password,
            )
    finally:
        _run_mysql(stdin=io.BytesIO(teardown))


def _restore_baseline_database(sql_path: Path) -> None:
    database = os.environ.get("MYSQL_DATABASE", "openemr")
    if not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise ImportFailure("invalid-database-name")
    _run_mysql(
        f"--execute=DROP DATABASE `{database}`; "
        f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    with sql_path.open("rb") as sql:
        _run_mysql(database, stdin=sql)


def _overlay_import_data(import_default: Path, staged_default: Path) -> None:
    for source in import_default.iterdir():
        target = staged_default / source.name
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)


def _restore_target_only_files(source_default: Path, staged_default: Path) -> None:
    if not (source_default / "sqlconf.php").is_file():
        raise ImportFailure("target-sqlconf-missing")
    target_ca = source_default / "documents" / "certificates" / "mysql-ca"
    if target_ca.is_file():
        staged_ca = staged_default / "documents" / "certificates" / "mysql-ca"
        staged_ca.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(target_ca, staged_ca)
    for source in source_default.rglob("*"):
        if not source.is_file() or source.name.lower() not in TARGET_ONLY_FILENAMES:
            continue
        relative = source.relative_to(source_default)
        destination = staged_default / relative
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(source, destination)


def _normalize_site_permissions(default_site: Path) -> None:
    """Make imported content writable by OpenEMR's enforced EFS gid 101."""

    for root, directories, files in os.walk(default_site, followlinks=False):
        root_path = Path(root)
        if root_path.is_symlink():
            raise ImportFailure("unsafe-staged-site-path")
        root_path.chmod(0o770)
        for name in directories:
            path = root_path / name
            if path.is_symlink():
                raise ImportFailure("unsafe-staged-site-path")
            path.chmod(0o770)
        for name in files:
            path = root_path / name
            if path.is_symlink() or not path.is_file():
                raise ImportFailure("unsafe-staged-site-path")
            path.chmod(0o660)


def _stage_and_swap_sites(work_default: Path, mount_root: Path, migration_id: str) -> None:
    target_default = mount_root / "default"
    if not target_default.is_dir():
        raise ImportFailure("target-default-site-missing")
    staging_root = mount_root / ".openemr-import-staging" / migration_id
    backup_root = mount_root / ".openemr-import-backup" / migration_id
    backup_default = backup_root / "default"
    if staging_root.exists() or backup_default.exists():
        raise ImportFailure("migration-path-already-exists")
    staged_default = staging_root / "default"
    staging_root.mkdir(parents=True, mode=0o700)
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copytree(target_default, staged_default)
    _overlay_import_data(work_default, staged_default)
    _restore_target_only_files(target_default, staged_default)
    _normalize_site_permissions(staged_default)
    os.replace(target_default, backup_default)
    try:
        os.replace(staged_default, target_default)
    except BaseException:
        os.replace(backup_default, target_default)
        raise


def _restore_site_backup(mount_root: Path, migration_id: str) -> None:
    target_default = mount_root / "default"
    staging_root = mount_root / ".openemr-import-staging" / migration_id
    backup_default = mount_root / ".openemr-import-backup" / migration_id / "default"
    if not backup_default.is_dir():
        return
    failed_default = staging_root / "failed-default"
    staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if failed_default.exists():
        if target_default.exists():
            raise ImportFailure("site-rollback-state-is-ambiguous")
        os.replace(backup_default, target_default)
        return
    if target_default.exists():
        os.replace(target_default, failed_default)
    os.replace(backup_default, target_default)


def _attempt_automatic_rollback(
    *,
    mount_root: Path,
    migration_id: str,
    baseline_sql: Path | None,
    database_mutation_started: bool,
) -> bool:
    succeeded = True
    try:
        _restore_site_backup(mount_root, migration_id)
        if database_mutation_started:
            if baseline_sql is None or not baseline_sql.is_file():
                raise ImportFailure("target-baseline-dump-missing")
            _restore_baseline_database(baseline_sql)
    except Exception:
        succeeded = False
    try:
        _drop_import_user(migration_id)
    except Exception:
        succeeded = False
    return succeeded


def _validate_import(
    mount_root: Path,
    expected_openemr_version: str,
    expected_database_version: int,
) -> None:
    database = os.environ.get("MYSQL_DATABASE", "openemr")
    tables = _run_mysql(database, "--execute=SHOW TABLES")
    if len(tables.splitlines()) < 50:
        raise ImportFailure("database-validation-failed")
    actual_version, actual_database_version = _database_version_identity(database)
    if (
        Version(actual_version) != Version(expected_openemr_version)
        or actual_database_version != expected_database_version
    ):
        raise ImportFailure("database-version-validation-failed")
    try:
        _validate_drive_key_files(mount_root / "default")
    except ImportFailure as exc:
        raise ImportFailure("site-validation-failed") from exc


def _put_status(
    s3_client: Any,
    *,
    bucket: str,
    migration_id: str,
    status: str,
    phase: str,
    error: str | None = None,
    rollback_status: str | None = None,
) -> None:
    owner, key_arn = _staging_s3_safeguards()
    payload = {
        "schema_version": 1,
        "migration_id": migration_id,
        "status": status,
        "phase": phase,
        "error": error,
        "rollback_status": rollback_status,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    # The bucket's default encryption applies the stack's customer-managed KMS
    # key; a generic aws:kms request header would select the AWS-managed S3 key.
    s3_client.put_object(
        Bucket=bucket,
        Key=f"migrations/{migration_id}/status.json",
        Body=(json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"),
        ContentType="application/json",
        ExpectedBucketOwner=owner,
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=key_arn,
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--operation",
        choices=("import", "cleanup", "recover"),
        default="import",
    )
    parser.add_argument("--migration-id", required=True)
    parser.add_argument("--source-key")
    parser.add_argument("--source-sha256")
    parser.add_argument("--source-fingerprint")
    parser.add_argument("--source-openemr-version")
    parser.add_argument("--source-database-version", type=int)
    parser.add_argument("--recovery-verified", action="store_true")
    parser.add_argument("--delete-site-backup", action="store_true")
    return parser.parse_args()


def _cleanup_site_artifacts(migration_id: str, *, delete_backup: bool) -> None:
    mount_root = Path(os.environ.get("OPENEMR_SITES_MOUNT_ROOT", "/mnt/openemr-sites")).resolve()
    targets = [mount_root / ".openemr-import-staging" / migration_id]
    if delete_backup:
        targets.append(mount_root / ".openemr-import-backup" / migration_id)
    for target in targets:
        try:
            target.resolve().relative_to(mount_root)
        except ValueError as exc:
            raise ImportFailure("unsafe-cleanup-path") from exc
        if target.is_symlink():
            raise ImportFailure("unsafe-cleanup-path")
        if target.exists():
            shutil.rmtree(target)


def _recover_local_baseline(migration_id: str) -> None:
    """Restore the worker-created database and EFS baseline after hard termination."""

    mount_root = Path(os.environ.get("OPENEMR_SITES_MOUNT_ROOT", "/mnt/openemr-sites")).resolve()
    baseline_sql = mount_root / ".openemr-import-backup" / migration_id / "target-baseline.sql"
    if baseline_sql.is_symlink() or not baseline_sql.is_file():
        raise ImportFailure("target-baseline-dump-missing")
    if not _attempt_automatic_rollback(
        mount_root=mount_root,
        migration_id=migration_id,
        baseline_sql=baseline_sql,
        database_mutation_started=True,
    ):
        raise ImportFailure("local-baseline-recovery-failed")
    database = os.environ.get("MYSQL_DATABASE", "openemr")
    restored_version, restored_database_version = _database_version_identity(database)
    _assert_empty_target(restored_version, restored_database_version)
    _assert_fresh_efs_target(mount_root)


def main() -> int:
    args = _arguments()
    if not re.fullmatch(r"import-[a-f0-9]{16}", args.migration_id):
        raise SystemExit("invalid migration identifier")
    if args.operation == "cleanup":
        if not args.delete_site_backup:
            raise SystemExit("cleanup requires explicit rollback-copy deletion")
        _phase("cleanup")
        try:
            _cleanup_site_artifacts(
                args.migration_id,
                delete_backup=args.delete_site_backup,
            )
        except ImportFailure as exc:
            print(
                json.dumps(
                    {"phase": "cleanup", "status": "failed", "error": str(exc)},
                    sort_keys=True,
                ),
                flush=True,
            )
            return 1
        _phase("cleanup-complete")
        return 0
    if args.operation == "recover":
        _phase("recovery")
        try:
            _recover_local_baseline(args.migration_id)
        except ImportFailure as exc:
            print(
                json.dumps(
                    {
                        "phase": "recovery",
                        "status": "failed",
                        "error": str(exc),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return 1
        _phase("recovery-complete")
        return 0
    if (
        not args.source_key
        or not args.source_sha256
        or not args.source_fingerprint
        or not args.source_openemr_version
        or not args.source_database_version
    ):
        raise SystemExit("import source arguments are required")
    expected_key = f"migrations/{args.migration_id}/source.tar"
    if args.source_key != expected_key:
        raise SystemExit("invalid staging key")
    bucket = os.environ.get("IMPORT_STAGING_BUCKET", "")
    if not bucket or not args.recovery_verified:
        raise SystemExit("required import safeguards are missing")
    try:
        bucket_owner, _ = _staging_s3_safeguards()
    except ImportFailure as exc:
        raise SystemExit(str(exc)) from exc
    try:
        s3_client = boto3.client("s3")
    except Exception:
        print(
            json.dumps(
                {
                    "phase": "initialization",
                    "status": "failed",
                    "error": "aws-client-initialization-failed",
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 1
    work = Path("/work") / args.migration_id
    work.mkdir(mode=0o700)
    source = work / "source.tar"
    mount_root = Path(os.environ.get("OPENEMR_SITES_MOUNT_ROOT", "/mnt/openemr-sites"))
    baseline_sql: Path | None = None
    database_mutation_started = False
    phase = "download"

    def publish_phase(name: str) -> None:
        nonlocal phase
        phase = name
        _put_status(
            s3_client,
            bucket=bucket,
            migration_id=args.migration_id,
            status="running",
            phase=name,
        )
        _phase(name)

    try:
        publish_phase("download")
        s3_client.download_file(
            bucket,
            args.source_key,
            str(source),
            ExtraArgs={"ExpectedBucketOwner": bucket_owner},
        )
        source_sha = _sha256(source)
        if source_sha != args.source_sha256:
            raise ImportFailure("source-checksum-mismatch")

        publish_phase("source-validation")
        sql_artifact, sites_artifact, artifact_hashes = _unpack_native_source(source, work)
        component_hashes = {
            "source": f"sha256:{source_sha}",
            "sql": f"sha256:{artifact_hashes['sql']}",
            "sites": f"sha256:{artifact_hashes['sites']}",
        }
        if _canonical_fingerprint(component_hashes) != args.source_fingerprint:
            raise ImportFailure("source-fingerprint-mismatch")
        source.unlink()
        work_default = work / "sites" / "default"
        work_default.mkdir(parents=True, mode=0o700)
        source_version, source_database_version = _validate_and_extract_sites(
            sites_artifact,
            work_default,
        )
        sites_artifact.unlink()
        target_version = os.environ.get("TARGET_OPENEMR_VERSION", "")
        if (
            Version(source_version) != Version(target_version)
            or Version(source_version) != Version(args.source_openemr_version)
            or source_database_version != args.source_database_version
        ):
            raise ImportFailure("source-target-version-mismatch")
        sql = work / "validated.sql"
        sql_version, sql_database_version = _validate_sql(sql_artifact, sql)
        sql_artifact.unlink()
        if Version(sql_version) != Version(source_version) or sql_database_version != source_database_version:
            raise ImportFailure("source-component-version-mismatch")

        publish_phase("target-validation")
        _assert_efs_mutable(mount_root, args.migration_id)
        _assert_fresh_efs_target(mount_root)
        _assert_empty_target(source_version, source_database_version)

        publish_phase("recovery-baseline")
        rollback_root = mount_root / ".openemr-import-backup" / args.migration_id
        if rollback_root.exists():
            raise ImportFailure("migration-path-already-exists")
        rollback_root.mkdir(parents=True, mode=0o700)
        baseline_sql = rollback_root / "target-baseline.sql"
        _dump_target_database(baseline_sql)

        publish_phase("database-import")
        database_mutation_started = True
        _replace_database(sql, args.migration_id)

        publish_phase("site-import")
        _stage_and_swap_sites(work_default, mount_root, args.migration_id)

        publish_phase("post-import-validation")
        _validate_import(mount_root, source_version, source_database_version)
        _put_status(
            s3_client,
            bucket=bucket,
            migration_id=args.migration_id,
            status="succeeded",
            phase="complete",
        )
        _phase("complete")
        return 0
    except ImportFailure as exc:
        rollback_status: str | None = None
        published_error = str(exc)
        if database_mutation_started:
            rollback_succeeded = _attempt_automatic_rollback(
                mount_root=mount_root,
                migration_id=args.migration_id,
                baseline_sql=baseline_sql,
                database_mutation_started=database_mutation_started,
            )
            rollback_status = "succeeded" if rollback_succeeded else "failed"
            if not rollback_succeeded:
                published_error = "automatic-rollback-failed"
        try:
            _put_status(
                s3_client,
                bucket=bucket,
                migration_id=args.migration_id,
                status="failed",
                phase=phase,
                error=published_error,
                rollback_status=rollback_status,
            )
        except Exception:
            # Still emit the local failure record even if status publishing fails.
            print(
                json.dumps(
                    {
                        "phase": phase,
                        "status": "failed",
                        "error": "status-publish-failed",
                        "original_error": published_error,
                        "rollback_status": rollback_status,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        print(
            json.dumps(
                {
                    "phase": phase,
                    "status": "failed",
                    "error": published_error,
                    "rollback_status": rollback_status,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 1
    except Exception:
        rollback_status = None
        published_error = "internal-worker-error"
        if database_mutation_started:
            rollback_succeeded = _attempt_automatic_rollback(
                mount_root=mount_root,
                migration_id=args.migration_id,
                baseline_sql=baseline_sql,
                database_mutation_started=database_mutation_started,
            )
            rollback_status = "succeeded" if rollback_succeeded else "failed"
            if not rollback_succeeded:
                published_error = "automatic-rollback-failed"
        try:
            _put_status(
                s3_client,
                bucket=bucket,
                migration_id=args.migration_id,
                status="failed",
                phase=phase,
                error=published_error,
                rollback_status=rollback_status,
            )
        except Exception:
            print(
                json.dumps(
                    {
                        "phase": phase,
                        "status": "failed",
                        "error": "status-publish-failed",
                        "original_error": published_error,
                        "rollback_status": rollback_status,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        print(
            json.dumps(
                {
                    "phase": phase,
                    "status": "failed",
                    "error": published_error,
                    "rollback_status": rollback_status,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
