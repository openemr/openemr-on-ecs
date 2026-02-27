#!/usr/bin/env python3
"""Generate the OpenEMR on ECS architecture diagram from CDK source code.

Uses the AWS PDK (https://github.com/aws/aws-pdk) CdkGraph plugin to build
a diagram directly from the CDK construct tree.  This keeps the diagram in
sync with the actual infrastructure definition -- no manual updates needed.

Requirements:
    pip install aws-pdk
    brew install graphviz   # macOS  (or: sudo apt-get install graphviz)

Usage (from the project root):
    python diagrams/generate.py
"""

import asyncio
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import aws_cdk as cdk  # noqa: E402
from aws_pdk.cdk_graph import CdkGraph, FilterPreset  # noqa: E402
from aws_pdk.cdk_graph_plugin_diagram import CdkGraphDiagramPlugin  # noqa: E402

from openemr_ecs.stack import OpenemrEcsStack  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent
CDK_OUT = OUTPUT_DIR / ".cdk.out"
DIAGRAM_NAME = "architecture"


async def main() -> None:
    app = cdk.App(outdir=str(CDK_OUT))

    app.node.set_context("route53_domain", "example.com")
    app.node.set_context("security_group_ip_range_ipv4", "10.0.0.0/8")

    OpenemrEcsStack(
        app,
        "OpenemrEcsStack",
        env=cdk.Environment(
            account=os.getenv("CDK_DEFAULT_ACCOUNT", "123456789012"),
            region=os.getenv("CDK_DEFAULT_REGION", "us-east-1"),
        ),
    )

    graph = CdkGraph(
        app,
        plugins=[
            CdkGraphDiagramPlugin(  # type: ignore[list-item]
                diagrams=[
                    {
                        "name": DIAGRAM_NAME,
                        "title": "OpenEMR on AWS ECS",
                        "filterPlan": {
                            "preset": FilterPreset.COMPACT,
                        },
                    },
                    {
                        "name": f"{DIAGRAM_NAME}-full",
                        "title": "OpenEMR on AWS ECS (Full)",
                        "filterPlan": {
                            "preset": FilterPreset.NONE,
                        },
                    },
                ]
            ),
        ],
    )

    app.synth()
    graph.report()

    cdkgraph_dir = CDK_OUT / "cdkgraph"
    rename_map = {
        f"diagram.{DIAGRAM_NAME}.png": "architecture.png",
        f"diagram.{DIAGRAM_NAME}-full.png": "architecture-full.png",
    }

    copied = False
    if cdkgraph_dir.exists():
        for src_name, dest_name in rename_map.items():
            src = cdkgraph_dir / src_name
            if src.exists():
                dest = OUTPUT_DIR / dest_name
                shutil.copy2(src, dest)
                print(f"  -> {dest.relative_to(OUTPUT_DIR.parent)}")
                copied = True

    if not copied:
        available = list(cdkgraph_dir.rglob("*")) if cdkgraph_dir.exists() else []
        raise FileNotFoundError(
            f"No diagrams found in {cdkgraph_dir}. " f"Available artifacts: {[str(f.name) for f in available]}"
        )

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
