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
    - cdk-dia:      npm install -g cdk-dia
    - Graphviz:     brew install graphviz   # macOS (or: sudo apt-get install graphviz)

Usage (from the project root, with the virtualenv activated):
    python diagrams/generate.py
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(__file__).resolve().parent
CDK_OUT = OUTPUT_DIR / ".cdk.out"
CDK_JSON = PROJECT_ROOT / "cdk.json"
CDK_JSON_BACKUP = PROJECT_ROOT / "cdk.json.diagrams-backup"
CDK_CONTEXT_JSON = PROJECT_ROOT / "cdk.context.json"
CDK_CONTEXT_JSON_BACKUP = PROJECT_ROOT / "cdk.context.json.diagrams-backup"

# Dummy values that let `cdk synth` complete without real AWS credentials or
# a real ACM certificate / Route53 hosted zone. Mirrors the approach used in
# scripts/test-cdk-synthesis.py.
DUMMY_ACCOUNT = "123456789012"
DUMMY_REGION = "us-east-1"
DUMMY_CERT_ARN = f"arn:aws:acm:{DUMMY_REGION}:{DUMMY_ACCOUNT}:certificate/00000000-0000-0000-0000-000000000000"
DUMMY_CONTEXT_OVERRIDES = {
    "certificate_arn": DUMMY_CERT_ARN,
    "route53_domain": None,
    "security_group_ip_range_ipv4": "10.0.0.0/8",
}
# Avoids a live AWS API call to resolve availability zones under --no-lookups.
DUMMY_CDK_CONTEXT = {
    f"availability-zones:account={DUMMY_ACCOUNT}:region={DUMMY_REGION}": [
        f"{DUMMY_REGION}a",
        f"{DUMMY_REGION}b",
        f"{DUMMY_REGION}c",
    ]
}

DIAGRAMS = [
    {"name": "architecture", "collapse": True, "description": "compact view"},
    {"name": "architecture-full", "collapse": False, "description": "full view"},
]


def run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=PROJECT_ROOT, check=True, **kwargs)


def stage_dummy_config() -> None:
    """Back up cdk.json/cdk.context.json and replace them with safe, synth-only values."""
    if CDK_JSON_BACKUP.exists() or CDK_CONTEXT_JSON_BACKUP.exists():
        sys.exit(
            "ERROR: found a leftover backup file from a previous interrupted run "
            f"({CDK_JSON_BACKUP.name} / {CDK_CONTEXT_JSON_BACKUP.name}). "
            "Manually inspect and restore cdk.json/cdk.context.json before re-running."
        )
    shutil.copy2(CDK_JSON, CDK_JSON_BACKUP)
    with open(CDK_JSON) as f:
        cdk_json = json.load(f)
    cdk_json["context"].update(DUMMY_CONTEXT_OVERRIDES)
    with open(CDK_JSON, "w") as f:
        json.dump(cdk_json, f, indent=2)
        f.write("\n")

    if CDK_CONTEXT_JSON.exists():
        shutil.copy2(CDK_CONTEXT_JSON, CDK_CONTEXT_JSON_BACKUP)
    with open(CDK_CONTEXT_JSON, "w") as f:
        json.dump(DUMMY_CDK_CONTEXT, f, indent=2)


def restore_real_config() -> None:
    """Restore the original cdk.json/cdk.context.json, removing the dummy ones."""
    if CDK_JSON_BACKUP.exists():
        shutil.move(str(CDK_JSON_BACKUP), str(CDK_JSON))
    if CDK_CONTEXT_JSON_BACKUP.exists():
        shutil.move(str(CDK_CONTEXT_JSON_BACKUP), str(CDK_CONTEXT_JSON))
    elif CDK_CONTEXT_JSON.exists():
        CDK_CONTEXT_JSON.unlink()


def synth() -> None:
    print("Running cdk synth...")
    env = {**os.environ, "CDK_DEFAULT_ACCOUNT": DUMMY_ACCOUNT, "CDK_DEFAULT_REGION": DUMMY_REGION}
    run(
        ["cdk", "synth", "--no-lookups", "-o", str(CDK_OUT)],
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
            "npx",
            "--yes",
            "cdk-dia",
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
    if shutil.which("cdk") is None:
        sys.exit("ERROR: 'cdk' CLI not found. Install with: npm install --location=global aws-cdk@2")
    if shutil.which("npx") is None:
        sys.exit("ERROR: 'npx' (Node.js/npm) not found. Install Node.js first.")
    if shutil.which("dot") is None:
        sys.exit("ERROR: Graphviz 'dot' binary not found. Install with: brew install graphviz")

    stage_dummy_config()
    try:
        synth()
        generate_diagrams()
    except subprocess.CalledProcessError as exc:
        output = exc.stdout.decode() if exc.stdout else ""
        print(output, file=sys.stderr)
        sys.exit(f"ERROR: command failed: {' '.join(exc.cmd)}")
    finally:
        restore_real_config()
        shutil.rmtree(CDK_OUT, ignore_errors=True)

    print("Done.")


if __name__ == "__main__":
    main()
