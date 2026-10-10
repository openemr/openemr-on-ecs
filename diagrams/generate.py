#!/usr/bin/env python3
"""Generate the OpenEMR on ECS architecture diagrams from CDK source code.

Uses cdk-dia (https://github.com/pistazie/cdk-dia) to render diagrams
directly from the CDK cloud assembly (`cdk.out/tree.json`) produced by a
real `cdk synth`. Because the diagram is derived from the actual synthesized
infrastructure, it stays in sync automatically -- no manual drawing updates
needed.

cdk-dia is a Node.js CLI tool that operates on CDK's synthesized output, so
unlike the AWS PDK CdkGraph plugin (our previous approach), it has no Python
dependencies of its own and does not conflict with cdk-nag or anything else
in the main requirements.txt. Everything runs in the project's normal
virtualenv.

Requirements:
    - The main project virtualenv, activated (see ../requirements.txt)
    - Node.js/npm (already required for the `cdk` CLI)
    - Pinned Node tools: npm ci
    - Graphviz:     brew install graphviz   # macOS (or: sudo apt-get install graphviz)

Usage (from the project root, with the virtualenv activated):
    python diagrams/generate.py
"""

import os
import shlex
import shutil

# Fixed local tools are invoked without a shell.
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(__file__).resolve().parent
CDK_OUT = OUTPUT_DIR / ".cdk.out"
CDK_COMMAND = PROJECT_ROOT / "node_modules" / ".bin" / "cdk"
CDK_DIA_COMMAND = PROJECT_ROOT / "node_modules" / ".bin" / "cdk-dia"

# Dummy values that let `cdk synth` complete without real AWS credentials or
# a real ACM certificate / Route53 hosted zone. Mirrors the approach used in
# scripts/test-cdk-synthesis.py.
DUMMY_ACCOUNT = "123456789012"
DUMMY_REGION = "us-east-1"
DUMMY_CERT_ARN = f"arn:aws:acm:{DUMMY_REGION}:{DUMMY_ACCOUNT}:certificate/00000000-0000-0000-0000-000000000000"
DUMMY_CONTEXT_OVERRIDES = {
    "certificate_arn": DUMMY_CERT_ARN,
    "route53_domain": "null",
    "security_group_ip_range_ipv4": "10.0.0.0/8",
}

DIAGRAMS = [
    {"name": "architecture", "collapse": True, "description": "compact view"},
    {"name": "architecture-full", "collapse": False, "description": "full view"},
]


def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    print(f"  $ {' '.join(cmd)}")
    # Executables are fixed and arguments are passed without a shell.
    return subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        check=True,
        text=True,
        **kwargs,
    )


def synth() -> None:
    print("Running cdk synth...")
    env = {
        **os.environ,
        "AWS_ACCESS_KEY_ID": "fake",
        "AWS_SECRET_ACCESS_KEY": "fake",
        "AWS_DEFAULT_REGION": DUMMY_REGION,
        "AWS_REGION": DUMMY_REGION,
        "CDK_DEFAULT_REGION": DUMMY_REGION,
    }
    env.pop("CDK_DEFAULT_ACCOUNT", None)
    command = [
        str(CDK_COMMAND),
        "synth",
        "--app",
        shlex.join((sys.executable, "app.py")),
        "--no-lookups",
        "--output",
        str(CDK_OUT),
    ]
    for key, value in sorted(DUMMY_CONTEXT_OVERRIDES.items()):
        command.extend(("--context", f"{key}={value}"))
    run(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def generate_diagrams() -> None:
    tree_path = CDK_OUT / "tree.json"
    if not tree_path.exists():
        raise FileNotFoundError(f"{tree_path} not found -- cdk synth did not produce a tree.json")

    for diagram in DIAGRAMS:
        target = OUTPUT_DIR / f"{diagram['name']}.png"
        print(f"Generating {diagram['description']} -> {target.relative_to(PROJECT_ROOT)}")
        cmd = [
            str(CDK_DIA_COMMAND),
            "--tree",
            str(tree_path),
            "--target-path",
            str(target),
        ]
        cmd.append("--collapse" if diagram["collapse"] else "--no-collapse")
        run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        print(f"  -> {target.relative_to(PROJECT_ROOT)}")

        # cdk-dia also writes the intermediate Graphviz .dot source next to the
        # PNG; it's a build artifact we don't need to keep around or commit.
        dot_file = target.with_suffix(".dot")
        dot_file.unlink(missing_ok=True)


def main() -> None:
    if not CDK_COMMAND.is_file() or not os.access(CDK_COMMAND, os.X_OK):
        sys.exit("ERROR: pinned CDK CLI not found; run npm ci.")
    if not CDK_DIA_COMMAND.is_file() or not os.access(CDK_DIA_COMMAND, os.X_OK):
        sys.exit("ERROR: pinned cdk-dia CLI not found; run npm ci.")
    if shutil.which("dot") is None:
        sys.exit("ERROR: Graphviz 'dot' binary not found. Install with: brew install graphviz")

    try:
        synth()
        generate_diagrams()
    except subprocess.CalledProcessError as exc:
        output = exc.stdout or ""
        print(output, file=sys.stderr)
        sys.exit(f"ERROR: command failed: {' '.join(exc.cmd)}")
    finally:
        shutil.rmtree(CDK_OUT, ignore_errors=True)

    print("Done.")


if __name__ == "__main__":
    main()
