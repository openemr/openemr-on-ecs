"""Deterministic deployment-timing history and Markdown reporting."""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path
from typing import Any, Iterable

from tools._shared import ToolError, atomic_write_json, atomic_write_text, canonical_json, redact_text

from .models import SCHEMA_VERSION, PhaseTiming, ResidualResource, RunResult

_ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
_CREDENTIAL_LIKE = re.compile(r"(?i)(?:password|passwd|secret|token|access[_-]?key|private[_-]?key)\s*[:=]\s*[^,}\]]+")
_RESOURCE_IDENTIFIER = re.compile(r"(?i)\barn:|https?://|(?:[a-z0-9-]+\.){2,}[a-z]{2,}\b|(?:\d{1,3}\.){3}\d{1,3}")
_RUN_ID = re.compile(r"[a-z0-9][a-z0-9-]{5,47}")
_COMMIT = re.compile(r"[0-9a-f]{7,40}")
_ACCOUNT_HASH = re.compile(r"sha256:[0-9a-f]{12}")
_STATUSES = {"passed", "failed", "interrupted"}
_CLEANUP_STATUSES = {
    "complete",
    "stack-deleted-with-expected-residuals",
    "failed",
    "not-required",
    "retained-on-failure",
}


def empty_history() -> dict[str, Any]:
    """Return the initial history document."""

    return {"schema_version": SCHEMA_VERSION, "runs": []}


def load_history(path: Path) -> dict[str, Any]:
    """Load and validate a timing history, or return an empty one."""

    if not path.exists():
        return empty_history()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError(f"Cannot read timing history: {exc}") from exc
    return validate_history(value)


def validate_history(value: Any) -> dict[str, Any]:
    """Validate the committed schema and reject sensitive identifiers."""

    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ToolError(f"Timing history must use schema version {SCHEMA_VERSION}")
    runs = value.get("runs")
    if not isinstance(runs, list):
        raise ToolError("Timing history runs must be a list")

    normalized: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "runs": []}
    seen: set[str] = set()
    for item in runs:
        run = _validate_run(item)
        run_id = run["run_id"]
        if run_id in seen:
            raise ToolError(f"Duplicate timing run ID: {run_id}")
        seen.add(run_id)
        normalized["runs"].append(run)
    normalized["runs"].sort(key=lambda run: (run["started_at"], run["run_id"]))
    return normalized


def append_result(history_path: Path, report_path: Path, result: RunResult) -> None:
    """Append one idempotent result and regenerate the Markdown report."""

    history = load_history(history_path)
    candidate = _validate_run(result.to_dict())
    existing = next((run for run in history["runs"] if run["run_id"] == result.run_id), None)
    if existing is not None and canonical_json(existing) != canonical_json(candidate):
        raise ToolError(f"Run ID {result.run_id} already exists with different measurements")
    if existing is None:
        history["runs"].append(candidate)
    history = validate_history(history)
    atomic_write_json(history_path, history)
    atomic_write_text(report_path, render_markdown(history))


def regenerate_report(history_path: Path, report_path: Path) -> None:
    """Regenerate Markdown without changing historical data."""

    atomic_write_text(report_path, render_markdown(load_history(history_path)))


def update_cleanup_result(
    history_path: Path,
    report_path: Path,
    *,
    run_id: str,
    cleanup_status: str,
    residuals: tuple[ResidualResource, ...],
    phase: PhaseTiming,
    finished_at: str,
) -> bool:
    """Update a retained/failed run after an explicit cleanup-only retry."""

    history = load_history(history_path)
    index = next(
        (position for position, run in enumerate(history["runs"]) if run["run_id"] == run_id),
        None,
    )
    if index is None:
        return False
    candidate = dict(history["runs"][index])
    candidate["cleanup_status"] = cleanup_status
    candidate["residuals"] = [
        {
            "resource_type": item.resource_type,
            "identifier_hash": item.identifier_hash,
            "disposition": item.disposition,
        }
        for item in residuals
    ]
    candidate["phases"] = [item for item in candidate["phases"] if item["name"] != "cleanup-retry"]
    candidate["phases"].append(
        {
            "name": phase.name,
            "duration_seconds": phase.duration_seconds,
            "source": phase.source,
            "started_at": phase.started_at,
            "finished_at": phase.finished_at,
        }
    )
    candidate["finished_at"] = finished_at
    cleanup_succeeded = cleanup_status in {
        "complete",
        "not-required",
        "stack-deleted-with-expected-residuals",
    } and not any(item.disposition == "unexpected-residual" for item in residuals)
    if candidate.get("failure_phase") == "cleanup" and cleanup_succeeded:
        candidate["status"] = "passed"
        candidate["failure_phase"] = None
        candidate["notes"] = []
    history["runs"][index] = _validate_run(candidate)
    history = validate_history(history)
    atomic_write_json(history_path, history)
    atomic_write_text(report_path, render_markdown(history))
    return True


