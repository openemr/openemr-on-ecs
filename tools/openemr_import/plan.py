"""Compatibility policy and deterministic fresh-target import planning."""

from __future__ import annotations

from typing import Any

from packaging.version import InvalidVersion, Version

from openemr_ecs.constants import StackConstants
from tools._shared import ToolError, fingerprint, utc_now

from .models import (
    SCHEMA_VERSION,
    ImportPlan,
    SiteInventory,
    SourceInspection,
)

TARGET_OPENEMR_VERSION = StackConstants.OPENEMR_VERSION


def _string(data: dict[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _boolean(data: dict[str, Any], key: str) -> bool:
    value = data[key]
    if type(value) is not bool:
        raise TypeError(f"{key} must be a boolean")
    return value


def _nonnegative_integer(data: dict[str, Any], key: str) -> int:
    value = data[key]
    if type(value) is not int or value < 0:
        raise TypeError(f"{key} must be a nonnegative integer")
    return value


def _strings(data: dict[str, Any], key: str) -> tuple[str, ...]:
    value = data[key]
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"{key} must be an array of strings")
    return tuple(value)


def _checksums(data: dict[str, Any], key: str = "checksums") -> dict[str, str]:
    value = data[key]
    if not isinstance(value, dict) or not all(
        isinstance(name, str) and isinstance(digest, str) for name, digest in value.items()
    ):
        raise TypeError(f"{key} must be an object of string checksums")
    return dict(value)


def inspection_from_dict(data: dict[str, Any]) -> SourceInspection:
    """Validate and rebuild a SourceInspection from JSON data."""

    if data.get("schema_version") != SCHEMA_VERSION:
        raise ToolError("Unsupported source inspection schema version")
    try:
        site_data = data["sites"]
        if not isinstance(site_data, (list, tuple)):
            raise TypeError("sites must be an array")
        sites = tuple(
            SiteInventory(
                site_id=_string(item, "site_id"),
                has_sqlconf=_boolean(item, "has_sqlconf"),
                has_documents=_boolean(item, "has_documents"),
                document_count=_nonnegative_integer(item, "document_count"),
                has_encryption_keys=_boolean(item, "has_encryption_keys"),
                certificate_count=_nonnegative_integer(item, "certificate_count"),
                edi_file_count=_nonnegative_integer(item, "edi_file_count"),
                executable_file_count=_nonnegative_integer(item, "executable_file_count"),
            )
            for item in site_data
            if isinstance(item, dict)
        )
        if len(sites) != len(site_data):
            raise TypeError("sites entries must be objects")
        source_version = data.get("source_openemr_version")
        if source_version is not None and not isinstance(source_version, str):
            raise TypeError("source_openemr_version must be a string or null")
        upstream_reference = data.get(
            "upstream_reference",
            SourceInspection.__dataclass_fields__["upstream_reference"].default,
        )
        if not isinstance(upstream_reference, str):
            raise TypeError("upstream_reference must be a string")
        return SourceInspection(
            schema_version=data["schema_version"],
            source_kind=_string(data, "source_kind"),
            source_fingerprint=_string(data, "source_fingerprint"),
            source_bytes=_nonnegative_integer(data, "source_bytes"),
            source_openemr_version=source_version,
            source_database_version=(
                _nonnegative_integer(data, "source_database_version")
                if data.get("source_database_version") is not None
                else None
            ),
            database_type=_string(data, "database_type"),
            sql_compressed=_boolean(data, "sql_compressed"),
            sql_bytes=_nonnegative_integer(data, "sql_bytes"),
            sites_archive_bytes=_nonnegative_integer(data, "sites_archive_bytes"),
            expanded_site_bytes=_nonnegative_integer(data, "expanded_site_bytes"),
            archive_member_count=_nonnegative_integer(data, "archive_member_count"),
            sites=sites,
            ignored_application_file_count=_nonnegative_integer(data, "ignored_application_file_count"),
            nested_archive_count=_nonnegative_integer(data, "nested_archive_count"),
            custom_code_detected=_boolean(data, "custom_code_detected"),
            unsupported_content=_strings(data, "unsupported_content"),
            manual_review=_strings(data, "manual_review"),
            checksums=_checksums(data),
            upstream_reference=upstream_reference,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError("Source inspection JSON is incomplete or malformed") from exc


def plan_from_dict(data: dict[str, Any]) -> ImportPlan:
    """Validate and rebuild an ImportPlan from JSON data."""

    if data.get("schema_version") != SCHEMA_VERSION:
        raise ToolError("Unsupported import plan schema version")
    try:
        return ImportPlan(
            schema_version=data["schema_version"],
            migration_id=_string(data, "migration_id"),
            created_at=_string(data, "created_at"),
            source_kind=_string(data, "source_kind"),
            source_fingerprint=_string(data, "source_fingerprint"),
            checksums=_checksums(data),
            source_openemr_version=_string(data, "source_openemr_version"),
            source_database_version=_nonnegative_integer(data, "source_database_version"),
            target_openemr_version=_string(data, "target_openemr_version"),
            target_mode=_string(data, "target_mode"),
            site_ids=_strings(data, "site_ids"),
            phases=_strings(data, "phases"),
            preconditions=_strings(data, "preconditions"),
            rollback=_strings(data, "rollback"),
            execution_allowed=_boolean(data, "execution_allowed"),
            blockers=_strings(data, "blockers"),
            warnings=_strings(data, "warnings"),
            configuration_fingerprint=_string(data, "configuration_fingerprint"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError("Import plan JSON is incomplete or malformed") from exc


def create_plan(
    inspection: SourceInspection,
    *,
    target_version: str = TARGET_OPENEMR_VERSION,
    generated_at: str | None = None,
) -> ImportPlan:
    """Create a non-mutating plan under the conservative first-release policy."""

    blockers = list(inspection.unsupported_content)
    warnings = list(inspection.manual_review)
    if inspection.source_openemr_version is None:
        blockers.append("Source version must be known before execution")
        source_version = "unknown"
    else:
        source_version = inspection.source_openemr_version
        try:
            source = Version(source_version)
            target = Version(target_version)
        except InvalidVersion as exc:
            raise ToolError("Source or target OpenEMR version is invalid") from exc
        if source > target:
            blockers.append("Source OpenEMR version is newer than the target")
        elif source < target:
            blockers.append(
                "First-release execution requires a same-version source; upgrade the "
                "source with OpenEMR's supported upgrade path before importing"
            )
    if inspection.source_database_version is None:
        blockers.append("Source database schema version must be verified before execution")
        source_database_version = 0
    else:
        source_database_version = inspection.source_database_version

    site_ids = tuple(site.site_id for site in inspection.sites)
    if site_ids != ("default",):
        blockers.append(
            "First-release execution supports exactly one site named 'default'; multisite remains inspect-and-plan only"
        )
    if inspection.custom_code_detected:
        blockers.append("Custom executable code is never copied automatically; review and port it separately")
    if any(not site.has_encryption_keys for site in inspection.sites):
        blockers.append("Both OpenEMR document-encryption key files are required for automatic execution")
    if inspection.source_kind != "native-openemr-backup":
        blockers.append(
            "Execution currently requires one native OpenEMR backup archive; "
            "directory and manifest sources are inspection-only"
        )

    configuration = {
        "source": inspection.source_fingerprint,
        "source_database_version": source_database_version,
        "target": target_version,
        "mode": "fresh-target-only",
        "sites": site_ids,
    }
    configuration_fingerprint = fingerprint(configuration, length=32)
    created_at = generated_at or utc_now()
    migration_fingerprint = fingerprint(
        {
            "configuration_fingerprint": configuration_fingerprint,
            "created_at": created_at,
        },
        length=32,
    )
    migration_id = f"import-{migration_fingerprint[:16]}"
    return ImportPlan(
        schema_version=SCHEMA_VERSION,
        migration_id=migration_id,
        created_at=created_at,
        source_kind=inspection.source_kind,
        source_fingerprint=inspection.source_fingerprint,
        checksums=dict(inspection.checksums),
        source_openemr_version=source_version,
        source_database_version=source_database_version,
        target_openemr_version=target_version,
        target_mode="fresh-target-only",
        site_ids=site_ids,
        phases=(
            "reinspect-and-verify-checksums",
            "verify-account-region-stack-and-empty-target",
            "verify-current-aurora-and-efs-recovery-points",
            "stage-with-tls-and-customer-managed-kms",
            "quiesce-openemr-service",
            "run-isolated-private-migration-task",
            "validate-database-sites-and-encryption-keys",
            "restart-and-validate-openemr",
            "delete-staging-object",
        ),
        preconditions=(
            "The target stack is a fresh non-production deployment",
            "AWS account and region match explicit approved inputs",
            "The target OpenEMR version exactly matches the source",
            "The application can be made unavailable for the entire import",
            "A verified recovery point exists before data mutation",
            "No unsupported custom executable code is imported",
        ),
        rollback=(
            "Keep the ECS service stopped after any partial mutation",
            "Restore the verified pre-import Aurora and EFS recovery points",
            "Validate restored database, files, and application health",
            "Delete only staging data associated with this migration ID",
        ),
        execution_allowed=not blockers,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
        configuration_fingerprint=configuration_fingerprint,
    )
