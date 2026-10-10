# Repository knowledge MCP

This repository includes an optional local [Model Context Protocol
(MCP)](https://modelcontextprotocol.io/) server for maintainers, contributors,
and reviewers. Connect it to an MCP-capable AI assistant (such as Cursor) to
give the assistant grounded knowledge about OpenEMR on ECS and bounded,
read-only access to this repository. It provides no operational access.

## Contents

- [Capabilities](#capabilities)
- [Install and run](#install-and-run)
- [Configure Cursor](#configure-cursor)
- [Other MCP clients](#other-mcp-clients)
- [Safety boundaries](#safety-boundaries)
- [Example requests](#example-requests)
- [Validate the server](#validate-the-server)
- [Troubleshooting](#troubleshooting)

## Capabilities

The server provides these read-only tools:

| Tool | Returns |
|---|---|
| `project_overview` | Project purpose, version, entry points, and guides |
| `architecture_map` | Request flow, data services, and CDK construct sources |
| `get_topic` | Curated summaries and source files for supported topics |
| `search_repository` | Bounded, deterministic search over approved text files |
| `read_repository_file` | A bounded line range from an approved file |
| `documentation_index` | Every searchable Markdown or reStructuredText document, plus the complete, validated set of curated topic sources |
| `version_inventory` | Locally declared versions, without online resolution |
| `configuration_reference` | CDK context keys, with sensitive defaults redacted |
| `discover_operational_commands` | Command and risk descriptions; commands are never executed |

It also exposes three JSON resources: `openemr://overview`,
`openemr://architecture`, and `openemr://documentation-index`.

> [!NOTE]
> The curated topic sources are listed in
> [`tools/knowledge_mcp/knowledge.py`](../../tools/knowledge_mcp/knowledge.py).
> If you move or delete a document that a topic uses, update that list too:
> missing curated sources fail closed, and the knowledge MCP tests fail.

## Install and run

From the repository root, create the project environment and install runtime
and development dependencies:

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
```

Start the STDIO server:

```bash
.venv/bin/python -m tools.knowledge_mcp
```

The process communicates through standard input and output (STDIO). Normally an
MCP client starts it and owns its lifetime.

## Configure Cursor

Add this server to `.cursor/mcp.json` for the workspace. The server requires
POSIX descriptor-relative and no-follow file operations, so use macOS, Linux,
or WSL, with a virtual environment at `.venv`.

```json
{
  "mcpServers": {
    "openemr-on-ecs-knowledge": {
      "type": "stdio",
      "command": "${workspaceFolder}/.venv/bin/python",
      "args": ["-m", "tools.knowledge_mcp"],
      "env": {
        "PYTHONPATH": "${workspaceFolder}"
      }
    }
  }
}
```

Restart Cursor after saving the configuration, then verify the server in
**Settings > Tools & MCP**. See the
[Cursor MCP documentation](https://cursor.com/docs/mcp) for global
configuration and troubleshooting.

> [!WARNING]
> Don't add AWS credentials, API keys, or secrets to this configuration. The
> knowledge server doesn't use them.

## Other MCP clients

Configure a local STDIO server with:

- **Command:** the repository virtual environment's Python executable
- **Arguments:** `-m tools.knowledge_mcp`
- **Working directory or `PYTHONPATH`:** this repository root

Exact configuration keys vary by client.

## Safety boundaries

The knowledge MCP is deliberately narrower than a general filesystem server:

- The bundled `python -m tools.knowledge_mcp` entry point supports only local
  STDIO transport. The in-process server factory exists for tests.
- Its tools accept no repository-root override. The server binds to this
  repository when it starts.
- It has no write, shell, subprocess, AWS, HTTP, or other network operations.
  Starting the STDIO process is the client's responsibility; tool calls never
  start child processes.
- Every tool is annotated read-only, non-destructive, idempotent, and
  closed-world.
- Reads are limited to approved repository-relative text paths and extensions.
  The documentation index covers every root document and every document under
  the approved source, test, diagram, script, Lambda, Compose, and tool paths.
  Missing or policy-inaccessible curated topic sources fail closed.
- Path traversal, absolute paths, symlinks (including symlinked directories),
  secret-like paths, invalid UTF-8, and oversized files are rejected.
- Reads fail closed on platforms without descriptor-relative, no-follow file
  operations.
- Search visits at most 10,000 directory entries; indexes at most 1,000 files
  of at most 256,000 bytes each; and scans at most 32,000,000 bytes and 250,000
  lines per request. It accepts at most eight query terms and returns at most
  20 results with 280-character excerpts.
- File reads return at most 200 lines and 32,000 characters.
- Configuration and version inventory output have fixed entry and input limits
  and deterministic sorting. Version data is a bounded, offline projection of
  the same authoritative declaration inventory used by the
  [maintenance audit](README.md#version-audit); its validated inputs are
  collected from one in-memory snapshot.
- Returned text and configuration values pass through account, credential,
  token, private-key, URL user-info, and sensitive query-value redaction.
- Operational commands are returned only as documentation with a risk label.

> [!IMPORTANT]
> These controls limit the server itself. The MCP client may have other tools
> and permissions, so keep reviewing the client's proposed actions. Read-only
> MCP output is not approval to deploy, rotate credentials, restore, or delete
> AWS resources.

## Example requests

After connecting the server, ask the client to:

- Summarize the request path and identify its source files
- Find the configuration controlling ECS task scaling
- List declared dependency, container, and runtime versions
- Explain the backup or credential-rotation workflow
- Retrieve the documented risk level for an operational command

## Validate the server

Run the focused protocol and security tests:

```bash
.venv/bin/pytest tests/tools/test_knowledge_mcp.py -q
```

The suite exercises every tool and all three resources in memory, starts the
real STDIO entry point for a timeout-bounded protocol smoke test, verifies that
every documented and curated source is indexed, and checks annotations,
deterministic bounds, redaction, race-resistant path controls, repository
immutability, and the absence of network or subprocess access during retrieval.

Run focused static validation (the same checks as the
[CI job](ci.md#repository-knowledge-mcp-knowledge-mcp-tests)):

```bash
.venv/bin/ruff format --check tools/_shared.py tools/knowledge_mcp tests/tools/test_knowledge_mcp.py
.venv/bin/ruff check tools/_shared.py tools/knowledge_mcp tests/tools/test_knowledge_mcp.py
.venv/bin/mypy tools/_shared.py tools/knowledge_mcp
```

## Troubleshooting

- **`No module named tools`:** launch from the repository root or set
  `PYTHONPATH` to it.
- **`No module named fastmcp`:** install `requirements-dev.txt` in the Python
  environment used by the client.
- **Server not listed:** validate `mcp.json`, restart the client, and confirm
  that the configured Python path exists.
- **File rejected:** use a repository-relative, policy-approved text path.
  Binary, secret-like, symlinked, and oversized files are intentionally
  unavailable.
