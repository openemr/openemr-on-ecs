# Architecture Diagrams

This directory contains the **diagram-as-code** source for the project's architecture diagram. Diagrams are generated directly from the CDK construct tree using the [AWS PDK](https://github.com/aws/aws-pdk) `cdk-graph-plugin-diagram` plugin. Because the diagram is derived from the actual infrastructure code, it stays in sync automatically -- no manual drawing updates needed.

## Table of Contents

- [Quick Start](#quick-start)
- [Prerequisites](#prerequisites)
- [Generating the Diagram](#generating-the-diagram)
- [Files](#files)
- [How It Works](#how-it-works)
- [Design Decisions](#design-decisions)
- [Troubleshooting](#troubleshooting)
- [References](#references)

## Quick Start

```bash
# One-time setup
brew install graphviz        # macOS (or: sudo apt-get install graphviz)
pip install -r requirements.txt   # includes aws-pdk

# Generate the diagram (run from project root)
python diagrams/generate.py
```

This produces `diagrams/architecture.png` (compact view) and `diagrams/architecture-full.png` (all resources), referenced by the project README.

## Prerequisites

| Dependency | Version | Install | Purpose |
|---|---|---|---|
| **Python** | 3.9+ | Already required by the CDK stack | Runtime |
| **Graphviz** | Any recent | `brew install graphviz` (macOS) / `sudo apt-get install graphviz` (Linux) | Rendering engine |
| **aws-pdk** | Latest | `pip install aws-pdk` (included in `requirements.txt`) | CDK graph + diagram plugin |
| **aws-cdk-lib** | 2.x | Already in `requirements.txt` | CDK constructs |

Graphviz provides the `dot` layout engine that converts the graph into a PNG. The AWS PDK provides the `CdkGraph` framework and `CdkGraphDiagramPlugin` that extract the architecture graph from CDK source code and render it.

## Generating the Diagram

From the project root:

```bash
python diagrams/generate.py
```

The script:

1. Creates a temporary CDK app that imports the `OpenemrEcsStack`.
2. Attaches the AWS PDK `CdkGraph` with the diagram plugin.
3. Runs `cdk synth` to build the construct tree.
4. Calls `graph.report()` to render the diagrams.
5. Copies the resulting PNGs to `diagrams/`.

Output:

| File | Description |
|---|---|
| `architecture.png` | Compact view -- meaningful defaults, filtered for readability |
| `architecture-full.png` | Full view -- all CDK resources (useful for auditing) |

Commit the updated PNGs alongside any infrastructure code changes.

## Files

```
diagrams/
├── README.md               # This file
├── generate.py             # Diagram generation script (source of truth)
├── architecture.png        # Compact diagram (committed to git)
└── architecture-full.png   # Full diagram (committed to git)
```

The `.cdk.out/` subdirectory is created at generation time and is gitignored.

## How It Works

The [AWS PDK CdkGraph](https://aws.github.io/aws-pdk/developer_guides/cdk-graph/index.html) framework hooks into the CDK synthesis lifecycle:

```
CDK App  -->  Construct Tree  -->  CdkGraph  -->  Diagram Plugin  -->  PNG
```

1. **Construct tree** -- CDK builds its internal tree of all stacks, constructs, and resources.
2. **CdkGraph** -- serializes that tree into a graph of nodes and edges.
3. **CdkGraphDiagramPlugin** -- applies filter presets, then renders the graph via Graphviz.

This means:
- Any new construct you add to the stack automatically appears in the next diagram generation.
- Removed constructs automatically disappear.
- No manual node/edge definitions to maintain.

## Design Decisions

**Why AWS PDK instead of a manual diagramming library?**

| Concern | Manual library | AWS PDK CdkGraph |
|---|---|---|
| Sync with code | Must update diagram source when infra changes | Automatic -- reads the CDK construct tree |
| Accuracy | Risk of drift between diagram and reality | Guaranteed to match the CDK definition |
| Maintenance | Two things to update (infra code + diagram code) | One thing to update (infra code only) |
| Official support | Community library | AWS-maintained ([github.com/aws/aws-pdk](https://github.com/aws/aws-pdk)) |
| Filter/views | Manual layout changes | Declarative filter presets |

**Why commit the PNGs?**

The PNGs are committed so the README renders on GitHub without requiring readers to install dependencies. The `generate.py` script is the source of truth; the PNGs are build artifacts that we track for convenience.

**Why a separate script instead of modifying `app.py`?**

Diagram generation is a development-time concern. Keeping it in a standalone script avoids adding the `aws-pdk` import path to the production CDK app, and avoids creating extra synthesis artifacts during `cdk deploy`.

## Troubleshooting

| Problem | Solution |
|---|---|
| `command not found: dot` | Install Graphviz: `brew install graphviz` (macOS) or `sudo apt-get install graphviz` (Linux) |
| `ModuleNotFoundError: No module named 'aws_pdk'` | `pip install aws-pdk` or `pip install -r requirements.txt` |
| `ModuleNotFoundError: No module named 'openemr_ecs'` | Run from the **project root**: `python diagrams/generate.py` |
| `No diagrams found` after running | Check that Graphviz is installed and the `dot` binary is on your `PATH` |
| Diagram looks too cluttered | Switch the preset to `FilterPreset.COMPACT` or add custom exclude filters |
| Diagram is missing a service | The service may be filtered out by the preset -- try `FilterPreset.NONE` to verify, then adjust filters |
| CDK synthesis errors | The script uses dummy account/region defaults. Set `CDK_DEFAULT_ACCOUNT` and `CDK_DEFAULT_REGION` if your stack requires real values. |

## References

- [AWS PDK GitHub](https://github.com/aws/aws-pdk)
- [CdkGraph Developer Guide](https://aws.github.io/aws-pdk/developer_guides/cdk-graph/index.html)
- [CdkGraph Diagram Plugin Guide](https://aws.github.io/aws-pdk/developer_guides/cdk-graph-plugin-diagram/index.html)
- [Python API Reference](https://aws.github.io/aws-pdk/api/python/cdk-graph-plugin-diagram/index.html)
- [Blog: AWS Architectural Diagrams on a Commit Base](https://dev.to/zirkonium88/aws-architectural-diagrams-on-a-commit-base-using-aws-pdk-diagram-plugin-with-python-3b84)
