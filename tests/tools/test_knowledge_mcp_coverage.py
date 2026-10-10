"""Fail-closed and bounds tests for the offline knowledge server."""

from __future__ import annotations

import json
import os
import runpy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from tools.knowledge_mcp import knowledge as knowledge_module
from tools.knowledge_mcp import server as server_module
from tools.knowledge_mcp.knowledge import _TOPICS, KnowledgeError, RepositoryKnowledge


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Create a synthetic repository containing every curated source."""

    root = tmp_path / "repository"
    (root / "openemr_ecs").mkdir(parents=True)
    (root / "openemr_ecs" / "constants.py").write_text('OPENEMR_VERSION = "8.1.1"\n', encoding="utf-8")
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "requirements.txt").write_text("requests==2.0.0\n", encoding="utf-8")
    (root / "requirements-dev.txt").write_text("pytest==9.0.0\n", encoding="utf-8")
    (root / "cdk.json").write_text(json.dumps({"context": {"feature": True}}), encoding="utf-8")
    for source in sorted({item for topic in _TOPICS.values() for item in topic["sources"]}):
        path = root / source
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Synthetic source\n\n{source}\n", encoding="utf-8")
    return root


def test_root_must_be_a_directory(tmp_path: Path) -> None:
    with pytest.raises(KnowledgeError, match="not a directory"):
        RepositoryKnowledge(tmp_path / "missing")


class _BrokenEntry:
    def __init__(self, entry: os.DirEntry[str]):
        self.name = entry.name

    def is_symlink(self) -> bool:
        return False

    def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
        raise PermissionError(f"cannot stat {self.name}")


def _scandir_breaking(names: set[str]) -> Any:
    real_scandir = os.scandir

    class Iterator_:
        def __init__(self, path: Path):
            self._context = real_scandir(path)

        def __enter__(self) -> Iterator[Any]:
            entries = self._context.__enter__()
            return (_BrokenEntry(entry) if entry.name in names else entry for entry in entries)

        def __exit__(self, *args: object) -> None:
            self._context.__exit__(*args)

    return SimpleNamespace(scandir=Iterator_)


def test_unstattable_entries_are_skipped_during_search(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / "docs" / "hidden-from-stat.md").write_text("unique-stat-marker\n", encoding="utf-8")
    (repo / "docs" / "visible.md").write_text("unique-stat-marker\n", encoding="utf-8")
    monkeypatch.setattr(knowledge_module, "os", _scandir_breaking({"hidden-from-stat.md"}))

    results = RepositoryKnowledge(repo).search("unique-stat-marker")

    assert [item["path"] for item in results] == ["docs/visible.md"]


def test_unstattable_documentation_fails_the_index(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / "docs" / "broken.md").write_text("broken\n", encoding="utf-8")
    monkeypatch.setattr(knowledge_module, "os", _scandir_breaking({"broken.md"}))

    with pytest.raises(KnowledgeError, match="cannot be inspected: docs/broken.md"):
        RepositoryKnowledge(repo).documentation_index()


def test_enumeration_failure_is_reported(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(path: Path) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(knowledge_module, "os", SimpleNamespace(scandir=fail))

    with pytest.raises(KnowledgeError, match="Unable to enumerate approved repository path: denied"):
        RepositoryKnowledge(repo).search("anything")


def test_overlong_file_paths_fail_closed(repo: Path) -> None:
    directory = repo / "docs" / ("a" * 240) / ("b" * 240)
    directory.mkdir(parents=True)
    (directory / "long-file-name.md").write_text("x\n", encoding="utf-8")

    with pytest.raises(KnowledgeError, match="500-character limit"):
        RepositoryKnowledge(repo).search("anything")


def test_topic_requires_every_curated_source(repo: Path) -> None:
    (repo / "openemr_ecs" / "database.py").unlink()

    with pytest.raises(KnowledgeError, match="unavailable under the read policy: openemr_ecs/database.py"):
        RepositoryKnowledge(repo).topic("aurora")


def test_documentation_index_requires_every_curated_source(repo: Path) -> None:
    (repo / "app.py").unlink()

    with pytest.raises(KnowledgeError, match="Curated source is unavailable under the read policy: app.py"):
        RepositoryKnowledge(repo).documentation_index()


def test_search_stops_at_total_byte_budget(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(knowledge_module, "MAX_SEARCH_TOTAL_BYTES", 1)

    assert RepositoryKnowledge(repo).search("synthetic") == []


def test_search_caps_matches_per_file_even_if_enumerated_twice(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = repo / "docs" / "repeat.md"
    path.write_text("".join(f"repeat-marker {number}\n" for number in range(5)), encoding="utf-8")
    knowledge = RepositoryKnowledge(repo)
    monkeypatch.setattr(knowledge, "_safe_files", lambda: [path, path])

    results = knowledge.search("repeat-marker", limit=20)

    assert len(results) == knowledge_module.MAX_MATCHES_PER_FILE
    assert {item["path"] for item in results} == {"docs/repeat.md"}


def test_versions_filter_by_category(repo: Path) -> None:
    result = RepositoryKnowledge(repo).versions(["Python-Dev"])

    assert result["categories"] == ["python-dev"]
    assert [item["identifier"] for item in result["components"]] == ["python:pytest"]
    assert result["matched_count"] == 1


def test_version_inventory_file_limit(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(knowledge_module, "MAX_VERSION_INPUT_FILES", 0)

    with pytest.raises(KnowledgeError, match="0-file limit"):
        RepositoryKnowledge(repo).versions()


def test_version_inventory_byte_limit(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(knowledge_module, "MAX_VERSION_TOTAL_BYTES", 1)

    with pytest.raises(KnowledgeError, match="1-byte limit"):
        RepositoryKnowledge(repo).versions()


def test_version_inventory_wraps_collection_errors(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise ValueError("bad inventory")

    monkeypatch.setattr(knowledge_module, "collect_declarations", fail)

    with pytest.raises(KnowledgeError, match="Unable to collect the local version inventory: bad inventory"):
        RepositoryKnowledge(repo).versions()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{not json", "not valid JSON"),
        ("[]", "must contain an object"),
        ('{"context": []}', "context must be an object"),
    ],
)
def test_configuration_rejects_malformed_cdk_json(repo: Path, content: str, message: str) -> None:
    (repo / "cdk.json").write_text(content, encoding="utf-8")

    with pytest.raises(KnowledgeError, match=message):
        RepositoryKnowledge(repo).configuration()


def test_run_stdio_uses_only_stdio_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    transports: list[str] = []

    class FakeServer:
        def run(self, *, transport: str) -> None:
            transports.append(transport)

    monkeypatch.setattr(server_module, "create_server", lambda root=None: FakeServer())

    server_module.run_stdio()

    assert transports == ["stdio"]


def test_package_entry_point_runs_stdio_server(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(server_module, "run_stdio", lambda: calls.append(True))

    runpy.run_module("tools.knowledge_mcp", run_name="__main__")

    assert calls == [True]
