"""Connectivity and health validators used by rotation orchestration."""

from __future__ import annotations

from typing import Any, Dict

import pymysql
import requests


class ValidationError(RuntimeError):
    """Raised when runtime validation fails."""


def validate_rds_connection(slot: Dict[str, Any], connect_timeout: int = 5) -> None:
    conn = pymysql.connect(
        host=slot["host"],
        user=slot["username"],
        password=slot["password"],
        database=slot["dbname"],
        port=int(slot.get("port", 3306)),
        connect_timeout=connect_timeout,
        ssl={"ssl": {}} if slot.get("ssl_required", True) else None,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            row = cur.fetchone()
            if not row or int(row[0]) != 1:
                raise ValidationError("RDS validation query returned unexpected result")
    finally:
        conn.close()


def validate_openemr_health(health_url: str | None, timeout_seconds: int = 10) -> None:
    """Best-effort HTTP health probe. The ECS services_stable waiter already
    confirms task health; this is an extra signal. Network topology may prevent
    the rotation task from reaching the ALB, so failures are non-fatal.
    """

    if not health_url:
        return

    try:
        response = requests.get(health_url, timeout=timeout_seconds, verify=False, allow_redirects=False)
        if response.status_code not in (200, 301, 302):
            print(f"WARNING: OpenEMR health probe returned status {response.status_code}")
    except Exception as exc:
        print(f"WARNING: OpenEMR health probe unreachable ({exc}); relying on ECS services_stable waiter")
