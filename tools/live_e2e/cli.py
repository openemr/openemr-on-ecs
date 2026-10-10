"""Command-line interface for the guarded live E2E runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from tools._shared import ToolError, repository_root

from .progress import ProgressReporter
from .runner import (
    ACCOUNT_CONFIRMATION,
    CREATE_CONFIRMATION,
    DESTROY_CONFIRMATION,
    KEEP_CONFIRMATION,
    ZONE_CONFIRMATION,
    LiveE2ERunner,
    profiles,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the maintained CLI contract."""

    parser = argparse.ArgumentParser(
        prog="python -m tools.live_e2e",
        description="Guarded local-only deployment, validation, teardown, and timing.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="Run local and read-only AWS prerequisites and plan")
    _preflight_arguments(preflight)
    plan = subparsers.add_parser("plan", help="Alias for preflight plus template-only CDK diff")
    _preflight_arguments(plan)

    run = subparsers.add_parser("run", help="Perform one explicitly approved live E2E run")
    run.add_argument("--preflight", type=Path, required=True)
    run.add_argument("--approved-account", required=True)
    run.add_argument("--confirm-create", required=True, help=f"Must equal: {CREATE_CONFIRMATION}")
    run.add_argument("--confirm-destroy", required=True, help=f"Must equal: {DESTROY_CONFIRMATION}")
    run.add_argument("--confirm-costs", action="store_true")
    run.add_argument("--keep-on-failure", action="store_true")
    run.add_argument(
        "--confirm-keep-on-failure",
        help=f"Required with --keep-on-failure; must equal: {KEEP_CONFIRMATION}",
    )
    run.add_argument("--deploy-timeout-seconds", type=float, default=90 * 60)
    run.add_argument("--readiness-timeout-seconds", type=float, default=30 * 60)
    run.add_argument("--cleanup-timeout-seconds", type=float, default=60 * 60)
    run.add_argument("--poll-seconds", type=float, default=20)
    run.add_argument("--noninteractive", action="store_true")
    run.add_argument("--json", action="store_true")
    run.add_argument("--verbose", action="store_true")

    cleanup = subparsers.add_parser("cleanup", help="Retry cleanup for an interrupted owned run")
    _scope_arguments(cleanup, include_dns=False)
    cleanup.add_argument("--run-id", required=True)
    cleanup.add_argument("--confirm-destroy", required=True, help=f"Must equal: {DESTROY_CONFIRMATION}")
    cleanup.add_argument("--timeout-seconds", type=float, default=60 * 60)
    cleanup.add_argument("--poll-seconds", type=float, default=20)
    cleanup.add_argument("--noninteractive", action="store_true")
    cleanup.add_argument("--json", action="store_true")
    cleanup.add_argument("--verbose", action="store_true")

    report = subparsers.add_parser("report", help="Regenerate the deterministic timing report")
    report.add_argument("--json", action="store_true")
    profile_parser = subparsers.add_parser("profiles", help="List maintained deployment profiles")
    profile_parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""

    args = build_parser().parse_args(argv)
    root = repository_root()
    progress = ProgressReporter(
        enabled=True,
        verbose=bool(getattr(args, "verbose", False)),
    )
    runner = LiveE2ERunner(root=root, progress=progress)
    try:
        if args.command in {"preflight", "plan"}:
            path = runner.preflight(
                approved_account=args.approved_account,
                region=args.region,
                route53_domain=args.route53_domain,
                allowed_ipv4_cidr=args.allowed_ipv4_cidr,
                profile=args.profile,
                aws_profile=args.aws_profile,
                cdk_command=args.cdk_command,
                bootstrap_stack_name=args.bootstrap_stack_name,
                confirm_dedicated_zone=args.confirm_dedicated_zone,
                confirm_non_production_account=args.confirm_non_production_account,
                run_id=args.run_id,
                require_tty=not args.noninteractive,
            )
            record = json.loads(path.read_text(encoding="utf-8"))
            if args.json:
                print(
                    json.dumps(
                        {
                            "status": "passed",
                            "run_id": record["run_id"],
                            "preflight_path": path.relative_to(root).as_posix(),
                            "resource_count": record["resource_count"],
                        },
                        sort_keys=True,
                    )
                )
            else:
                print(f"Preflight passed for run {record['run_id']}.")
                print(f"Owner-only approval file: {path.relative_to(root)}")
                print("No application stack or other AWS resource was created.")
                if args.verbose:
                    print(
                        f"Validated {len(record['checks'])} checks and "
                        f"{record['resource_count']} synthesized resources."
                    )
            return 0
        if args.command == "run":
            result = runner.run(
                preflight_path=args.preflight,
                approved_account=args.approved_account,
                confirm_create=args.confirm_create,
                confirm_destroy=args.confirm_destroy,
                confirm_costs=args.confirm_costs,
                keep_on_failure=args.keep_on_failure,
                confirm_keep_on_failure=args.confirm_keep_on_failure,
                deploy_timeout_seconds=_positive(args.deploy_timeout_seconds, "deploy timeout"),
                readiness_timeout_seconds=_positive(args.readiness_timeout_seconds, "readiness timeout"),
                cleanup_timeout_seconds=_positive(args.cleanup_timeout_seconds, "cleanup timeout"),
                poll_seconds=_positive(args.poll_seconds, "poll interval"),
                require_tty=not args.noninteractive,
            )
            if args.json:
                print(json.dumps(result.to_dict(), sort_keys=True))
                return 0 if result.status == "passed" else 1
            print(
                f"Run {result.run_id}: {result.status}; cleanup={result.cleanup_status}; "
                f"residuals={len(result.residuals)}"
            )
            durations = {phase.name: phase.duration_seconds for phase in result.phases}
            print(
                "Timing (seconds): "
                f"total={_timing(durations, 'total')}; "
                f"deploy={_timing_first(durations, 'deployment-with-assets', 'cdk-deploy')}; "
                f"ecs-create={_timing(durations, 'ecs-service-creation')}; "
                f"https-ready={_timing(durations, 'application-https-ready')}; "
                f"cleanup={_timing(durations, 'cleanup')}"
            )
            if args.verbose:
                print(f"Owner-only diagnostics: .live-e2e/runs/{result.run_id}/")
            return 0 if result.status == "passed" else 1
        if args.command == "cleanup":
            status, residual_count = runner.cleanup(
                run_id=args.run_id,
                approved_account=args.approved_account,
                region=args.region,
                aws_profile=args.aws_profile,
                confirm_destroy=args.confirm_destroy,
                timeout_seconds=_positive(args.timeout_seconds, "cleanup timeout"),
                poll_seconds=_positive(args.poll_seconds, "poll interval"),
                require_tty=not args.noninteractive,
            )
            if args.json:
                print(
                    json.dumps(
                        {
                            "run_id": args.run_id,
                            "cleanup_status": status,
                            "residual_count": residual_count,
                        },
                        sort_keys=True,
                    )
                )
            else:
                print(f"Cleanup {args.run_id}: {status}; residuals={residual_count}")
                if args.verbose:
                    print(f"Owner-only diagnostics: .live-e2e/runs/{args.run_id}/")
            return (
                0
                if status
                in {
                    "complete",
                    "stack-deleted-with-expected-residuals",
                    "not-required",
                }
                else 1
            )
        if args.command == "report":
            runner.regenerate_report()
            if args.json:
                print((root / "e2e-results" / "history.json").read_text(encoding="utf-8").rstrip())
            else:
                print("Regenerated docs/maintainers/deployment-timing.md.")
            return 0
        if args.command == "profiles":
            print(json.dumps({"profiles": profiles()}) if args.json else "\n".join(profiles()))
            return 0
    except (ToolError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    raise AssertionError("unreachable")


def _preflight_arguments(parser: argparse.ArgumentParser) -> None:
    _scope_arguments(parser, include_dns=True)
    parser.add_argument("--profile", choices=profiles(), default="default")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--cdk-command",
        default="node_modules/.bin/cdk",
        help="Pinned local CDK only; install it with npm ci",
    )
    parser.add_argument("--bootstrap-stack-name", default="CDKToolkit")
    parser.add_argument(
        "--confirm-dedicated-zone",
        required=True,
        help=f"Must equal: {ZONE_CONFIRMATION}",
    )
    parser.add_argument(
        "--confirm-non-production-account",
        required=True,
        help=f"Must equal: {ACCOUNT_CONFIRMATION}",
    )
    parser.add_argument("--noninteractive", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true")


def _scope_arguments(parser: argparse.ArgumentParser, *, include_dns: bool) -> None:
    parser.add_argument("--approved-account", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--aws-profile")
    if include_dns:
        parser.add_argument("--route53-domain", required=True)
        parser.add_argument("--allowed-ipv4-cidr", required=True)


def _positive(value: float, name: str) -> float:
    if value <= 0:
        raise ToolError(f"{name} must be positive")
    return value


def _timing(durations: dict[str, float], name: str) -> str:
    value = durations.get(name)
    return "not-measured" if value is None else f"{value:.3f}"


def _timing_first(durations: dict[str, float], *names: str) -> str:
    for name in names:
        if name in durations:
            return _timing(durations, name)
    return "not-measured"
