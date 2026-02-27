"""CLI entrypoint for ECS credential rotation task."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .rotate import RotationOrchestrator, main_json_error


def fix_permissions() -> int:
    """Fix sqlconf.php permissions (chmod 644) so Apache can read it. No rotation."""
    root = os.environ.get("OPENEMR_SITES_MOUNT_ROOT", "/mnt/openemr-sites")
    path = Path(root) / "default" / "sqlconf.php"
    if not path.exists():
        print(f"sqlconf.php not found at {path}", file=sys.stderr)
        return 1
    path.chmod(0o644)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rotate OpenEMR RDS credentials with dual slots")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate flow without mutating state")
    parser.add_argument("--log-json", action="store_true", help="Emit structured JSON status output")
    parser.add_argument("--fix-permissions", action="store_true", help="Only fix sqlconf.php permissions (chmod 644), then exit")
    parser.add_argument("--sync-db-users", action="store_true", help="Sync RDS users (openemr_a, openemr_b) to match slot secret passwords; no flip, no sqlconf change")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.fix_permissions:
        return fix_permissions()

    if args.sync_db_users:
        try:
            orchestrator = RotationOrchestrator.from_env(dry_run=False)
            orchestrator.sync_db_users()
            if args.log_json:
                print(json.dumps({"status": "ok", "action": "sync_db_users"}))
            else:
                print("Database users synced to match slot secrets.")
            return 0
        except Exception as exc:
            if args.log_json:
                print(main_json_error(exc))
            else:
                print(f"Sync failed: {exc}", file=sys.stderr)
            return 1

    try:
        orchestrator = RotationOrchestrator.from_env(dry_run=args.dry_run)
        orchestrator.rotate()
        if args.log_json:
            print(json.dumps({"status": "ok", "dry_run": args.dry_run}))
        else:
            print(f"Rotation completed successfully (dry_run={args.dry_run})")
        return 0
    except Exception as exc:
        if args.log_json:
            print(main_json_error(exc))
        else:
            print(f"Rotation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
