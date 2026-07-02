# Architecture Diagrams

This directory contains the **diagram-as-code** source for the project's architecture diagram. Diagrams are generated directly from the synthesized CDK cloud assembly using [cdk-dia](https://github.com/pistazie/cdk-dia). Because the diagram is derived from the actual infrastructure code, it stays in sync automatically -- no manual drawing updates needed.

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
brew install graphviz         # macOS (or: sudo apt-get install graphviz)
npm install --location=global cdk-dia

# Generate the diagrams (run from project root, with the project's
# virtualenv activated -- no separate virtualenv needed)
source .venv/bin/activate
python diagrams/generate.py
```

This produces `diagrams/architecture.png` (compact view) and `diagrams/architecture-full.png` (all resources), referenced by the project README.

> **Note:** cdk-dia is a Node.js CLI tool that operates on CDK's synthesized `cdk.out/tree.json` output -- it has no Python dependencies and does not conflict with anything in the main `requirements.txt`. Unlike the project's previous AWS PDK-based approach, there is no need for a separate virtualenv.

## Prerequisites

| Dependency | Version | Install | Purpose |
|---|---|---|---|
| **Node.js / npm** | Any recent | Already required for the `cdk` CLI | Runs cdk-dia |
| **cdk-dia** | Latest | `npm install --location=global cdk-dia` | Renders the CDK cloud assembly into a diagram |
| **Graphviz** | Any recent | `brew install graphviz` (macOS) / `sudo apt-get install graphviz` (Linux) | Rendering engine (`dot`) |
| **Python** | 3.9+ | Already required by the CDK stack | Runs `generate.py` and `cdk synth` |

Graphviz provides the `dot` layout engine that converts the graph into a PNG. cdk-dia reads the CDK construct tree (`tree.json`) produced by `cdk synth` and renders it.

## Generating the Diagram

From the project root, with the main project virtualenv activated:

```bash
python diagrams/generate.py
```

The script:

1. Temporarily backs up `cdk.json` / `cdk.context.json` and swaps in dummy, synth-only values (a placeholder ACM certificate ARN, a fixed IP range, and cached availability zones) so `cdk synth` succeeds without real AWS credentials.
2. Runs `cdk synth --no-lookups` to build the construct tree.
3. Runs `npx cdk-dia` twice against the resulting `tree.json` -- once with `--collapse` (compact view) and once with `--no-collapse` (full view).
4. Restores the original `cdk.json` / `cdk.context.json` and cleans up build artifacts.

Output:

| File | Description |
|---|---|
| `architecture.png` | Compact view -- CDK L2/L3 constructs collapsed for readability |
| `architecture-full.png` | Full view -- every CDK resource, uncollapsed (useful for auditing) |

Commit the updated PNGs alongside any infrastructure code changes.

## Files

```
diagrams/
├── README.md               # This file
├── generate.py             # Diagram generation script (source of truth)
├── architecture.png        # Compact diagram (committed to git)
└── architecture-full.png   # Full diagram (committed to git)
```

The `.cdk.out/` subdirectory is created at generation time and is gitignored, as are the intermediate `.dot` files cdk-dia writes alongside each PNG.

## How It Works

[cdk-dia](https://github.com/pistazie/cdk-dia) reads CDK's synthesized cloud assembly directly, so it works with any CDK language (Python, TypeScript, Java, etc.) without needing language-specific bindings:

```
CDK App  -->  cdk synth  -->  cdk.out/tree.json  -->  cdk-dia  -->  Graphviz  -->  PNG
```

1. **`cdk synth`** -- CDK builds the construct tree and writes it to `tree.json` in the cloud assembly.
2. **cdk-dia** -- parses `tree.json`, optionally collapsing CDK L2/L3 constructs into single nodes, and builds a Graphviz graph.
3. **Graphviz** -- lays out and renders the graph as a PNG.

This means:
- Any new construct you add to the stack automatically appears in the next diagram generation.
- Removed constructs automatically disappear.
- No manual node/edge definitions to maintain.

## Design Decisions

**Why cdk-dia instead of a manual diagramming library?**

| Concern | Manual library | cdk-dia |
|---|---|---|
| Sync with code | Must update diagram source when infra changes | Automatic -- reads the synthesized CDK cloud assembly |
| Accuracy | Risk of drift between diagram and reality | Guaranteed to match the CDK definition |
| Maintenance | Two things to update (infra code + diagram code) | One thing to update (infra code only) |
| Dependencies | Varies | Node.js CLI tool operating on `cdk.out` -- no Python dependency conflicts |
| Views | Manual layout changes | `--collapse` / `--no-collapse` for compact vs. full detail |

**Why cdk-dia instead of AWS PDK's CdkGraph plugin (the previous approach)?**

The project previously used AWS PDK's `CdkGraph`/`CdkGraphDiagramPlugin`. That required a dedicated, isolated Python virtualenv because `aws-pdk` pins `cdk-nag<3.0.0`, which is incompatible with the `cdk-nag` v3 used by the main app (confirmed: `aws-pdk`'s bundled `pdk_nag` module references `cdk_nag.INagLogger`, a type removed in `cdk-nag` v3, so it fails at import time even if pip's resolver is bypassed). Since cdk-dia operates on CDK's language-agnostic synthesized output rather than importing CDK/cdk-nag Python bindings itself, it sidesteps that conflict entirely and lets diagram generation run in the same virtualenv as everything else.

**Why commit the PNGs?**

The PNGs are committed so the README renders on GitHub without requiring readers to install dependencies. The `generate.py` script is the source of truth; the PNGs are build artifacts that we track for convenience.

**Why a separate script instead of modifying `app.py`?**

Diagram generation is a development-time concern. Keeping it in a standalone script avoids adding diagram-only tooling to the production CDK app, and avoids creating extra synthesis artifacts during `cdk deploy`.

## Troubleshooting

| Problem | Solution |
|---|---|
| `command not found: dot` | Install Graphviz: `brew install graphviz` (macOS) or `sudo apt-get install graphviz` (Linux) |
| `command not found: cdk` | Install the CDK CLI: `npm install --location=global aws-cdk@2` |
| `npx cdk-dia` fails to resolve the package | Install it globally instead: `npm install --location=global cdk-dia` |
| `ModuleNotFoundError: No module named 'openemr_ecs'` | Run from the **project root** with the project virtualenv activated: `python diagrams/generate.py` |
| `cdk synth` errors about missing context | The script seeds dummy account/region/AZ context automatically. If you interrupt the script mid-run, restore `cdk.json`/`cdk.context.json` from the `.diagrams-backup` files it creates. |
| Diagram looks too cluttered | Use the compact (`--collapse`, default) output; the full (`--no-collapse`) diagram is intentionally dense for auditing |

## References

- [cdk-dia GitHub](https://github.com/pistazie/cdk-dia)
- [cdk-dia npm package](https://www.npmjs.com/package/cdk-dia)
- [Graphviz](https://graphviz.org/)
