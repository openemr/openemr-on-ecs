"""Security, bounds, and protocol tests for the repository knowledge server."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from tools import _shared
from tools._shared import ToolError, is_secret_like_path, safe_read_text
from tools.knowledge_mcp import knowledge as knowledge_module
from tools.knowledge_mcp.knowledge import (
    _TOPICS,
    MAX_CONFIGURATION_ENTRIES,
    MAX_READ_CHARS,
    MAX_VERSION_COMPONENTS,
    KnowledgeError,
    RepositoryKnowledge,
)
from tools.knowledge_mcp.server import create_server
from tools.version_audit.inventory import collect_declarations


@pytest.fixture
def knowledge_root(tmp_path: Path) -> Path:
    """Create a small synthetic repository with no real operational data."""

    (tmp_path / "openemr_ecs").mkdir()
    (tmp_path / "openemr_ecs" / "constants.py").write_text(
        'class StackConstants:\n    OPENEMR_VERSION = "8.1.1"\n    EMR_SERVERLESS_RELEASE_LABEL = "emr-7.13.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "tools" / "credential-rotation").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("requests==2.34.2\n", encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text("pytest>=9\n", encoding="utf-8")
    (tmp_path / "tools" / "credential-rotation" / "requirements.txt").write_text(
        "boto3>=1\n",
        encoding="utf-8",
    )
    (tmp_path / "cdk.json").write_text(
        json.dumps(
            {
                "app": "python3 app.py",
                "context": {
                    "enable_monitoring_alarms": False,
                    "password": "must-not-leak",
                    "nested": {"password": {"value": "nested-secret-must-not-leak"}},
                    "@aws-cdk/core:checkSecretUsage": True,
                    "certificate_arn": (
                        "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000"
                    ),
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "# Synthetic OpenEMR\n\nSecure resilient healthcare deployment.\n",
        encoding="utf-8",
    )
    (tmp_path / "ARCHITECTURE.md").write_text(
        "# Architecture\n\n## Aurora database\n\nPrivate encrypted storage.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "guide.md").write_text(
        "# Credential rotation\n\nRotate credentials with explicit approval.\n"
        "password = should-not-leak\n"
        "DB_PASSWORD = prefixed-secret-must-not-leak\n"
        "    yaml_password: indented-secret-must-not-leak\n"
        "export API_TOKEN=exported-secret-must-not-leak\n"
        "DB_PASS=shell-pass-must-not-leak\n"
        "dbPassword=camel-secret-must-not-leak\n"
        "block_secret: |\n"
        "  multiline-secret-must-not-leak\n"
        "PASSED=0\n"
        "compass=north\n"
        "bypass_mode=false\n"
        "key = AKIAABCDEFGHIJKLMNOP\n"
        '{"password":"inline-must-not-leak","api_key":"also-private",'
        '"source":"https://user:pass@example.test/archive?token=query-secret"}\n'
        "signed_url=https://example.test/object?X-Amz-Signature=signed-secret"
        "&X-Amz-Security-Token=session-secret\n"
        "token_without_label ghp_abcdefghijklmnopqrstuvwxyz123456\n"
        "-----BEGIN PRIVATE KEY-----\n"
        "private-key-material\n"
        "-----END PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    for source in sorted({item for topic in _TOPICS.values() for item in topic["sources"]}):
        path = tmp_path / source
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Synthetic source\n\n{source}\n", encoding="utf-8")
    return tmp_path


def test_overview_architecture_topics_and_deterministic_search(knowledge_root: Path) -> None:
    knowledge = RepositoryKnowledge(knowledge_root)

    assert knowledge.overview()["version"] == "9.9.9"
    assert knowledge.architecture()["data_services"]["database"].startswith("Aurora")
    assert knowledge.topic("database")["topic"] == "aurora"
    first = knowledge.search("credential rotation", limit=3)
    second = knowledge.search("credential rotation", limit=3)

    assert first == second
    assert first[0]["path"] == "docs/guide.md"
    assert "Credential rotation" in first[0]["excerpt"]
    assert all(len(item["excerpt"]) <= 280 for item in first)


def test_search_enforces_global_line_bound(
    knowledge_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = knowledge_root / "docs" / "bounded.md"
    path.write_text("first\nsecond\nlate-marker\n", encoding="utf-8")
    knowledge = RepositoryKnowledge(knowledge_root)
    monkeypatch.setattr(knowledge, "_safe_files", lambda: [path])
    monkeypatch.setattr(knowledge_module, "MAX_SEARCH_TOTAL_LINES", 2)

    assert knowledge.search("late-marker") == []


def test_safe_read_is_bounded_and_redacted(knowledge_root: Path) -> None:
    knowledge = RepositoryKnowledge(knowledge_root)

    result = knowledge.read_file("docs/guide.md", start_line=1, max_lines=20)

    for secret in (
        "should-not-leak",
        "prefixed-secret-must-not-leak",
        "indented-secret-must-not-leak",
        "exported-secret-must-not-leak",
        "shell-pass-must-not-leak",
        "camel-secret-must-not-leak",
        "multiline-secret-must-not-leak",
        "AKIAABCDEFGHIJKLMNOP",
        "inline-must-not-leak",
        "also-private",
        "user:pass",
        "query-secret",
        "signed-secret",
        "session-secret",
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "private-key-material",
    ):
        assert secret not in result["content"]
    assert "<redacted>" in result["content"]
    assert "<private-key-redacted>" in result["content"]
    assert "PASSED=0" in result["content"]
    assert "compass=north" in result["content"]
    assert "bypass_mode=false" in result["content"]
    assert knowledge.search("must-not-leak") == []

    (knowledge_root / "docs" / "wide.md").write_text("x" * 100_000, encoding="utf-8")
    wide = knowledge.read_file("docs/wide.md", max_lines=1)
    assert len(wide["content"]) <= MAX_READ_CHARS
    assert wide["truncated"] is True


def test_configuration_and_versions_are_offline_redacted_and_bounded(knowledge_root: Path) -> None:
    knowledge = RepositoryKnowledge(knowledge_root)

    configuration = knowledge.configuration()
    versions = knowledge.versions()

    serialized = json.dumps(configuration)
    assert "must-not-leak" not in serialized
    assert "nested-secret-must-not-leak" not in serialized
    assert "123456789012" not in serialized
    entries = {entry["key"]: entry["default"] for entry in configuration["entries"]}
    assert entries["@aws-cdk/core:checkSecretUsage"] == "true"
    assert versions["online_lookup"] is False
    assert any(item["name"] == "requests" for item in versions["components"])
    assert any(item["identifier"] == "container:openemr" for item in versions["components"])

    context = {f"key_{number:03d}": number for number in range(MAX_CONFIGURATION_ENTRIES + 5)}
    (knowledge_root / "cdk.json").write_text(
        json.dumps({"app": "python3 app.py", "context": context}),
        encoding="utf-8",
    )
    limited_configuration = knowledge.configuration()
    assert limited_configuration["returned_count"] == MAX_CONFIGURATION_ENTRIES
    assert limited_configuration["truncated"] is True

    requirements = "".join(f"package-{number:03d}==1.0\n" for number in range(MAX_VERSION_COMPONENTS + 5))
    (knowledge_root / "requirements.txt").write_text(requirements, encoding="utf-8")
    limited_versions = knowledge.versions()
    assert limited_versions["count"] == MAX_VERSION_COMPONENTS
    assert len(limited_versions["components"]) == MAX_VERSION_COMPONENTS
    assert limited_versions["truncated"] is True


def test_documentation_index_covers_every_claimed_source_and_repository_document() -> None:
    knowledge = RepositoryKnowledge()
    index = knowledge.documentation_index()
    tracked = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "*.md",
            "*.rst",
        ],
        cwd=knowledge.root,
        check=True,
        capture_output=True,
        text=True,
    )
    repository_documents = sorted(path for path in tracked.stdout.split("\0") if path)
    claimed_sources = sorted({source for topic in _TOPICS.values() for source in topic["sources"]})

    assert index["documents"] == repository_documents
    assert index["document_count"] == len(repository_documents)
    assert index["curated_sources"] == claimed_sources
    assert index["curated_source_count"] == len(claimed_sources)
    for source in claimed_sources:
        result = knowledge.read_file(source, max_lines=1)
        assert result["path"] == source
    assert knowledge.read_file("e2e-results/history.json", max_lines=1)["path"] == ("e2e-results/history.json")


def test_documentation_index_fails_closed_on_invalid_sources_and_limits(
    knowledge_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge = RepositoryKnowledge(knowledge_root)

    invalid = knowledge_root / "docs" / "invalid-index.md"
    invalid.write_bytes(b"\xff\xfe")
    with pytest.raises(KnowledgeError, match="UTF-8"):
        knowledge.documentation_index()
    invalid.unlink()

    fifo = knowledge_root / "docs" / "index-pipe.md"
    os.mkfifo(fifo)
    with pytest.raises(KnowledgeError, match="regular file"):
        knowledge.documentation_index()
    fifo.unlink()

    linked = knowledge_root / "docs" / "linked-index.md"
    linked.symlink_to("../README.md")
    with pytest.raises(KnowledgeError, match="symlinked"):
        knowledge.documentation_index()
    linked.unlink()

    long_root = knowledge_root / "docs" / ("a" * 120)
    long_path = long_root
    for _ in range(4):
        long_path /= "b" * 120
    long_path.mkdir(parents=True)
    (long_path / "guide.md").write_text("long-path-marker\n", encoding="utf-8")
    with pytest.raises(KnowledgeError, match="character limit"):
        knowledge.documentation_index()
    shutil.rmtree(long_root)

    with monkeypatch.context() as patch:
        patch.setattr(knowledge_module, "MAX_TRAVERSED_ENTRIES", 2)
        with pytest.raises(KnowledgeError, match="traversal exceeds"):
            knowledge.documentation_index()

    with monkeypatch.context() as patch:
        patch.setattr(knowledge_module, "MAX_INDEXED_FILES", 1)
        with pytest.raises(KnowledgeError, match="index exceeds"):
            knowledge.documentation_index()


def test_version_inventory_is_a_bounded_projection_of_authoritative_inventory() -> None:
    knowledge = RepositoryKnowledge()
    inventory = knowledge.versions()
    expected = {
        declaration.identifier: declaration
        for declaration in collect_declarations(
            knowledge.root,
            discover_consumers=False,
        )
    }
    actual = {
        component["identifier"]: component
        for component in inventory["components"]
        if component["identifier"] != "project:openemr-on-ecs"
    }

    assert inventory["truncated"] is False
    assert actual.keys() == expected.keys()
    for identifier, declaration in expected.items():
        assert actual[identifier]["category"] == declaration.category
        assert actual[identifier]["declared"] == declaration.current
        assert actual[identifier]["source_kind"] == declaration.source_kind
    assert actual["container:openemr"]["metadata"]["arm64_digest"].startswith("sha256:")
    assert {
        "python:botocore",
        "python:jmespath",
        "python:python-dateutil",
        "python:s3transfer",
        "python:six",
        "python:urllib3",
    } <= actual.keys()
    assert not any(
        identifier.startswith("python-invalid:tools/openemr-import-worker/requirements.txt") for identifier in actual
    )


def test_version_inventory_collects_the_validated_snapshot(
    knowledge_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge = RepositoryKnowledge(knowledge_root)
    original_collect = knowledge_module.collect_declarations

    def mutate_after_validation(*args: object, **kwargs: object) -> object:
        (knowledge_root / "requirements.txt").write_text(
            "changed-after-validation==9.9.9\n",
            encoding="utf-8",
        )
        return original_collect(*args, **kwargs)

    monkeypatch.setattr(
        knowledge_module,
        "collect_declarations",
        mutate_after_validation,
    )
    components = knowledge.versions()["components"]

    assert any(item["identifier"] == "python:requests" for item in components)
    assert all(item["identifier"] != "python:changed-after-validation" for item in components)


def test_curated_sources_and_commands_match_current_scope() -> None:
    knowledge = RepositoryKnowledge()
    declared_version = (Path(__file__).resolve().parents[2] / "VERSION").read_text(encoding="utf-8").strip()

    assert knowledge.overview()["version"] == declared_version
    assert knowledge.topic("mcp")["topic"] == "knowledge-mcp"
    assert "docs/maintainers/knowledge-mcp.md" in knowledge.topic("knowledge-mcp")["sources"]
    assert "docs/maintainers/knowledge-mcp.md" in knowledge.overview()["primary_guides"]
    assert knowledge.topic("import")["topic"] == "openemr-import"
    assert "docs/guides/importing-openemr.md" in knowledge.overview()["primary_guides"]
    assert knowledge.topic("live e2e")["topic"] == "live-e2e"
    assert "docs/maintainers/live-e2e.md" in knowledge.overview()["primary_guides"]
    assert knowledge.topic("floci")["topic"] == "floci-emulation"
    assert "docs/maintainers/floci.md" in knowledge.overview()["primary_guides"]
    for source in knowledge.topic("floci")["sources"]:
        knowledge.read_file(source, max_lines=1)
    for source in knowledge.topic("credential-rotation")["sources"]:
        knowledge.read_file(source, max_lines=1)
    serialized = json.dumps(knowledge.operational_commands()).lower()
    assert "openemr import" in serialized
    assert "live e2e" in serialized
    assert "floci" in serialized


def test_path_traversal_symlink_secrets_encoding_and_size_are_rejected(
    knowledge_root: Path,
    tmp_path: Path,
) -> None:
    knowledge = RepositoryKnowledge(knowledge_root)
    outside = tmp_path.parent / "outside-mcp-test.md"
    outside.write_text("outside-marker\n", encoding="utf-8")
    outside_directory = tmp_path.parent / "outside-mcp-directory"
    outside_directory.mkdir(exist_ok=True)
    (outside_directory / "hidden.md").write_text("outside-directory-marker\n", encoding="utf-8")
    (knowledge_root / "docs" / "escape.md").symlink_to(outside)
    (knowledge_root / "docs" / "internal-alias.md").symlink_to("../README.md")
    (knowledge_root / "docs" / "external-directory").symlink_to(outside_directory, target_is_directory=True)
    (knowledge_root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (knowledge_root / "docs" / ".aws").mkdir()
    (knowledge_root / "docs" / ".aws" / "config.md").write_text("aws-state-marker\n", encoding="utf-8")
    for directory in (".docker", ".kube", ".ssh"):
        (knowledge_root / "docs" / directory).mkdir()
        (knowledge_root / "docs" / directory / "config.md").write_text(
            f"{directory}-state-marker\n",
            encoding="utf-8",
        )
    (knowledge_root / "docs" / ".kube" / "config.md").write_text(
        "uses: hidden/action@hidden-revision-must-not-leak\n",
        encoding="utf-8",
    )
    (knowledge_root / ".github" / "workflows" / "leak.yml").symlink_to("../../docs/.kube/config.md")
    (knowledge_root / "docs" / "credentials.txt").write_text("secret\n", encoding="utf-8")
    (knowledge_root / "docs" / "credential-backup.txt").write_text(
        "backup-secret\n",
        encoding="utf-8",
    )
    (knowledge_root / "docs" / ".hidden.md").write_text("hidden-marker\n", encoding="utf-8")
    (knowledge_root / "docs" / "unsupported.bin").write_bytes(b"text")
    (knowledge_root / "docs" / "invalid.md").write_bytes(b"\xff\xfe")
    (knowledge_root / "docs" / "large.md").write_text(
        "x" * 256_001,
        encoding="utf-8",
    )

    for path in (
        "../outside-mcp-test.md",
        str(outside),
        "docs/escape.md",
        "docs/internal-alias.md",
        "docs/external-directory/hidden.md",
        ".env",
        "docs/.aws/config.md",
        "docs/.docker/config.md",
        "docs/.kube/config.md",
        "docs/.ssh/config.md",
        "docs/credentials.txt",
        "docs/credential-backup.txt",
        "docs/.hidden.md",
        "docs/unsupported.bin",
        "docs/invalid.md",
        "docs/large.md",
    ):
        try:
            knowledge.read_file(path)
        except KnowledgeError:
            pass
        else:
            pytest.fail(f"unsafe path was accepted: {path}")

    assert knowledge.search("outside-directory-marker") == []
    assert knowledge.search("aws-state-marker") == []
    assert knowledge.search("hidden-marker") == []
    assert "hidden-revision-must-not-leak" not in json.dumps(knowledge.versions())
    assert is_secret_like_path(Path("docs/credential-backup.txt"))
    assert not is_secret_like_path(Path("docs/credential-rotation.md"))


def test_safe_read_resists_swaps_growth_and_special_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    docs = root / "docs"
    docs.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("outside-marker\n", encoding="utf-8")

    swap_target = docs / "swap.md"
    swap_target.write_text("inside-marker\n", encoding="utf-8")
    original_open = os.open
    swapped = False

    def swap_before_open(
        path: str | bytes | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if path == "swap.md" and dir_fd is not None and not swapped:
            swapped = True
            swap_target.unlink()
            swap_target.symlink_to(outside)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    with monkeypatch.context() as patch:
        patch.setattr(_shared.os, "open", swap_before_open)
        with pytest.raises(ToolError, match="safely read"):
            safe_read_text(root, "docs/swap.md")

    outside_directory = tmp_path / "outside-directory"
    outside_directory.mkdir()
    (outside_directory / "file.md").write_text("outside-directory-marker\n", encoding="utf-8")
    race_directory = root / "race"
    race_directory.mkdir()
    (race_directory / "file.md").write_text("inside-directory-marker\n", encoding="utf-8")
    original_directory = root / "race-original"
    directory_swapped = False

    def swap_directory_before_open(
        path: str | bytes | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal directory_swapped
        if path == "race" and dir_fd is not None and not directory_swapped:
            directory_swapped = True
            race_directory.rename(original_directory)
            race_directory.symlink_to(outside_directory, target_is_directory=True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    with monkeypatch.context() as patch:
        patch.setattr(_shared.os, "open", swap_directory_before_open)
        with pytest.raises(ToolError, match="safely read"):
            safe_read_text(root, "race/file.md")

    growing = docs / "growing.md"
    growing.write_text("safe", encoding="utf-8")
    original_read = os.read
    grew = False

    def grow_before_read(descriptor: int, size: int) -> bytes:
        nonlocal grew
        if not grew:
            grew = True
            with growing.open("ab") as handle:
                handle.write(b"x" * 100)
        return original_read(descriptor, size)

    with monkeypatch.context() as patch:
        patch.setattr(_shared.os, "read", grow_before_read)
        with pytest.raises(ToolError, match="exceeded"):
            safe_read_text(root, "docs/growing.md", max_bytes=20)

    fifo = docs / "named-pipe.md"
    os.mkfifo(fifo)
    with pytest.raises(ToolError, match="regular file"):
        safe_read_text(root, "docs/named-pipe.md")

    close_target = docs / "close.md"
    close_target.write_text("close-marker\n", encoding="utf-8")
    original_close = os.close
    closed_descriptors: list[int] = []

    def fail_first_close(descriptor: int) -> None:
        closed_descriptors.append(descriptor)
        original_close(descriptor)
        if len(closed_descriptors) == 1:
            raise OSError("simulated close failure")

    with monkeypatch.context() as patch:
        patch.setattr(_shared.os, "close", fail_first_close)
        assert safe_read_text(root, "docs/close.md") == "close-marker\n"
    assert len(closed_descriptors) == 3


def test_limits_and_unknown_topics_return_bounded_errors(knowledge_root: Path) -> None:
    knowledge = RepositoryKnowledge(knowledge_root)

    with pytest.raises(KnowledgeError, match="Query length"):
        knowledge.search("x")
    with pytest.raises(KnowledgeError, match="Result limit"):
        knowledge.search("architecture", limit=21)
    with pytest.raises(KnowledgeError, match="search terms"):
        knowledge.search("one two three four five six seven eight nine")
    with pytest.raises(KnowledgeError, match="max_lines"):
        knowledge.read_file("README.md", max_lines=201)
    with pytest.raises(KnowledgeError, match="start_line"):
        knowledge.read_file("README.md", start_line=1_000_001)
    with pytest.raises(KnowledgeError, match="Path length"):
        knowledge.read_file(f"docs/{'x' * 500}.md")
    with pytest.raises(KnowledgeError, match="At most 20"):
        knowledge.versions(["project"] * 21)
    with pytest.raises(KnowledgeError, match="Version categories"):
        knowledge.versions(["NOT VALID"])
    with pytest.raises(KnowledgeError, match="Unknown topic"):
        knowledge.topic("not-a-topic")
    with pytest.raises(KnowledgeError, match="Topic length"):
        knowledge.topic("x" * 81)


def test_all_knowledge_operations_leave_repository_unchanged(knowledge_root: Path) -> None:
    knowledge = RepositoryKnowledge(knowledge_root)
    before = {
        path.relative_to(knowledge_root).as_posix(): path.read_bytes()
        for path in knowledge_root.rglob("*")
        if path.is_file()
    }

    knowledge.overview()
    knowledge.architecture()
    knowledge.topic("configuration")
    knowledge.search("OpenEMR")
    knowledge.read_file("README.md")
    knowledge.documentation_index()
    knowledge.configuration()
    knowledge.versions()
    knowledge.operational_commands()

    after = {
        path.relative_to(knowledge_root).as_posix(): path.read_bytes()
        for path in knowledge_root.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_normal_retrieval_uses_no_network_or_subprocess(
    knowledge_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("external access is forbidden")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    knowledge = RepositoryKnowledge(knowledge_root)

    knowledge.overview()
    knowledge.architecture()
    knowledge.topic("backup")
    knowledge.search("OpenEMR")
    knowledge.read_file("README.md")
    knowledge.documentation_index()
    knowledge.configuration()
    knowledge.versions()
    knowledge.operational_commands()


def test_fastmcp_tools_resources_and_read_only_annotations(knowledge_root: Path) -> None:
    async def exercise_server() -> None:
        async with Client(create_server(knowledge_root)) as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
            results = [
                await client.call_tool("project_overview", {}),
                await client.call_tool("architecture_map", {}),
                await client.call_tool("get_topic", {"topic": "efs"}),
                await client.call_tool("search_repository", {"query": "OpenEMR", "limit": 2}),
                await client.call_tool(
                    "read_repository_file",
                    {"path": "README.md", "start_line": 1, "max_lines": 2},
                ),
                await client.call_tool("documentation_index", {}),
                await client.call_tool("version_inventory", {}),
                await client.call_tool("configuration_reference", {}),
                await client.call_tool("discover_operational_commands", {}),
            ]
            overview_resource = await client.read_resource("openemr://overview")
            architecture_resource = await client.read_resource("openemr://architecture")
            documentation_resource = await client.read_resource("openemr://documentation-index")

            assert len(tools) == 9
            assert all(tool.annotations and tool.annotations.readOnlyHint for tool in tools)
            assert all(tool.annotations and tool.annotations.destructiveHint is False for tool in tools)
            assert all(tool.annotations and tool.annotations.idempotentHint for tool in tools)
            assert all(tool.annotations and tool.annotations.openWorldHint is False for tool in tools)
            assert {str(resource.uri) for resource in resources} == {
                "openemr://architecture",
                "openemr://documentation-index",
                "openemr://overview",
            }
            assert all(result.is_error is False for result in results)
            assert results[0].data["version"] == "9.9.9"
            assert results[2].data["topic"] == "efs"
            assert json.loads(overview_resource[0].text)["version"] == "9.9.9"
            assert json.loads(architecture_resource[0].text)["data_services"]["database"].startswith("Aurora")
            assert "README.md" in json.loads(documentation_resource[0].text)["documents"]

    asyncio.run(exercise_server())


def test_stdio_server_startup_and_protocol_smoke() -> None:
    repository = Path(__file__).resolve().parents[2]

    async def exercise_stdio_server() -> None:
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "tools.knowledge_mcp"],
            env={"PYTHONPATH": str(repository)},
            cwd=str(repository),
        )
        async with Client(transport, timeout=10) as client:
            tools = await client.list_tools()
            overview = await client.call_tool("project_overview", {})

            assert {tool.name for tool in tools} >= {
                "documentation_index",
                "project_overview",
                "read_repository_file",
                "search_repository",
            }
            assert overview.is_error is False
            assert overview.data["version"] == (repository / "VERSION").read_text(encoding="utf-8").strip()

    asyncio.run(asyncio.wait_for(exercise_stdio_server(), timeout=15))