def render_markdown(history: dict[str, Any]) -> str:
    """Render a deterministic report and never invent a measurement."""

    history = validate_history(history)
    runs: list[dict[str, Any]] = history["runs"]
    lines = [
        "# Live E2E deployment timing",
        "",
        "This report is generated from sanitized records produced by the guarded local live-E2E runner.",
        "Durations are measurements, not estimates. Account IDs, resource ARNs, hostnames, and secrets are excluded.",
        "",
    ]
    if not runs:
        lines.extend(
            [
                "No live E2E deployment has been approved or measured yet.",
                "",
            ]
        )
    else:
        passed = [run for run in runs if run["status"] == "passed"]
        failed = [run for run in runs if run["status"] != "passed"]
        lines.extend(
            [
                "## Summary",
                "",
                f"- Recorded runs: {len(runs)}",
                f"- Successful runs: {len(passed)}",
                f"- Failed or interrupted runs: {len(failed)}",
                f"- Latest recorded run: {runs[-1]['started_at']} (`{runs[-1]['git_commit'][:12]}`)",
                "",
                "## Latest successful measurement",
                "",
            ]
        )
        if passed:
            latest = passed[-1]
            lines.extend(_latest_successful(latest))
        else:
            lines.extend(
                [
                    "No successful live E2E measurement has been recorded. Failed runs are listed below without",
                    "being treated as deployment-time benchmarks.",
                    "",
                ]
            )

        lines.extend(
            [
                "## Profile statistics",
                "",
                "Successful end-to-end totals are aggregated only within the same configuration profile.",
                "",
                "| Profile | Successful | Failed/interrupted | Minimum | Maximum | Median | Recent successful trend |",
                "|---|---:|---:|---:|---:|---:|---|",
            ]
        )
        for profile in sorted({run["profile"] for run in runs}):
            profile_runs = [run for run in runs if run["profile"] == profile]
            successful = [run for run in profile_runs if run["status"] == "passed"]
            totals = [duration for run in successful if (duration := _phase_duration(run, "total")) is not None]
            trend = " → ".join(_duration(value) for value in totals[-5:]) or "—"
            lines.append(
                "| {profile} | {passed} | {failed} | {minimum} | {maximum} | {median} | {trend} |".format(
                    profile=profile,
                    passed=len(successful),
                    failed=len(profile_runs) - len(successful),
                    minimum=_stat_duration(totals, min),
                    maximum=_stat_duration(totals, max),
                    median=_stat_duration(totals, statistics.median),
                    trend=trend,
                )
            )

        lines.extend(
            [
                "",
                "## Historical runs",
                "",
                "| Run | Started (UTC) | Commit | Region | Profile | Result | Total | Deploy incl. assets | ECS steady | "
                "HTTPS ready | Cleanup time | Cleanup result | Residuals |",
                "|---|---|---|---|---|---|---:|---:|---:|---:|---:|---|---:|",
            ]
        )
        for run in reversed(runs):
            lines.append(
                "| {run_id} | {started} | `{commit}` | {region} | {profile} | {status} | {total} | "
                "{deploy} | {ecs} | {ready} | {cleanup_time} | {cleanup} | {residuals} |".format(
                    run_id=run["run_id"],
                    started=run["started_at"],
                    commit=run["git_commit"][:12],
                    region=run["region"],
                    profile=run["profile"],
                    status=(
                        run["status"]
                        if run.get("failure_phase") is None
                        else f"{run['status']} ({run['failure_phase']})"
                    ),
                    total=_display_phase(run, "total"),
                    deploy=_display_first_phase(run, "deployment-with-assets", "cdk-deploy"),
                    ecs=_display_phase(run, "ecs-service-creation"),
                    ready=_display_phase(run, "application-https-ready"),
                    cleanup_time=_display_phase(run, "cleanup"),
                    cleanup=run["cleanup_status"],
                    residuals=_residual_count(run),
                )
            )

    lines.extend(
        [
            "",
            "## Methodology",
            "",
            "- A transparent Docker proxy records monotonic build durations without recording Docker arguments;",
            "  asset publication is the serialized CDK asset-pipeline duration minus measured Docker builds.",
            "- CDK deployment, post-deploy HTTPS wait, cleanup, and total durations use a local monotonic clock.",
            "- Time to application readiness spans the CloudFormation stack creation timestamp through the first",
            "  successful local HTTPS probe.",
            "- CloudFormation stack, Aurora, ElastiCache, EFS, and ECS durations come from AWS API event timestamps.",
            "- HTTPS readiness requires a successful TLS request with an HTTP 200 response containing OpenEMR.",
            "- Deployment validation also checks expected stack resources, ECS desired count, target health, Aurora,",
            "  and both EFS file systems.",
            "- Cleanup timing includes stack deletion, owned orphan Lambda log-group cleanup, retained-asset",
            "  inventory, and residual-resource inventory.",
            "",
            "## Comparability caveats",
            "",
            "Compare runs only when profile, Region, versions, and configuration fingerprint are compatible.",
            "Bootstrap asset caching, AWS control-plane load, DNS and certificate propagation, account quota usage,",
            "container-image caching, and file-asset packaging can materially affect durations. Failed and interrupted runs are not",
            "included in successful timing aggregates.",
            "",
            "## Source and reproduction",
            "",
            "- Machine-readable history: `e2e-results/history.json`",
            "- Runner guide: `docs/maintainers/live-e2e.md`",
            "- Regenerate this report without contacting AWS:",
            "",
            "```bash",
            "python -m tools.live_e2e report",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _validate_run(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolError("Each timing run must be an object")
    required_strings = (
        "run_id",
        "started_at",
        "finished_at",
        "git_commit",
        "branch",
        "repository",
        "account_hash",
        "region",
        "safe_stack_id",
        "profile",
        "configuration_fingerprint",
        "bootstrap_state",
        "python_version",
        "node_version",
        "cdk_cli_version",
        "cdk_library_version",
        "cdk_assets_version",
        "openemr_version",
        "aurora_version",
        "test_runner_version",
        "status",
        "stack_status",
        "cleanup_status",
    )
    for key in required_strings:
        if not isinstance(value.get(key), str) or not value[key]:
            raise ToolError(f"Timing run {key} must be a non-empty string")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ToolError(f"Timing run must use schema version {SCHEMA_VERSION}")
    if not _RUN_ID.fullmatch(value["run_id"]):
        raise ToolError("Timing run ID has an invalid format")
    if not _COMMIT.fullmatch(value["git_commit"]):
        raise ToolError("Timing git commit must be a hexadecimal commit ID")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", value["branch"]):
        raise ToolError("Timing branch has an invalid format")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"]):
        raise ToolError("Timing repository has an invalid format")
    if not _ACCOUNT_HASH.fullmatch(value["account_hash"]):
        raise ToolError("Timing account must be a one-way account hash")
    if not _ACCOUNT_HASH.fullmatch(value["safe_stack_id"]):
        raise ToolError("Timing stack identifier must be a one-way hash")
    if not re.fullmatch(r"sha256:[0-9a-f]{16}", value["configuration_fingerprint"]):
        raise ToolError("Timing configuration fingerprint is invalid")
    if value["status"] not in _STATUSES:
        raise ToolError(f"Unsupported run status: {value['status']}")
    if value["cleanup_status"] not in _CLEANUP_STATUSES:
        raise ToolError(f"Unsupported cleanup status: {value['cleanup_status']}")

    normalized = dict(value)
    normalized["phases"] = _validate_records(value.get("phases"), "phases", _validate_phase)
    normalized["checks"] = _validate_records(value.get("checks", []), "checks", _validate_check)
    normalized["residuals"] = _validate_records(value.get("residuals", []), "residuals", _validate_residual)
    normalized["notes"] = _strings(value.get("notes", []), "notes")
    failure_phase = value.get("failure_phase")
    if failure_phase is not None and (not isinstance(failure_phase, str) or not failure_phase):
        raise ToolError("Timing failure phase must be null or a non-empty string")
    if not isinstance(value.get("metadata", {}), dict):
        raise ToolError("Timing metadata must be an object")
    normalized["metadata"] = value.get("metadata", {})

    scan_value = dict(normalized)
    for hash_field in (
        "account_hash",
        "configuration_fingerprint",
        "git_commit",
        "safe_stack_id",
    ):
        scan_value[hash_field] = "<validated-hash>"
    serialized = canonical_json(scan_value)
    if (
        _ACCOUNT_ID.search(serialized)
        or _CREDENTIAL_LIKE.search(serialized)
        or _RESOURCE_IDENTIFIER.search(serialized)
        or redact_text(serialized) != serialized
    ):
        raise ToolError("Timing history contains a raw account, resource, host, network, or credential identifier")
    return normalized


def _validate_records(value: Any, name: str, validator: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise ToolError(f"Timing {name} must be a list")
    return [validator(item) for item in value]


def _validate_phase(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolError("Timing phase must be an object")
    if not isinstance(value.get("name"), str) or not value["name"]:
        raise ToolError("Timing phase name is required")
    duration = value.get("duration_seconds")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
        raise ToolError("Timing phase duration must be non-negative")
    if not isinstance(value.get("source"), str) or not value["source"]:
        raise ToolError("Timing phase source is required")
    result = dict(value)
    result["duration_seconds"] = round(float(duration), 3)
    return result


def _validate_check(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolError("Timing check must be an object")
    for key in ("name", "status", "detail"):
        if not isinstance(value.get(key), str):
            raise ToolError(f"Timing check {key} must be a string")
    return dict(value)


def _validate_residual(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolError("Residual resource must be an object")
    for key in ("resource_type", "identifier_hash", "disposition"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ToolError(f"Residual resource {key} must be a non-empty string")
    return dict(value)


def _strings(value: Any, name: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise ToolError(f"Timing {name} must contain only strings")
    return list(value)


def _latest_successful(run: dict[str, Any]) -> list[str]:
    return [
        f"- Run: `{run['run_id']}` at {run['started_at']}",
        f"- Source: `{run['repository']}` branch `{run['branch']}` commit `{run['git_commit']}`",
        f"- Configuration: `{run['profile']}` in `{run['region']}` (`{run['configuration_fingerprint']}`)",
        f"- Safe stack identifier: `{run['safe_stack_id']}`; bootstrap: `{run['bootstrap_state']}`",
        f"- Total E2E time: {_display_phase(run, 'total')}",
        f"- Total deployment time (including assets): "
        f"{_display_first_phase(run, 'deployment-with-assets', 'cdk-deploy')}",
        f"- ECS service creation time: {_display_phase(run, 'ecs-service-creation')}",
        f"- Time to application readiness: {_display_phase(run, 'application-https-ready')}",
        f"- Cleanup time: {_display_phase(run, 'cleanup')} ({run['cleanup_status']})",
        f"- Versions: Python `{run['python_version']}`, Node.js `{run['node_version']}`, "
        f"CDK CLI `{run['cdk_cli_version']}`, CDK library `{run['cdk_library_version']}`, "
        f"CDK assets `{run['cdk_assets_version']}`, "
        f"OpenEMR `{run['openemr_version']}`, Aurora `{run['aurora_version']}`, "
        f"runner `{run['test_runner_version']}`",
        f"- Residual resources: {_residual_count(run)}",
        "",
    ]


def _stat_duration(values: list[float], operation: Any) -> str:
    if not values:
        return "—"
    return _duration(float(operation(values)))


def _phase_duration(run: dict[str, Any], name: str) -> float | None:
    for phase in run["phases"]:
        if phase["name"] == name:
            return float(phase["duration_seconds"])
    return None


def _display_phase(run: dict[str, Any], name: str) -> str:
    value = _phase_duration(run, name)
    return "—" if value is None else _duration(value)


def _display_first_phase(run: dict[str, Any], *names: str) -> str:
    for name in names:
        value = _phase_duration(run, name)
        if value is not None:
            return _duration(value)
    return "—"


def _residual_count(run: dict[str, Any]) -> str:
    if run["cleanup_status"] == "failed":
        return "unknown"
    return str(len(run["residuals"]))


def _duration(seconds: float) -> str:
    rounded = int(round(seconds))
    minutes, remaining = divmod(rounded, 60)
    if minutes:
        return f"{minutes}m {remaining:02d}s"
    return f"{remaining}s"


def phase_names(runs: Iterable[dict[str, Any]]) -> set[str]:
    """Return measured phase names (primarily useful to report tests)."""

    return {phase["name"] for run in runs for phase in run["phases"]}
