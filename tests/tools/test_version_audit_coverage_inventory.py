"""Edge-case tests for repository declaration discovery in the version audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools._shared import ToolError
from tools.version_audit import inventory
from tools.version_audit.inventory import (
    InventorySource,
    collect_action_declarations,
    collect_declarations,
    collect_go_declarations,
    collect_node_declarations,
    collect_precommit_declarations,
    collect_python_declarations,
    collect_stack_platform_declarations,
    collect_workflow_toolchains,
)


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "openemr_ecs").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "cdk.json").write_text('{"context": {}}\n', encoding="utf-8")
    return root


def _restricted(root: Path, files: dict[str, str]) -> InventorySource:
    return InventorySource(root, paths=files, reader=files.__getitem__)


def test_inventory_source_requires_paths_and_reader_together(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="provided together"):
        InventorySource(tmp_path, paths=["requirements.txt"])
    with pytest.raises(ValueError, match="provided together"):
        InventorySource(tmp_path, reader=lambda relative: "")


@pytest.mark.parametrize("value", ["/etc/passwd", "../outside.txt", ""])
def test_inventory_source_rejects_non_relative_paths(tmp_path: Path, value: str) -> None:
    with pytest.raises(ToolError, match="repository-relative"):
        InventorySource(tmp_path, paths=[value], reader=lambda relative: "")


def test_restricted_source_enforces_its_closed_path_set(tmp_path: Path) -> None:
    source = _restricted(tmp_path, {"requirements.txt": "demo==1\n", "notes.md": "x"})

    assert source.restricted is True
    assert source.contains(tmp_path / "requirements.txt") is True
    assert source.contains(tmp_path / "other.txt") is False
    assert source.contains(tmp_path.parent / "outside.txt") is False
    assert source.read_text(Path("requirements.txt")) == "demo==1\n"
    assert source.resolve(Path("requirements.txt"), allowed_extensions={".txt"}) == (
        tmp_path.resolve() / "requirements.txt"
    )
    with pytest.raises(ToolError, match="outside the approved input set"):
        source.resolve(Path("other.txt"))
    with pytest.raises(ToolError, match="unsupported extension"):
        source.resolve(Path("notes.md"), allowed_extensions={".txt"})
    with pytest.raises(ToolError, match="outside the approved input set"):
        source.read_text(Path("other.txt"))
    with pytest.raises(ToolError, match="escapes the repository root"):
        source.read_text(tmp_path.parent / "outside.txt")


def test_unrestricted_source_exposes_no_selected_paths(tmp_path: Path) -> None:
    source = InventorySource(tmp_path)

    assert source.restricted is False
    assert source.selected_paths() == ()


def test_consumer_discovery_skips_short_values_and_undecodable_files(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "binary.txt").write_bytes(b"\xff\xfe 4.2.0")
    (root / "notes.md").write_text("uses 4.2.0\n", encoding="utf-8")

    assert inventory._discover_value_consumers(root, "4", ()) == ()
    assert inventory._discover_value_consumers(root, "4.2.0", ("requirements.txt:1",)) == ("notes.md:1",)


def test_python_inventory_handles_editables_options_urls_and_includes(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "requirements.txt").write_text(
        "-r requirements.txt\n"
        "--index-url https://pypi.org/simple\n"
        "-e unpinned-editable\n"
        "direct @ https://user:secret@example.test/direct.tar.gz\n"
        "trailing==1.0 \\\n",
        encoding="utf-8",
    )

    declarations = {item.identifier: item for item in collect_python_declarations(root, discover_consumers=False)}

    assert declarations["python:unpinned-editable"].current == "unbounded"
    direct = declarations["python:direct"]
    assert direct.source_kind == "manual"
    assert "secret" not in direct.current
    assert direct.current.endswith("@example.test/direct.tar.gz")
    dangling = declarations["python-invalid:requirements.txt:5"]
    assert dangling.source_kind == "inventory-error"
    assert set(declarations) == {
        "python:direct",
        "python:unpinned-editable",
        "python-invalid:requirements.txt:5",
    }


def test_go_inventory_reads_single_line_requires(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    go_mod = root / "scripts" / "backup-tui" / "go.mod"
    go_mod.parent.mkdir(parents=True)
    go_mod.write_text(
        "module example.test/tool\n\nrequire example.test/single v0.4.0\nrequire example.test/broken\n",
        encoding="utf-8",
    )

    declarations = collect_go_declarations(root, discover_consumers=False)

    assert [(item.identifier, item.current) for item in declarations] == [("go:example.test/single", "v0.4.0")]
    assert declarations[0].definition.endswith("go.mod:3")


def test_stack_inventory_requires_constants_file(tmp_path: Path) -> None:
    assert collect_stack_platform_declarations(_repository(tmp_path)) == []


def test_stack_inventory_skips_non_literal_values(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "openemr_ecs" / "constants.py").write_text(
        "class StackConstants:\n"
        "    OPENEMR_VERSION: str\n"
        "    EMR_SERVERLESS_RELEASE_LABEL = compute_label()\n"
        "    LAMBDA_PYTHON_RUNTIME = factory().PYTHON_3_14\n"
        "    CREDENTIAL_ROTATION_PYTHON_VERSION: str = '3.14'\n",
        encoding="utf-8",
    )

    declarations = collect_stack_platform_declarations(root, discover_consumers=False)

    assert [(item.identifier, item.current) for item in declarations] == [
        ("toolchain:credential-python", "3.14"),
    ]


def test_stack_inventory_marks_missing_openemr_digest(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "openemr_ecs" / "constants.py").write_text("OPENEMR_VERSION = '8.1.1'\n", encoding="utf-8")

    declaration = collect_stack_platform_declarations(root, discover_consumers=False)[0]

    assert declaration.metadata["arm64_digest"] == ""


def test_workflow_inventories_skip_non_file_yaml_entries(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / ".github" / "workflows" / "directory.yml").mkdir()
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "steps:\n  - uses: actions/setup-node@v5\n  - run: npx aws-cdk@2.1100.0 synth\n",
        encoding="utf-8",
    )

    actions = collect_action_declarations(root)
    toolchains = {item.identifier: item for item in collect_workflow_toolchains(root)}

    assert [item.identifier for item in actions] == ["github-action:actions/setup-node"]
    assert toolchains["toolchain:cdk-cli"].current == "2.1100.0"
    assert toolchains["toolchain:cdk-cli"].source_kind == "npm"
    assert toolchains["toolchain:cdk-cli"].metadata["package"] == "aws-cdk"


def test_workflow_toolchains_skip_symlinked_dockerfiles(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    real = root / "real.Dockerfile.txt"
    real.write_text("ARG PYTHON_VERSION=3.99\n", encoding="utf-8")
    linked_directory = root / "linked"
    linked_directory.mkdir()
    (linked_directory / "Dockerfile").symlink_to(real)

    identifiers = {item.identifier for item in collect_workflow_toolchains(root)}

    assert "toolchain:python" not in identifiers


def test_node_inventory_tolerates_malformed_lock_and_sections(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "package.json").write_text(
        json.dumps({"dependencies": ["not", "a", "mapping"], "devDependencies": {"aws-cdk": "^2.1.0"}}),
        encoding="utf-8",
    )
    (root / "package-lock.json").write_text(json.dumps({"packages": []}), encoding="utf-8")

    declarations = collect_node_declarations(root, discover_consumers=False)

    assert [(item.identifier, item.current, item.constraint) for item in declarations] == [
        ("node:aws-cdk", "2.1.0", "^2.1.0"),
    ]


def test_collect_declarations_requires_matching_root(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    with pytest.raises(ValueError, match="does not match"):
        collect_declarations(root, source=InventorySource(tmp_path))


def test_restricted_collection_cannot_discover_consumers(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    with pytest.raises(ValueError, match="unrestricted consumer discovery"):
        collect_declarations(root, source=_restricted(root, {}))


def test_restricted_collection_reads_only_the_supplied_snapshot(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    files = {
        "requirements.txt": "# pinned runtime\n\ndemo==1.0\n",
        ".github/workflows/lint.yml": "run: go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@v2.5.0\n",
        "tools/worker/Dockerfile": "ARG PYTHON_VERSION=3.14\n",
    }

    declarations = {
        item.identifier: item
        for item in collect_declarations(root, discover_consumers=False, source=_restricted(root, files))
    }

    assert declarations["python:demo"].definition == "requirements.txt:3"
    assert declarations["toolchain:golangci-lint"].current == "v2.5.0"
    assert declarations["toolchain:golangci-lint"].metadata["repository"] == "golangci/golangci-lint"
    assert declarations["toolchain:python"].definition == "tools/worker/Dockerfile:1"
    assert not any(identifier.startswith("precommit:") for identifier in declarations)
    assert not (root / "requirements.txt").exists()


def test_collect_declarations_deduplicates_identifiers(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / ".pre-commit-config.yaml").write_text(
        "repos:\n"
        "  - repo: https://github.com/psf/black\n    rev: 25.1.0\n"
        "  - repo: https://github.com/psf/black\n    rev: 24.0.0\n",
        encoding="utf-8",
    )

    assert len(collect_precommit_declarations(root)) == 2
    declarations = [item for item in collect_declarations(root) if item.category == "pre-commit"]

    assert [(item.identifier, item.current) for item in declarations] == [("precommit:psf/black", "25.1.0")]
