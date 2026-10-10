"""Bounded, offline repository knowledge used by the local MCP server."""

from __future__ import annotations

import json
import os
import re
import stat
from collections import defaultdict
from pathlib import Path
from typing import Any

from tools._shared import (
    ToolError,
    is_secret_like_path,
    is_sensitive_key,
    redact_text,
    repository_root,
    safe_read_text,
)
from tools.version_audit.inventory import InventorySource, collect_declarations

MAX_QUERY_LENGTH = 120
MAX_QUERY_TERMS = 8
MAX_RESULTS = 20
MAX_TOPIC_LENGTH = 80
MAX_PATH_LENGTH = 500
MAX_READ_LINES = 200
MAX_READ_CHARS = 32_000
MAX_START_LINE = 1_000_000
MAX_SEARCH_FILE_BYTES = 256_000
MAX_INDEXED_FILES = 1_000
MAX_TRAVERSED_ENTRIES = 10_000
MAX_SEARCH_TOTAL_BYTES = 32_000_000
MAX_SEARCH_TOTAL_LINES = 250_000
MAX_MATCHES_PER_FILE = 3
MAX_CONFIGURATION_ENTRIES = 200
MAX_CONFIGURATION_VALUE_CHARS = 1_000
MAX_VERSION_COMPONENTS = 200
MAX_VERSION_INPUT_FILES = 1_000
MAX_VERSION_TOTAL_BYTES = 32_000_000
MAX_VERSION_VALUE_CHARS = 500

