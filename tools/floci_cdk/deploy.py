"""Bootstrap, deploy, and destroy the Floci CDK smoke stack."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping, Sequence

from tools._shared import CommandResult, ToolError, repository_root, run_command
from tools.live_e2e.emulator import assert_safe_emulator_endpoint, is_floci_e2e_enabled

STACK_NAME = "OpenemrFlociSmoke"
DEFAULT_ACCOUNT = "123456789012"
DEFAULT_REGION = "us-east-1"


def floci_cdk_root(root: Path | None = None) -> Path:
    """Return the Floci CDK app directory."""

    return Path(repository_root(root)) / "tools" / "floci_cdk"


def resolve_cdk_command(root: Path | None = None) -> str:
    """Prefer the repository-pinned CDK CLI."""

    repo = repository_root(root)
    pinned = repo / "node_modules" / ".bin" / "cdk"
    if pinned.is_file() and os.access(pinned, os.X_OK):
        return str(pinned.resolve())
    raise ToolError("Pinned CDK CLI is missing; run npm ci")


def emulator_environ(
    *,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    account_id: str = DEFAULT_ACCOUNT,
    region: str = DEFAULT_REGION,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build process environment that forces CDK/AWS clients onto Floci."""

    assert_safe_emulator_endpoint(endpoint_url)
    if not is_floci_e2e_enabled(endpoint_url):
        raise ToolError("Floci CDK deploy requires OPENEMR_FLOCI_E2E=1 and a local endpoint")
    env = dict(os.environ)
    env.update(
        {
            "AWS_ENDPOINT_URL": endpoint_url,
            "AWS_ACCESS_KEY_ID": access_key_id,
            "AWS_SECRET_ACCESS_KEY": secret_access_key,
            "AWS_DEFAULT_REGION": region,
            "AWS_REGION": region,
            "CDK_DEFAULT_ACCOUNT": account_id,
            "CDK_DEFAULT_REGION": region,
            "CDK_DISABLE_CLI_TELEMETRY": "true",
            "JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION": "1",
        }
    )
    env.pop("AWS_PROFILE", None)
    env.pop("AWS_DEFAULT_PROFILE", None)
    if extra:
        env.update(dict(extra))
    return env


def bootstrap(
    *,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    account_id: str = DEFAULT_ACCOUNT,
    region: str = DEFAULT_REGION,
    root: Path | None = None,
    timeout_seconds: float = 15 * 60,
) -> CommandResult:
    """Bootstrap the CDK toolkit stack into Floci."""

    repo = repository_root(root)
    cdk = resolve_cdk_command(repo)
    app_dir = floci_cdk_root(repo)
    env = emulator_environ(
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        account_id=account_id,
        region=region,
    )
    return _cdk(
        cdk,
        (
            "bootstrap",
            f"aws://{account_id}/{region}",
            "--toolkit-stack-name",
            "CDKToolkit",
            "--qualifier",
            "hnb659fds",
        ),
        cwd=app_dir,
        env=env,
        timeout_seconds=timeout_seconds,
        contexts={"account": account_id, "region": region},
    )


def deploy(
    *,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    account_id: str = DEFAULT_ACCOUNT,
    region: str = DEFAULT_REGION,
    root: Path | None = None,
    timeout_seconds: float = 20 * 60,
) -> CommandResult:
    """Deploy the Floci smoke stack."""

    repo = repository_root(root)
    cdk = resolve_cdk_command(repo)
    app_dir = floci_cdk_root(repo)
    env = emulator_environ(
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        account_id=account_id,
        region=region,
    )
    return _cdk(
        cdk,
        (
            "deploy",
            STACK_NAME,
            "--require-approval",
            "never",
            "--outputs-file",
            str(app_dir / "cdk.out" / "floci-smoke-outputs.json"),
        ),
        cwd=app_dir,
        env=env,
        timeout_seconds=timeout_seconds,
        contexts={"account": account_id, "region": region},
    )


def destroy(
    *,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    account_id: str = DEFAULT_ACCOUNT,
    region: str = DEFAULT_REGION,
    root: Path | None = None,
    timeout_seconds: float = 15 * 60,
) -> CommandResult:
    """Destroy the Floci smoke stack."""

    repo = repository_root(root)
    cdk = resolve_cdk_command(repo)
    app_dir = floci_cdk_root(repo)
    env = emulator_environ(
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        account_id=account_id,
        region=region,
    )
    return _cdk(
        cdk,
        ("destroy", STACK_NAME, "--force"),
        cwd=app_dir,
        env=env,
        timeout_seconds=timeout_seconds,
        contexts={"account": account_id, "region": region},
    )


def run_lifecycle(
    *,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    account_id: str = DEFAULT_ACCOUNT,
    region: str = DEFAULT_REGION,
    root: Path | None = None,
) -> tuple[CommandResult, CommandResult, CommandResult]:
    """Bootstrap, deploy, then destroy the smoke stack against Floci."""

    boot = bootstrap(
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        account_id=account_id,
        region=region,
        root=root,
    )
    if not boot.ok:
        raise ToolError(f"Floci CDK bootstrap failed: {boot.stderr or boot.stdout}")
    deployed: CommandResult | None = None
    try:
        deployed = deploy(
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            account_id=account_id,
            region=region,
            root=root,
        )
        if not deployed.ok:
            raise ToolError(f"Floci CDK deploy failed: {deployed.stderr or deployed.stdout}")
    finally:
        destroyed = destroy(
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            account_id=account_id,
            region=region,
            root=root,
        )
    if not destroyed.ok:
        raise ToolError(f"Floci CDK destroy failed: {destroyed.stderr or destroyed.stdout}")
    assert deployed is not None  # deploy() either returned a result or raised
    return boot, deployed, destroyed


def _cdk(
    cdk: str,
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: float,
    contexts: Mapping[str, str],
) -> CommandResult:
    command = [cdk, *argv, "--app", f"{sys.executable} app.py"]
    for key, value in contexts.items():
        command.extend(("-c", f"{key}={value}"))
    return run_command(tuple(command), cwd=cwd, timeout_seconds=timeout_seconds, env=dict(env))
