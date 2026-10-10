"""Structured records for safe OpenEMR import inspection and planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ArchiveLimits:
    """Limits applied before any archive content is accepted."""

    max_members: int = 100_000
    max_member_bytes: int = 2 * 1024**3
    max_expanded_bytes: int = 20 * 1024**3
    max_nested_archive_bytes: int = 4 * 1024**3
    max_compression_ratio: int = 200


@dataclass(frozen=True)
class SiteInventory:
    """Non-PHI aggregate facts for one OpenEMR site."""

    site_id: str
    has_sqlconf: bool
    has_documents: bool
    document_count: int
    has_encryption_keys: bool
    certificate_count: int
    edi_file_count: int
    executable_file_count: int


@dataclass(frozen=True)
class SourceInspection:
    """Redacted result of fully inspecting a local import source."""

    schema_version: int
    source_kind: str
    source_fingerprint: str
    source_bytes: int
    source_openemr_version: str | None
    source_database_version: int | None
    database_type: str
    sql_compressed: bool
    sql_bytes: int
    sites_archive_bytes: int
    expanded_site_bytes: int
    archive_member_count: int
    sites: tuple[SiteInventory, ...]
    ignored_application_file_count: int
    nested_archive_count: int
    custom_code_detected: bool
    unsupported_content: tuple[str, ...] = ()
    manual_review: tuple[str, ...] = ()
    checksums: dict[str, str] = field(default_factory=dict)
    upstream_reference: str = "openemr/openemr v8_2_0 commit 6125a2fd8089c8bcc3848071c1293c60e27a7585"

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-ready representation."""

        return asdict(self)


@dataclass(frozen=True)
class ImportPlan:
    """Redacted, non-executable plan for a fresh-target import."""

    schema_version: int
    migration_id: str
    created_at: str
    source_kind: str
    source_fingerprint: str
    checksums: dict[str, str]
    source_openemr_version: str
    source_database_version: int
    target_openemr_version: str
    target_mode: str
    site_ids: tuple[str, ...]
    phases: tuple[str, ...]
    preconditions: tuple[str, ...]
    rollback: tuple[str, ...]
    execution_allowed: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    configuration_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-ready representation."""

        return asdict(self)
