"""Human, JSON-adjacent, and Markdown rendering for version-audit reports."""

from __future__ import annotations

from collections import defaultdict

from tools._shared import redact_text

from .models import AuditReport, Finding, Status

_STATUS_LABELS = {
    Status.CURRENT: "Current",
    Status.STABLE_UPDATE: "Stable update available",
    Status.PRERELEASE_ONLY: "Prerelease-only update",
    Status.UNABLE: "Unable to determine",
    Status.INCOMPATIBLE: "Incompatible",
    Status.DEFERRED: "Deferred",
    Status.MANUAL_REVIEW: "Manual review required",
}


def _escape(value: object | None) -> str:
    if value is None or value == "":
        return "—"
    return redact_text(str(value)).replace("|", "\\|").replace("\n", " ")


def render_human(report: AuditReport) -> str:
    """Render compact terminal output."""

    grouped: dict[str, list[Finding]] = defaultdict(list)
    for finding in report.findings:
        grouped[finding.category].append(finding)
    lines = [
        "OpenEMR on ECS version audit",
        f"Generated: {report.generated_at}",
        "",
    ]
    for category in sorted(grouped):
        lines.append(f"[{category}]")
        for finding in grouped[category]:
            latest = finding.latest or "unknown"
            lines.append(f"- {finding.name}: {finding.current} -> {latest} ({_STATUS_LABELS[finding.status]})")
            if finding.error:
                lines.append(f"  source error: {finding.error}")
            elif finding.note:
                lines.append(f"  note: {finding.note}")
        lines.append("")
    summary = report.summary()
    lines.extend(
        [
            (
                f"Total: {summary['total']} | actionable: "
                f"{summary['status_counts'][Status.STABLE_UPDATE.value] + summary['status_counts'][Status.MANUAL_REVIEW.value]}"
            ),
            f"Partial source failure: {'yes' if report.partial_failure else 'no'}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(report: AuditReport) -> str:
    """Render a deterministic Markdown report suitable for CI artifacts and issues."""

    summary = report.summary()
    lines = [
        "# Dependency and Platform Version Audit",
        "",
        f"- Generated: `{report.generated_at}`",
        f"- Components inspected: **{summary['total']}**",
        f"- Actionable findings: **{'yes' if report.updates_found else 'no'}**",
        f"- Partial source failure: **{'yes' if report.partial_failure else 'no'}**",
        "",
        "| Category | Component | Declared | Latest stable | Status | Definition |",
        "|---|---|---:|---:|---|---|",
    ]
    for finding in report.findings:
        lines.append(
            "| "
            + " | ".join(
                (
                    _escape(finding.category),
                    _escape(finding.name),
                    _escape(finding.current),
                    _escape(finding.latest),
                    _escape(_STATUS_LABELS[finding.status]),
                    _escape(finding.definition),
                )
            )
            + " |"
        )
    lines.extend(["", "## Details", ""])
    for finding in report.findings:
        lines.extend(
            [
                f"### {finding.name}",
                "",
                f"- Identifier: `{finding.identifier}`",
                f"- Source kind: `{finding.source_kind}`",
                f"- Source: {_escape(finding.source_url)}",
                f"- Constraint: `{_escape(finding.constraint)}`",
                f"- Consumers: {_escape(', '.join(finding.consumers))}",
                f"- Note: {_escape(finding.note)}",
                f"- Error: {_escape(finding.error)}",
                "",
            ]
        )
    lines.extend(
        [
            "## Classification policy",
            "",
            "- Installed package state is never used as the declared current version.",
            "- Stable releases exclude alpha, beta, release-candidate, dev, nightly, and preview builds.",
            "- A source failure affects only that component and is reported explicitly.",
            "- Aurora values exposed by CDK still require regional and workload compatibility review.",
            "- OpenEMR results require an ARM64 image tag matching an official stable GitHub release.",
            "",
        ]
    )
    return "\n".join(lines)
