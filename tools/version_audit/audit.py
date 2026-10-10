"""Version-audit orchestration and classification."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from tools._shared import utc_now

from .inventory import collect_declarations
from .models import AuditReport, Declaration, Finding, Status
from .sources import HttpClient, Resolution, SourceError, VersionSources


def _parse_version(value: str) -> Version:
    normalized = value.strip()
    normalized = re.sub(r"^(?:emr-|go|v)", "", normalized)
    return Version(normalized)


def _compatible_alias(current: str, latest: str) -> bool:
    """Return whether a broad major/minor declaration already accepts latest."""

    raw = current.strip().removeprefix("v")
    if not re.fullmatch(r"\d+(?:\.\d+)?", raw):
        return False
    try:
        latest_version = _parse_version(latest)
    except InvalidVersion:
        return False
    parts = tuple(int(part) for part in raw.split("."))
    if len(parts) == 1:
        return latest_version.major == parts[0]
    return latest_version.release[:2] == parts


def _classify(
    declaration: Declaration,
    resolution: Resolution,
) -> tuple[Status, str | None]:
    if declaration.metadata.get("conflicting_exact_pins") or declaration.metadata.get("conflicting_pins"):
        return Status.MANUAL_REVIEW, "The same component has conflicting repository declarations"
    if resolution.current_reference_verified is False:
        return Status.MANUAL_REVIEW, resolution.note
    if resolution.latest is None:
        return Status.UNABLE, resolution.note
    current = declaration.current
    latest = resolution.latest
    action_is_mutably_pinned = declaration.category in {
        "github-actions",
        "pre-commit",
    } and not declaration.metadata.get("immutable_sha_pins", False)
    if action_is_mutably_pinned:
        return Status.MANUAL_REVIEW, "GitHub dependency is not pinned to an immutable commit SHA"

    if declaration.source_kind == "pypi" and declaration.constraint and " / " not in declaration.constraint:
        try:
            specifier = SpecifierSet(declaration.constraint)
            latest_version = Version(latest)
            if declaration.constraint and latest_version in specifier:
                exact_pin = any(item.operator in {"==", "==="} and "*" not in item.version for item in specifier)
                if not exact_pin:
                    return Status.CURRENT, "The declared range already accepts the latest stable release"
        except InvalidSpecifier, InvalidVersion:
            return Status.MANUAL_REVIEW, "The declared Python constraint could not be compared safely"

    tracks_release_line = declaration.source_kind in {
        "go-toolchain",
        "node-toolchain",
        "python-toolchain",
    }
    if tracks_release_line and _compatible_alias(current, latest):
        return Status.CURRENT, "The major/minor declaration already tracks this stable release line"

    try:
        current_version = _parse_version(current)
        latest_version = _parse_version(latest)
    except InvalidVersion:
        if current == latest:
            return Status.CURRENT, resolution.note
        return Status.MANUAL_REVIEW, "Non-semantic declaration requires manual compatibility review"

    if current_version == latest_version:
        return Status.CURRENT, resolution.note
    if current_version > latest_version:
        return Status.MANUAL_REVIEW, "Declared version is newer than the source's latest stable result"

    if declaration.source_kind == "aws-cdk-aurora":
        return Status.MANUAL_REVIEW, resolution.note
    return Status.STABLE_UPDATE, resolution.note


def _load_deferrals(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "tools" / "version_audit" / "deferrals.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("version-audit deferrals must be a JSON object")
    allowed_statuses = {
        Status.DEFERRED.value,
        Status.INCOMPATIBLE.value,
        Status.MANUAL_REVIEW.value,
    }
    active: dict[str, dict[str, Any]] = {}
    today = datetime.now(UTC).date()
    for identifier, value in data.items():
        if not isinstance(identifier, str) or not identifier or not isinstance(value, dict):
            raise ValueError("each version-audit deferral must be a named JSON object")
        unknown = set(value) - {"status", "reason", "prerequisite", "review_date"}
        if unknown:
            raise ValueError(f"deferral {identifier} has unknown fields: {', '.join(sorted(unknown))}")
        status = value.get("status")
        reason = value.get("reason")
        review_date = value.get("review_date")
        prerequisite = value.get("prerequisite", "")
        if status not in allowed_statuses:
            raise ValueError(f"deferral {identifier} has an invalid status")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"deferral {identifier} requires a nonempty reason")
        if not isinstance(prerequisite, str):
            raise ValueError(f"deferral {identifier} prerequisite must be text")
        if not isinstance(review_date, str):
            raise ValueError(f"deferral {identifier} requires an ISO review_date")
        try:
            parsed_review_date = date.fromisoformat(review_date)
        except ValueError as exc:
            raise ValueError(f"deferral {identifier} has an invalid review_date") from exc
        if parsed_review_date <= today:
            continue
        active[identifier] = {
            "status": status,
            "reason": reason.strip(),
            "prerequisite": prerequisite.strip(),
            "review_date": review_date,
        }
    return active


def _apply_deferral(
    declaration: Declaration,
    finding: Finding,
    deferrals: dict[str, dict[str, Any]],
) -> Finding:
    deferral = deferrals.get(declaration.identifier)
    if not deferral or finding.status not in {Status.STABLE_UPDATE, Status.MANUAL_REVIEW}:
        return finding
    status_name = str(deferral.get("status", "deferred"))
    try:
        status = Status(status_name)
    except ValueError:
        status = Status.DEFERRED
    if status not in {Status.DEFERRED, Status.INCOMPATIBLE, Status.MANUAL_REVIEW}:
        status = Status.DEFERRED
    reason = str(deferral.get("reason", "")).strip()
    prerequisite = str(deferral.get("prerequisite", "")).strip()
    review_date = str(deferral.get("review_date", "")).strip()
    note_parts = [part for part in (reason, prerequisite, f"Review by {review_date}" if review_date else "") if part]
    return Finding(
        identifier=finding.identifier,
        name=finding.name,
        category=finding.category,
        current=finding.current,
        latest=finding.latest,
        status=status,
        definition=finding.definition,
        source_kind=finding.source_kind,
        source_url=finding.source_url,
        consumers=finding.consumers,
        constraint=finding.constraint,
        note="; ".join(note_parts) or finding.note,
        error=finding.error,
    )


def _finding_from_error(declaration: Declaration, error: str, *, offline: bool) -> Finding:
    return Finding(
        identifier=declaration.identifier,
        name=declaration.name,
        category=declaration.category,
        current=declaration.current,
        latest=None,
        status=Status.UNABLE,
        definition=declaration.definition,
        source_kind=declaration.source_kind,
        source_url=None,
        consumers=declaration.consumers,
        constraint=declaration.constraint,
        note="Network lookup disabled" if offline else None,
        error=error,
    )


def run_audit(
    root: Path,
    *,
    categories: Iterable[str] | None = None,
    timeout_seconds: float = 12.0,
    online: bool = True,
    generated_at: str | None = None,
) -> AuditReport:
    """Discover declarations, resolve sources independently, and return a report."""

    selected = tuple(sorted(set(categories or ())))
    selected_set = set(selected)
    declarations = [
        declaration
        for declaration in collect_declarations(root)
        if not selected_set or declaration.category in selected_set
    ]
    deferrals = _load_deferrals(root)
    sources = VersionSources(HttpClient(timeout_seconds=timeout_seconds)) if online else None
    findings: list[Finding] = []
    for declaration in declarations:
        if not online or sources is None:
            findings.append(
                _finding_from_error(
                    declaration,
                    "External version sources are disabled for this audit",
                    offline=True,
                )
            )
            continue
        try:
            resolution = sources.resolve(declaration)
            status, classification_note = _classify(declaration, resolution)
            note = classification_note or resolution.note
            if status is Status.CURRENT and resolution.latest_prerelease and resolution.latest:
                try:
                    if _parse_version(resolution.latest_prerelease) > _parse_version(resolution.latest):
                        status = Status.PRERELEASE_ONLY
                        note = f"Newer prerelease exists: {resolution.latest_prerelease}"
                except InvalidVersion:
                    # Keep CURRENT when prerelease/latest strings are not PEP 440-comparable.
                    pass
            finding = Finding(
                identifier=declaration.identifier,
                name=declaration.name,
                category=declaration.category,
                current=declaration.current,
                latest=resolution.latest,
                status=status,
                definition=declaration.definition,
                source_kind=declaration.source_kind,
                source_url=resolution.source_url,
                consumers=declaration.consumers,
                constraint=declaration.constraint,
                note=note,
            )
            findings.append(_apply_deferral(declaration, finding, deferrals))
        except SourceError as exc:
            findings.append(_finding_from_error(declaration, str(exc), offline=False))
    findings.sort(key=lambda item: (item.category, item.name.lower(), item.identifier))
    available_categories = tuple(sorted({declaration.category for declaration in declarations}))
    return AuditReport(
        generated_at=generated_at or utc_now(),
        repository_root=".",
        findings=tuple(findings),
        selected_categories=selected or available_categories,
    )