_DOCUMENTATION_EXTENSIONS = {".md", ".rst"}
_ALLOWED_EXTENSIONS = {
    ".go",
    ".in",
    ".json",
    ".md",
    ".mod",
    ".py",
    ".rst",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
_TOP_LEVEL_FILES = {
    ".gitignore",
    ".pre-commit-config.yaml",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "README.md",
    "VERSION",
    "app.py",
    "cdk.json",
    "package-lock.json",
    "package.json",
    "pyproject.toml",
    "requirements-dev.txt",
    "requirements.txt",
}
_ALLOWED_PREFIXES = {
    ".github/workflows",
    "compose",
    "diagrams",
    "docs",
    "e2e-results",
    "lambda",
    "openemr_ecs",
    "scripts",
    "tests",
    "tools",
}
_VERSION_METADATA_FIELDS = {
    "arm64_digest",
    "conflicting_exact_pins",
    "conflicting_pins",
    "immutable_sha_pins",
    "raw_value",
    "revision_labels",
    "revisions",
}

_TOPICS: dict[str, dict[str, Any]] = {
    "project-purpose": {
        "summary": "Deploy a secure, resilient OpenEMR environment on AWS ECS Fargate with CDK.",
        "sources": ["README.md", "docs/get-started/README.md"],
    },
    "architecture": {
        "summary": "The stack combines edge routing, private ECS tasks, Aurora, Valkey, EFS, and AWS Backup.",
        "sources": ["docs/reference/architecture.md", "openemr_ecs/stack.py"],
    },
    "cdk": {
        "summary": "app.py creates OpenemrEcsStack; feature constructs live under openemr_ecs/.",
        "sources": ["app.py", "openemr_ecs/stack.py", "cdk.json"],
    },
    "ecs-fargate": {
        "summary": "OpenEMR runs as ARM64 Fargate tasks with shared EFS storage.",
        "sources": ["docs/reference/architecture.md", "openemr_ecs/compute.py"],
    },
    "networking": {
        "summary": "Public edge resources route to tasks in private subnets with explicit security-group paths.",
        "sources": ["docs/reference/architecture.md", "openemr_ecs/network.py"],
    },
    "alb": {
        "summary": "An Application Load Balancer performs public TLS termination and HTTPS target checks.",
        "sources": ["docs/reference/architecture.md", "openemr_ecs/compute.py"],
    },
    "waf": {
        "summary": "AWS WAF protections are attached at the application edge.",
        "sources": ["docs/reference/security-and-compliance.md", "openemr_ecs/security.py"],
    },
    "tls": {
        "summary": "The deployment requires Route 53-managed ACM or an explicit certificate ARN.",
        "sources": ["docs/guides/https-and-dns.md", "openemr_ecs/security.py"],
    },
    "route53": {
        "summary": "Route 53 can provide DNS validation and application records for an owned hosted zone.",
        "sources": ["docs/guides/https-and-dns.md", "openemr_ecs/security.py"],
    },
    "global-accelerator": {
        "summary": "Global Accelerator is an optional edge path and has separate cost implications.",
        "sources": ["docs/guides/optional-features.md", "openemr_ecs/network.py"],
    },
    "aurora": {
        "summary": "Aurora MySQL Serverless v2 stores application data with encrypted connections and backups.",
        "sources": ["docs/reference/architecture.md", "openemr_ecs/database.py"],
    },
    "elasticache": {
        "summary": "A TLS-enabled ElastiCache Serverless Valkey cache supports OpenEMR caching.",
        "sources": ["docs/reference/architecture.md", "openemr_ecs/database.py"],
    },
    "efs": {
        "summary": "Encrypted EFS file systems persist OpenEMR sites data and shared TLS material.",
        "sources": ["docs/reference/architecture.md", "openemr_ecs/storage.py"],
    },
    "backup": {
        "summary": "AWS Backup protects Aurora and EFS; local scripts expose explicit backup operations.",
        "sources": ["docs/guides/backup-and-restore.md", "scripts/create-backup.sh"],
    },
    "credential-rotation": {
        "summary": "A dedicated ECS task rotates credentials and updates persisted OpenEMR settings.",
        "sources": ["docs/guides/credential-rotation.md", "scripts/run-credential-rotation.sh"],
    },
    "monitoring": {
        "summary": "Optional alarms, dashboards, logs, and notifications provide operational visibility.",
        "sources": ["docs/guides/optional-features.md", "openemr_ecs/monitoring.py"],
    },
    "analytics": {
        "summary": "Optional analytics and EMR Serverless resources are isolated behind context flags.",
        "sources": ["docs/guides/analytics.md", "openemr_ecs/analytics.py"],
    },
    "lambda": {
        "summary": "Lambda-backed custom resources support setup, cleanup, exports, and operational automation.",
        "sources": ["lambda/README.md", "openemr_ecs/cleanup.py"],
    },
    "configuration": {
        "summary": "CDK context controls optional features, capacities, certificates, retention, and operations.",
        "sources": ["docs/guides/configuration.md", "cdk.json"],
    },
    "versions": {
        "summary": "The local inventory reports declared project, dependency, container, and runtime versions.",
        "sources": [
            "VERSION",
            "requirements.txt",
            "requirements-dev.txt",
            "tools/openemr-import-worker/requirements.in",
            "tools/openemr-import-worker/requirements.txt",
            "openemr_ecs/constants.py",
        ],
    },
    "ci": {
        "summary": "GitHub Actions runs tests, synthesis, security checks, and static validation without deployment.",
        "sources": [".github/workflows/ci.yml", "docs/maintainers/ci.md"],
    },
    "local-testing": {
        "summary": "Unit, synthesis, Go, and Docker Compose checks are available without mutating AWS.",
        "sources": ["docs/guides/local-testing.md", "docs/maintainers/README.md"],
    },
    "cleanup": {
        "summary": "Cleanup is high risk and must target only intended stack resources.",
        "sources": ["docs/reference/troubleshooting.md", "scripts/cleanup-all-stacks.sh"],
    },
    "restore": {
        "summary": "Restore procedures use AWS Backup recovery points and require post-restore validation.",
        "sources": ["docs/guides/backup-and-restore.md", "scripts/restore-from-backup.sh"],
    },
    "openemr-import": {
        "summary": (
            "The guarded import utility inspects native backups offline, requires a "
            "fresh same-version target, and keeps destructive AWS execution explicit."
        ),
        "sources": [
            "docs/guides/importing-openemr.md",
            "docs/adr/0001-guarded-openemr-import.md",
            "tools/openemr_import/cli.py",
            "tools/openemr_import/aws.py",
            "tools/openemr-import-worker/worker.py",
            "openemr_ecs/compute.py",
            "openemr_ecs/storage.py",
        ],
    },
    "live-e2e": {
        "summary": (
            "An approval-gated local runner deploys, validates, measures, and cleans up an isolated real-AWS stack."
        ),
        "sources": [
            "docs/maintainers/live-e2e.md",
            "docs/maintainers/deployment-timing.md",
            "e2e-results/history.json",
            "tools/live_e2e/runner.py",
            "tools/live_e2e/aws.py",
        ],
    },
    "floci-emulation": {
        "summary": (
            "Digest-pinned Floci tests exercise guarded live-E2E AWS, CDK, "
            "ownership, and cleanup paths locally without real AWS credentials."
        ),
        "sources": [
            "docs/maintainers/floci.md",
            ".github/workflows/ci.yml",
            "compose/docker-compose.floci.yml",
            "tools/floci_cdk/deploy.py",
            "tools/live_e2e/emulator.py",
            "tools/live_e2e/floci_images.py",
            "tools/live_e2e/floci_seed.py",
            "tests/tools/test_floci_live_e2e_full.py",
        ],
    },
    "troubleshooting": {
        "summary": "The troubleshooting guide covers deployment, health, database, DNS, and cleanup diagnostics.",
        "sources": ["docs/reference/troubleshooting.md"],
    },
    "costs": {
        "summary": "Costs depend on database, cache, NAT, edge, backup, and optional analytics choices.",
        "sources": ["docs/reference/costs.md", "README.md"],
    },
    "destructive-commands": {
        "summary": "Deploy, destroy, restore, cleanup, and credential rotation require explicit human review.",
        "sources": ["docs/maintainers/README.md", "docs/guides/backup-and-restore.md"],
    },
    "maintainer-workflows": {
        "summary": "The maintainer guide documents local MCP setup, validation, and safety boundaries.",
        "sources": ["docs/maintainers/README.md"],
    },
    "knowledge-mcp": {
        "summary": "The local read-only MCP exposes bounded, redacted repository knowledge over STDIO.",
        "sources": ["docs/maintainers/knowledge-mcp.md", "tools/knowledge_mcp/server.py"],
    },
}


class KnowledgeError(ValueError):
    """Raised for a bounded knowledge request that violates server policy."""


def _normalize_topic(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _bounded(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 15]}...<truncated>"


def _redact_structured_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and is_sensitive_key(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(child_key): _redact_structured_value(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_structured_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class RepositoryKnowledge:
    """Read-only repository index with deterministic safety limits."""

    def __init__(self, root: Path | None = None):
        self.root = (root or repository_root()).resolve()
        if not self.root.is_dir():
            raise KnowledgeError("Repository root is not a directory")

    @staticmethod
    def _has_unapproved_hidden_part(relative: Path) -> bool:
        for index, part in enumerate(relative.parts):
            if not part.startswith("."):
                continue
            if index == 0 and part == ".github":
                continue
            if len(relative.parts) == 1 and relative.as_posix() in _TOP_LEVEL_FILES:
                continue
            return True
        return False

    def _is_allowed_relative(self, relative: Path) -> bool:
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or is_secret_like_path(relative)
            or self._has_unapproved_hidden_part(relative)
        ):
            return False
        normalized = relative.as_posix()
        if normalized in _TOP_LEVEL_FILES:
            return True
        if len(relative.parts) == 1 and relative.suffix.lower() in _DOCUMENTATION_EXTENSIONS:
            return True
        if relative.suffix.lower() not in _ALLOWED_EXTENSIONS:
            if relative.name != "Dockerfile":
                return False
        return any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in _ALLOWED_PREFIXES)

    def _directory_is_allowed(self, relative: Path) -> bool:
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or is_secret_like_path(relative)
            or self._has_unapproved_hidden_part(relative)
        ):
            return False
        normalized = relative.as_posix()
        return any(
            normalized == prefix or normalized.startswith(f"{prefix}/") or prefix.startswith(f"{normalized}/")
            for prefix in _ALLOWED_PREFIXES
        )

    def _safe_files(self, *, validate_documents: bool = False) -> list[Path]:
        files: list[Path] = []
        traversed_entries = 0
        pending = [self.root]
        while pending:
            current_path = pending.pop()
            entries: list[tuple[str, bool, bool, bool, int]] = []
            try:
                with os.scandir(current_path) as iterator:
                    for entry in iterator:
                        traversed_entries += 1
                        if traversed_entries > MAX_TRAVERSED_ENTRIES:
                            raise KnowledgeError(
                                f"Repository traversal exceeds the {MAX_TRAVERSED_ENTRIES}-entry limit"
                            )
                        try:
                            information = entry.stat(follow_symlinks=False)
                            entries.append(
                                (
                                    entry.name,
                                    entry.is_symlink(),
                                    stat.S_ISDIR(information.st_mode),
                                    stat.S_ISREG(information.st_mode),
                                    information.st_size,
                                )
                            )
                        except OSError as exc:
                            relative = (current_path / entry.name).relative_to(self.root)
                            if (
                                validate_documents
                                and self._is_allowed_relative(relative)
                                and relative.suffix.lower() in _DOCUMENTATION_EXTENSIONS
                            ):
                                raise KnowledgeError(
                                    f"Documentation source cannot be inspected: {relative.as_posix()}"
                                ) from exc
                            continue
            except OSError as exc:
                raise KnowledgeError(f"Unable to enumerate approved repository path: {exc}") from exc

            allowed_directories: list[Path] = []
            for name, is_symlink, is_directory, is_regular, size in sorted(entries):
                path = current_path / name
                relative = path.relative_to(self.root)
                if is_symlink:
                    if (
                        validate_documents
                        and self._is_allowed_relative(relative)
                        and relative.suffix.lower() in _DOCUMENTATION_EXTENSIONS
                    ):
                        raise KnowledgeError(f"Documentation source is symlinked: {relative.as_posix()}")
                    continue
                if is_directory:
                    if self._directory_is_allowed(relative):
                        if len(relative.as_posix()) > MAX_PATH_LENGTH:
                            raise KnowledgeError(
                                f"Approved repository path exceeds the {MAX_PATH_LENGTH}-character limit"
                            )
                        allowed_directories.append(path)
                    continue
                if not self._is_allowed_relative(relative):
                    continue
                if len(relative.as_posix()) > MAX_PATH_LENGTH:
                    raise KnowledgeError(f"Approved repository path exceeds the {MAX_PATH_LENGTH}-character limit")
                if not is_regular or size > MAX_SEARCH_FILE_BYTES:
                    if validate_documents and relative.suffix.lower() in _DOCUMENTATION_EXTENSIONS:
                        raise KnowledgeError(
                            f"Documentation source is not a readable regular file: {relative.as_posix()}"
                        )
                    continue
                files.append(path)
                if len(files) > MAX_INDEXED_FILES:
                    raise KnowledgeError(f"Repository index exceeds the {MAX_INDEXED_FILES}-file limit")
            pending.extend(reversed(allowed_directories))

        if validate_documents:
            for path in files:
                relative_name = _relative(self.root, path)
                if path.suffix.lower() in _DOCUMENTATION_EXTENSIONS:
                    self._read_raw(relative_name)
        return files

    def _read_raw(self, requested: str, *, max_bytes: int = MAX_SEARCH_FILE_BYTES) -> str:
        if not requested or len(requested) > MAX_PATH_LENGTH or "\x00" in requested:
            raise KnowledgeError(f"Path length must be between 1 and {MAX_PATH_LENGTH} characters")
        requested_path = Path(requested)
        if requested_path.is_absolute():
            raise KnowledgeError("Only repository-relative paths are accepted")
        if not self._is_allowed_relative(requested_path):
            raise KnowledgeError("Path is outside the MCP read policy")
        try:
            return safe_read_text(
                self.root,
                requested_path,
                max_bytes=max_bytes,
                allowed_extensions=_ALLOWED_EXTENSIONS | {""},
            )
        except (OSError, ToolError) as exc:
            raise KnowledgeError(str(exc)) from exc

    def _read(self, requested: str, *, max_bytes: int = MAX_SEARCH_FILE_BYTES) -> str:
        return redact_text(self._read_raw(requested, max_bytes=max_bytes))

    def overview(self) -> dict[str, Any]:
        """Return compact, non-sensitive project identity and entry points."""

        version = self._read("VERSION", max_bytes=128).strip()
        return {
            "name": "OpenEMR on ECS",
            "version": version,
            "purpose": _TOPICS["project-purpose"]["summary"],
            "cdk_entry_point": "app.py",
            "stack": "openemr_ecs.stack.OpenemrEcsStack",
            "configuration": "cdk.json context",
            "primary_guides": [
                "README.md",
                "docs/README.md",
                "docs/get-started/README.md",
                "docs/reference/architecture.md",
                "docs/guides/configuration.md",
                "docs/reference/troubleshooting.md",
                "docs/guides/importing-openemr.md",
                "docs/maintainers/live-e2e.md",
                "docs/maintainers/floci.md",
                "docs/maintainers/knowledge-mcp.md",
                "docs/maintainers/README.md",
            ],
            "safety": "This server is offline and read-only; it cannot execute operational commands.",
        }

    def architecture(self) -> dict[str, Any]:
        """Return the high-level component map and source locations."""

        return {
            "request_path": [
                "Client",
                "Route 53 / optional Global Accelerator",
                "AWS WAF",
                "Application Load Balancer",
                "ECS Fargate OpenEMR service",
            ],
            "data_services": {
                "database": "Aurora MySQL Serverless v2",
                "cache": "ElastiCache Valkey",
                "shared_files": "Amazon EFS",
                "recovery": "AWS Backup",
            },
            "construct_sources": {
                "orchestration": "openemr_ecs/stack.py",
                "network": "openemr_ecs/network.py",
                "compute": "openemr_ecs/compute.py",
                "database": "openemr_ecs/database.py",
                "storage": "openemr_ecs/storage.py",
                "security": "openemr_ecs/security.py",
                "monitoring": "openemr_ecs/monitoring.py",
                "analytics": "openemr_ecs/analytics.py",
            },
            "details": "docs/reference/architecture.md",
        }

    def topic(self, topic: str) -> dict[str, Any]:
        """Return a curated topic summary and available local sources."""

        if not topic or len(topic) > MAX_TOPIC_LENGTH:
            raise KnowledgeError(f"Topic length must be between 1 and {MAX_TOPIC_LENGTH} characters")
        normalized = _normalize_topic(topic)
        aliases = {
            "cache": "elasticache",
            "database": "aurora",
            "ecs": "ecs-fargate",
            "fargate": "ecs-fargate",
            "floci": "floci-emulation",
            "import": "openemr-import",
            "mcp": "knowledge-mcp",
            "route-53": "route53",
            "valkey": "elasticache",
        }
        normalized = aliases.get(normalized, normalized)
        entry = _TOPICS.get(normalized)
        if entry is None:
            raise KnowledgeError(f"Unknown topic. Choose one of: {', '.join(sorted(_TOPICS))}")
        indexed = {_relative(self.root, path) for path in self._safe_files()}
        sources = list(entry["sources"])
        unavailable = sorted(source for source in sources if source not in indexed)
        if unavailable:
            raise KnowledgeError(f"Curated topic source is unavailable under the read policy: {', '.join(unavailable)}")
        for source in sources:
            self._read_raw(source)
        return {
            "topic": normalized,
            "summary": entry["summary"],
            "sources": sources,
            "search_terms": normalized.replace("-", " ").split(),
        }

    def documentation_index(self) -> dict[str, Any]:
        """Return every searchable document and verify all curated sources."""

        indexed = sorted(_relative(self.root, path) for path in self._safe_files(validate_documents=True))
        indexed_set = set(indexed)
        documents = [path for path in indexed if Path(path).suffix.lower() in _DOCUMENTATION_EXTENSIONS]
        curated_sources = sorted({source for entry in _TOPICS.values() for source in entry["sources"]})
        unavailable = [source for source in curated_sources if source not in indexed_set]
        if unavailable:
            raise KnowledgeError(f"Curated source is unavailable under the read policy: {', '.join(unavailable)}")
        for source in curated_sources:
            self._read_raw(source)
        return {
            "document_count": len(documents),
            "documents": documents,
            "curated_source_count": len(curated_sources),
            "curated_sources": curated_sources,
            "searchable_file_count": len(indexed),
            "file_limit": MAX_INDEXED_FILES,
            "traversal_entry_limit": MAX_TRAVERSED_ENTRIES,
        }

    def search(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        """Search policy-approved repository text with a small relevance ranking."""

        query = query.strip()
        if len(query) < 2 or len(query) > MAX_QUERY_LENGTH:
            raise KnowledgeError(f"Query length must be between 2 and {MAX_QUERY_LENGTH} characters")
        if limit < 1 or limit > MAX_RESULTS:
            raise KnowledgeError(f"Result limit must be between 1 and {MAX_RESULTS}")
        terms = tuple(dict.fromkeys(re.findall(r"[a-z0-9][a-z0-9_-]*", query.lower())))
        if not terms or len(terms) > MAX_QUERY_TERMS:
            raise KnowledgeError(f"Query must contain 1 to {MAX_QUERY_TERMS} search terms")

        ranked: list[tuple[int, str, int, str]] = []
        phrase = query.lower()
        scanned_bytes = 0
        scanned_lines = 0
        search_exhausted = False
        for path in self._safe_files():
            relative = _relative(self.root, path)
            try:
                text = self._read(relative)
            except KnowledgeError:
                continue
            encoded_size = len(text.encode("utf-8"))
            if scanned_bytes + encoded_size > MAX_SEARCH_TOTAL_BYTES:
                break
            scanned_bytes += encoded_size
            path_lower = relative.lower()
            file_ranked: list[tuple[int, str, int, str]] = []
            for line_number, line in enumerate(text.splitlines(), start=1):
                if scanned_lines >= MAX_SEARCH_TOTAL_LINES:
                    search_exhausted = True
                    break
                scanned_lines += 1
                lowered = line.lower()
                matched = sum(term in lowered for term in terms)
                if matched == 0:
                    continue
                score = matched * 3
                if phrase in lowered:
                    score += 8
                if line.lstrip().startswith("#"):
                    score += 3
                score += sum(term in path_lower for term in terms)
                excerpt = re.sub(r"\s+", " ", line).strip()[:280]
                if excerpt:
                    file_ranked.append((score, relative, line_number, excerpt))
                    file_ranked.sort(key=lambda item: (-item[0], item[2]))
                    del file_ranked[MAX_MATCHES_PER_FILE:]
            if not file_ranked and any(term in path_lower for term in terms):
                path_score = sum(term in path_lower for term in terms)
                file_ranked.append((path_score, relative, 1, f"Path match: {relative}"))
            ranked.extend(file_ranked)
            if search_exhausted:
                break

        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        per_file: defaultdict[str, int] = defaultdict(int)
        results: list[dict[str, Any]] = []
        for score, relative, line_number, excerpt in ranked:
            if per_file[relative] >= MAX_MATCHES_PER_FILE:
                continue
            per_file[relative] += 1
            results.append(
                {
                    "path": relative,
                    "line": line_number,
                    "score": score,
                    "excerpt": excerpt,
                }
            )
            if len(results) == limit:
                break
        return results

    def read_file(
        self,
        path: str,
        *,
        start_line: int = 1,
        max_lines: int = 100,
    ) -> dict[str, Any]:
        """Read a bounded line range from an approved repository text file."""

        if start_line < 1 or start_line > MAX_START_LINE:
            raise KnowledgeError(f"start_line must be between 1 and {MAX_START_LINE}")
        if max_lines < 1 or max_lines > MAX_READ_LINES:
            raise KnowledgeError(f"max_lines must be between 1 and {MAX_READ_LINES}")
        text = self._read(path)
        lines = text.splitlines()
        start_index = start_line - 1
        selected = lines[start_index : start_index + max_lines]
        content = "\n".join(selected)
        content_limited = len(content) > MAX_READ_CHARS
        if content_limited:
            content = _bounded(content, MAX_READ_CHARS)
        return {
            "path": Path(path).as_posix(),
            "start_line": start_line,
            "end_line": start_line + len(selected) - 1 if selected else start_line - 1,
            "total_lines": len(lines),
            "content": content,
            "truncated": content_limited or start_index + len(selected) < len(lines),
            "content_limit_chars": MAX_READ_CHARS,
        }

    def _version_declarations(self) -> list[dict[str, Any]]:
        inventory_files = []
        fixed_inputs = {
            ".pre-commit-config.yaml",
            "openemr_ecs/constants.py",
            "package-lock.json",
            "package.json",
            "requirements-dev.txt",
            "requirements.txt",
            "scripts/backup-tui/go.mod",
            "tools/credential-rotation/requirements.txt",
            "tools/openemr-import-worker/requirements.in",
            "tools/openemr-import-worker/requirements.txt",
        }
        for path in self._safe_files():
            relative = _relative(self.root, path)
            if relative in fixed_inputs or relative.startswith(".github/workflows/") or path.name == "Dockerfile":
                inventory_files.append(relative)
        if len(inventory_files) > MAX_VERSION_INPUT_FILES:
            raise KnowledgeError(f"Version inventory input exceeds the {MAX_VERSION_INPUT_FILES}-file limit")
        total_bytes = 0
        inventory_contents: dict[str, str] = {}
        for relative in inventory_files:
            content = self._read_raw(relative)
            total_bytes += len(content.encode("utf-8"))
            if total_bytes > MAX_VERSION_TOTAL_BYTES:
                raise KnowledgeError(f"Version inventory input exceeds the {MAX_VERSION_TOTAL_BYTES}-byte limit")
            inventory_contents[relative] = content

        declarations: list[dict[str, Any]] = [
            {
                "identifier": "project:openemr-on-ecs",
                "name": "OpenEMR on ECS",
                "category": "project",
                "declared": self._read("VERSION", max_bytes=128).strip(),
                "definition": "VERSION:1",
                "source_kind": "project-version",
                "constraint": "",
                "metadata": {},
            }
        ]
        try:
            source = InventorySource(
                self.root,
                paths=inventory_files,
                reader=inventory_contents.__getitem__,
            )
            discovered = collect_declarations(
                self.root,
                discover_consumers=False,
                source=source,
            )
        except (OSError, ValueError) as exc:
            raise KnowledgeError(f"Unable to collect the local version inventory: {exc}") from exc
        for declaration in discovered:
            metadata = {
                key: _redact_structured_value(value)
                for key, value in declaration.metadata.items()
                if key in _VERSION_METADATA_FIELDS
            }
            declarations.append(
                {
                    "identifier": _bounded(redact_text(declaration.identifier), 200),
                    "name": _bounded(redact_text(declaration.name), 200),
                    "category": _bounded(redact_text(declaration.category), 80),
                    "declared": _bounded(
                        redact_text(declaration.current),
                        MAX_VERSION_VALUE_CHARS,
                    ),
                    "definition": _bounded(
                        redact_text(declaration.definition),
                        MAX_VERSION_VALUE_CHARS,
                    ),
                    "source_kind": _bounded(redact_text(declaration.source_kind), 100),
                    "constraint": _bounded(
                        redact_text(declaration.constraint or ""),
                        MAX_VERSION_VALUE_CHARS,
                    ),
                    "metadata": metadata,
                }
            )
        declarations.sort(key=lambda item: (item["category"], item["name"].lower(), item["definition"]))
        return declarations

    def versions(self, categories: list[str] | None = None) -> dict[str, Any]:
        """Return declared versions without installed-package or network lookups."""

        requested = categories or []
        if len(requested) > 20:
            raise KnowledgeError("At most 20 categories may be requested")
        selected = set()
        for item in requested:
            normalized = item.strip().lower()
            if not normalized or len(normalized) > 40 or re.fullmatch(r"[a-z0-9-]+", normalized) is None:
                raise KnowledgeError("Version categories must be 1 to 40 lowercase letters, digits, or hyphens")
            selected.add(normalized)
        discovered = self._version_declarations()
        matched = [item for item in discovered if not selected or item["category"] in selected]
        declarations = matched[:MAX_VERSION_COMPONENTS]
        return {
            "online_lookup": False,
            "count": len(declarations),
            "matched_count": len(matched),
            "categories": sorted({item["category"] for item in matched}),
            "components": declarations,
            "component_limit": MAX_VERSION_COMPONENTS,
            "input_file_limit": MAX_VERSION_INPUT_FILES,
            "input_byte_limit": MAX_VERSION_TOTAL_BYTES,
            "truncated": len(matched) > len(declarations),
        }

    def configuration(self) -> dict[str, Any]:
        """Return CDK context keys and redacted defaults."""

        try:
            data = json.loads(self._read_raw("cdk.json"))
        except json.JSONDecodeError as exc:
            raise KnowledgeError("cdk.json is not valid JSON") from exc
        if not isinstance(data, dict):
            raise KnowledgeError("cdk.json must contain an object")
        context = data.get("context", {})
        if not isinstance(context, dict):
            raise KnowledgeError("cdk.json context must be an object")
        sorted_items = sorted(context.items())
        entries = []
        for key, value in sorted_items[:MAX_CONFIGURATION_ENTRIES]:
            rendered = json.dumps(
                _redact_structured_value(value, key=key),
                sort_keys=True,
            )
            entries.append(
                {
                    "key": _bounded(redact_text(key), 200),
                    "default": _bounded(rendered, MAX_CONFIGURATION_VALUE_CHARS),
                    "type": type(value).__name__,
                }
            )
        return {
            "source": "cdk.json",
            "count": len(context),
            "returned_count": len(entries),
            "entries": entries,
            "truncated": len(context) > len(entries),
            "entry_limit": MAX_CONFIGURATION_ENTRIES,
            "reference": "docs/guides/configuration.md",
        }

    def operational_commands(self) -> list[dict[str, Any]]:
        """Return discovery metadata; this method never executes a command."""

        return [
            {
                "purpose": "Configuration synthesis matrix",
                "command": "python3 scripts/test-cdk-synthesis.py",
                "risk": "local synthesis only; writes generated cdk.out content",
            },
            {
                "purpose": "Credential rotation",
                "command": "scripts/run-credential-rotation.sh",
                "risk": "mutates AWS and OpenEMR credentials; explicit review required",
            },
            {
                "purpose": "Backup",
                "command": "scripts/create-backup.sh",
                "risk": "creates AWS Backup recovery points and costs",
            },
            {
                "purpose": "Restore",
                "command": "scripts/restore-from-backup.sh",
                "risk": "destructive/high-risk recovery operation",
            },
            {
                "purpose": "Inspect an OpenEMR import source",
                "command": "python3 -m tools.openemr_import inspect --help",
                "risk": "local, offline, read-only source inspection",
            },
            {
                "purpose": "Plan a guarded OpenEMR import",
                "command": "python3 -m tools.openemr_import plan --help",
                "risk": "local, offline planning; writes no patient data",
            },
            {
                "purpose": "Execute a guarded OpenEMR import",
                "command": "python3 -m tools.openemr_import execute --help",
                "risk": ("destructive AWS/OpenEMR mutation and downtime; all documented confirmations are required"),
            },
            {
                "purpose": "Reconcile an uncertain import task launch",
                "command": "python3 -m tools.openemr_import reconcile-launch --help",
                "risk": "may restore service and remove a migration scope after bounded verification",
            },
            {
                "purpose": "Recover an interrupted import from its local baseline",
                "command": "python3 -m tools.openemr_import recover --help",
                "risk": "destructively restores the pre-import database and EFS baseline",
            },
            {
                "purpose": "Finalize a successful OpenEMR import",
                "command": "python3 -m tools.openemr_import finalize --help",
                "risk": "restarts the application and resumes autoscaling",
            },
            {
                "purpose": "Abort a failed OpenEMR import",
                "command": "python3 -m tools.openemr_import abort --help",
                "risk": "restores service and deletes failed-attempt artifacts after verification",
            },
            {
                "purpose": "Read guarded OpenEMR import status",
                "command": "python3 -m tools.openemr_import status --help",
                "risk": "read-only AWS and local-state inspection",
            },
            {
                "purpose": "Clean up a completed OpenEMR import",
                "command": "python3 -m tools.openemr_import cleanup --help",
                "risk": "permanently deletes rollback and staging artifacts",
            },
            {
                "purpose": "Generate the live E2E timing report",
                "command": "python3 -m tools.live_e2e report",
                "risk": "local-only deterministic report generation; does not contact AWS",
            },
            {
                "purpose": "Preflight a live AWS E2E run",
                "command": "python3 -m tools.live_e2e preflight --help",
                "risk": "read-only AWS checks and local synthesis; requires explicit scope confirmations",
            },
            {
                "purpose": "Run an approved live AWS E2E lifecycle",
                "command": "python3 -m tools.live_e2e run --help",
                "risk": "creates billable AWS resources and then deletes the owned stack",
            },
            {
                "purpose": "Clean up an interrupted live AWS E2E run",
                "command": "python3 -m tools.live_e2e cleanup --help",
                "risk": "destructively deletes only resources bound to the exact E2E ownership marker",
            },
            {
                "purpose": "Run the local Floci live E2E emulation suite",
                "command": "pytest tests/tools/test_floci_live_e2e_full.py -m floci -q",
                "risk": "local Docker/CDK emulation only; real AWS endpoints and credentials are rejected",
            },
            {
                "purpose": "Stack cleanup",
                "command": "scripts/cleanup-all-stacks.sh",
                "risk": "destructive; review target resources before use",
            },
        ]
