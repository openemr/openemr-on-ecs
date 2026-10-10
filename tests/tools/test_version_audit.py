"""Focused tests for the reusable dependency and platform version audit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests

from tools._shared import ToolError, resolve_repo_path
from tools.version_audit import audit
from tools.version_audit.__main__ import EXIT_OK, EXIT_UPDATES, main
from tools.version_audit.inventory import (
    collect_action_declarations,
    collect_go_declarations,
    collect_node_declarations,
    collect_precommit_declarations,
    collect_python_declarations,
    collect_stack_platform_declarations,
    collect_workflow_toolchains,
)
from tools.version_audit.models import AuditReport, Declaration, Finding, Status
from tools.version_audit.render import render_markdown
from tools.version_audit.sources import HttpClient, Resolution, SourceError, VersionSources


def _repository(tmp_path: Path) -> Path:
    (tmp_path / "openemr_ecs").mkdir()
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "scripts" / "backup-tui").mkdir(parents=True)
    (tmp_path / "tools" / "credential-rotation").mkdir(parents=True)
    (tmp_path / "tools" / "openemr-import-worker").mkdir(parents=True)
    (tmp_path / "cdk.json").write_text('{"context": {}}\n', encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("", encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text("", encoding="utf-8")
    (tmp_path / "tools" / "credential-rotation" / "requirements.txt").write_text("", encoding="utf-8")
    (tmp_path / "tools" / "openemr-import-worker" / "requirements.in").write_text(
        "",
        encoding="utf-8",
    )
    (tmp_path / "tools" / "openemr-import-worker" / "requirements.txt").write_text("", encoding="utf-8")
    return tmp_path


def _declaration(**overrides: Any) -> Declaration:
    values: dict[str, Any] = {
        "identifier": "python:demo",
        "name": "demo",
        "category": "python-production",
        "current": "1.0.0",
        "definition": "requirements.txt:1",
        "source_kind": "pypi",
        "constraint": "==1.0.0",
        "metadata": {"normalized_name": "demo"},
    }
    values.update(overrides)
    return Declaration(**values)


def test_python_inventory_parses_pep508_and_deduplicates(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "requirements.txt").write_text(
        "Demo_Pkg[feature]==1.2.3; python_version >= '3.12'\n-r extra.txt\n",
        encoding="utf-8",
    )
    (root / "extra.txt").write_text("requests>=2.0,<3\n", encoding="utf-8")
    (root / "requirements-dev.txt").write_text("demo-pkg==1.2.3\n", encoding="utf-8")
    (root / "tools" / "credential-rotation" / "requirements.txt").write_text(
        "boto3==1.2.3\n",
        encoding="utf-8",
    )
    (root / "tools" / "openemr-import-worker" / "requirements.in").write_text(
        "packaging==26.3\n",
        encoding="utf-8",
    )
    (root / "tools" / "openemr-import-worker" / "requirements.txt").write_text(
        "botocore==1.43.69 \\\n"
        "    --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \\\n"
        "    --hash=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n",
        encoding="utf-8",
    )

    declarations = {item.identifier: item for item in collect_python_declarations(root)}

    demo = declarations["python:demo-pkg"]
    assert demo.current == "1.2.3"
    assert demo.category == "python-shared"
    assert demo.metadata["extras"] == ["feature"]
    assert demo.metadata["markers"] == ['python_version >= "3.12"']
    assert declarations["python:requests"].current == "<3,>=2.0"
    assert declarations["python:boto3"].current == "1.2.3"
    assert declarations["python:packaging"].current == "26.3"
    assert "openemr-import-worker/requirements.in:1" in declarations["python:packaging"].definition
    assert declarations["python:botocore"].current == "1.43.69"
    assert "openemr-import-worker/requirements.txt:1" in declarations["python:botocore"].definition


def test_python_inventory_reports_malformed_requirement(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "requirements.txt").write_text("not a valid !!! requirement\n", encoding="utf-8")

    declarations = collect_python_declarations(root)

    assert declarations[0].source_kind == "inventory-error"
    assert declarations[0].metadata["error"] == "Invalid PEP 508 requirement"


def test_python_inventory_redacts_malformed_secret_assignment(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "requirements.txt").write_text(
        "AWS_SECRET_ACCESS_KEY=supersecret\n",
        encoding="utf-8",
    )

    declarations = collect_python_declarations(root)

    assert declarations[0].source_kind == "inventory-error"
    assert "supersecret" not in declarations[0].name
    assert "<redacted>" in declarations[0].name


def test_repository_read_policy_rejects_nested_credential_filename(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    credential_file = root / "config" / "credentials.txt"
    credential_file.parent.mkdir()
    credential_file.write_text("secret\n", encoding="utf-8")

    with pytest.raises(ToolError, match="excluded"):
        resolve_repo_path(root, "config/credentials.txt")


def test_python_requirement_includes_cannot_escape_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    root = _repository(repository)
    outside = tmp_path / "outside.txt"
    outside.write_text("private-package==9.9.9\n", encoding="utf-8")
    (root / "requirements.txt").write_text("-r ../outside.txt\n", encoding="utf-8")

    declarations = collect_python_declarations(root)

    assert len(declarations) == 1
    assert declarations[0].source_kind == "inventory-error"
    assert "private-package" not in declarations[0].name


def test_go_inventory_includes_only_direct_modules(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "scripts" / "backup-tui" / "go.mod").write_text(
        "module example.test/tool\n\ngo 1.26\n\nrequire (\n"
        "\texample.test/direct v1.2.3\n"
        "\texample.test/indirect v2.0.0 // indirect\n"
        ")\n",
        encoding="utf-8",
    )

    identifiers = {item.identifier for item in collect_go_declarations(root)}

    assert "toolchain:go" in identifiers
    assert "go:example.test/direct" in identifiers
    assert "go:example.test/indirect" not in identifiers


def test_toolchain_inventory_binds_workflows_manifests_and_dockerfiles(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    workflow = root / ".github" / "workflows" / "ci.yml"
    workflow.write_text(
        'env:\n  PYTHON_VERSION: "3.14"\n  NODE_VERSION: "24"\n'
        '  PIP_VERSION: "26.2"\n  SEMVER_VERSION: "3.0.4"\n'
        '  SHELLCHECK_VERSION: "0.11.0"\n',
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        '{"engines":{"node":">=24 <25"}}\n',
        encoding="utf-8",
    )
    dockerfile = root / "tools" / "credential-rotation" / "Dockerfile"
    dockerfile.write_text(
        "ARG PYTHON_VERSION=3.14\nFROM python:${PYTHON_VERSION}-alpine\n",
        encoding="utf-8",
    )

    declarations = {item.identifier: item for item in collect_workflow_toolchains(root)}

    assert declarations["toolchain:python"].current == "3.14"
    assert "Dockerfile:1" in declarations["toolchain:python"].definition
    assert declarations["toolchain:python"].metadata["conflicting_pins"] is False
    assert declarations["toolchain:node"].current == "24"
    assert "package.json:1" in declarations["toolchain:node"].definition
    assert declarations["toolchain:pip"].current == "26.2"
    assert declarations["toolchain:pip"].source_kind == "pypi"
    assert declarations["toolchain:semver"].current == "3.0.4"
    assert declarations["toolchain:shellcheck"].current == "0.11.0"
    assert declarations["toolchain:shellcheck"].metadata["repository"] == "koalaman/shellcheck"

    dockerfile.write_text(
        "ARG PYTHON_VERSION=3.13\nFROM python:${PYTHON_VERSION}-alpine\n",
        encoding="utf-8",
    )
    declarations = {item.identifier: item for item in collect_workflow_toolchains(root)}
    assert declarations["toolchain:python"].current == "3.13 / 3.14"
    assert declarations["toolchain:python"].metadata["conflicting_pins"] is True


def test_stack_inventory_uses_ast_not_regex(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "openemr_ecs" / "constants.py").write_text(
        "class StackConstants:\n"
        "    OPENEMR_VERSION = '8.1.1'\n"
        "    OPENEMR_ARM64_DIGEST = 'sha256:" + ("a" * 64) + "'\n"
        "    AURORA_MYSQL_ENGINE_VERSION = rds.AuroraMysqlEngineVersion.VER_3_12_0\n"
        "    LAMBDA_PYTHON_RUNTIME = runtime.PYTHON_3_14\n"
        "    EMR_SERVERLESS_RELEASE_LABEL = 'emr-7.13.0'\n",
        encoding="utf-8",
    )

    declarations = {item.identifier: item for item in collect_stack_platform_declarations(root)}

    assert declarations["container:openemr"].current == "8.1.1"
    assert declarations["container:openemr"].metadata["arm64_digest"] == ("sha256:" + ("a" * 64))
    assert declarations["platform:aurora-mysql"].current == "3.12.0"
    assert declarations["platform:lambda-python"].current == "3.14"


def test_compose_openemr_images_match_the_deployment_version() -> None:
    root = Path(__file__).resolve().parents[2]
    declarations = {item.identifier: item for item in collect_stack_platform_declarations(root)}
    expected = (
        f"openemr/openemr:{declarations['container:openemr'].current}"
        f"@{declarations['container:openemr'].metadata['arm64_digest']}"
    )

    for compose_file in (
        root / "compose" / "docker-compose.test.yml",
        root / "compose" / "docker-compose.test-ssl.yml",
    ):
        text = compose_file.read_text(encoding="utf-8")
        assert f"image: {expected}" in text
        assert "platform: linux/arm64" in text


def test_actions_inventory_deduplicates_consumers(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / ".github" / "workflows" / "one.yml").write_text(
        "steps:\n  - uses: actions/checkout@v6\n",
        encoding="utf-8",
    )
    (root / ".github" / "workflows" / "two.yml").write_text(
        "steps:\n  - uses: actions/checkout@v6\n",
        encoding="utf-8",
    )

    declarations = collect_action_declarations(root)

    assert len(declarations) == 1
    assert declarations[0].current == "v6"
    assert len(declarations[0].consumers) == 1
    assert declarations[0].metadata["immutable_sha_pins"] is False


def test_actions_inventory_reads_version_comment_for_sha_pin(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    sha = "a" * 40
    (root / ".github" / "workflows" / "one.yml").write_text(
        f"steps:\n  - uses: actions/checkout@{sha} # v7.0.1\n",
        encoding="utf-8",
    )

    declaration = collect_action_declarations(root)[0]

    assert declaration.current == "v7.0.1"
    assert declaration.metadata["immutable_sha_pins"] is True
    assert declaration.metadata["revision_labels"] == {sha: "v7.0.1"}


def test_precommit_inventory_pairs_repo_and_revision(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    sha = "c" * 40
    (root / ".pre-commit-config.yaml").write_text(
        f"repos:\n  - repo: https://github.com/psf/black\n    rev: {sha} # 25.1.0\n    hooks:\n      - id: black\n",
        encoding="utf-8",
    )

    declarations = collect_precommit_declarations(root)

    assert declarations[0].name == "psf/black"
    assert declarations[0].current == "25.1.0"
    assert declarations[0].metadata["revision_labels"] == {sha: "25.1.0"}
    assert declarations[0].metadata["immutable_sha_pins"] is True


def test_node_inventory_uses_lockfile_resolution_and_manifest_constraint(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    (root / "package.json").write_text(
        '{"private":true,"devDependencies":{"aws-cdk":"^2.1134.0"}}\n',
        encoding="utf-8",
    )
    (root / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "": {"devDependencies": {"aws-cdk": "^2.1134.0"}},
                    "node_modules/aws-cdk": {"version": "2.1134.1"},
                }
            }
        ),
        encoding="utf-8",
    )

    declaration = collect_node_declarations(root)[0]

    assert declaration.identifier == "node:aws-cdk"
    assert declaration.current == "2.1134.1"
    assert declaration.constraint == "^2.1134.0"
    assert any("package-lock.json" in consumer for consumer in declaration.consumers)


def test_http_client_converts_timeout_to_source_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = HttpClient(timeout_seconds=0.1)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise requests.Timeout("slow")

    monkeypatch.setattr(client.session, "get", fail)

    with pytest.raises(SourceError, match="slow"):
        client.get_bytes("https://pypi.org/pypi/example/json")


def test_http_client_rejects_unapproved_hosts_before_network_access(monkeypatch: pytest.MonkeyPatch) -> None:
    client = HttpClient()
    monkeypatch.setattr(
        client.session,
        "get",
        lambda *args, **kwargs: pytest.fail("unapproved URL must not be fetched"),
    )

    with pytest.raises(SourceError, match="authoritative host policy"):
        client.get_bytes("https://example.test/redirected-source")


def test_http_client_sends_token_only_to_github_api(monkeypatch: pytest.MonkeyPatch) -> None:
    client = HttpClient(github_token="test-token")
    calls: list[tuple[str, dict[str, str] | None]] = []

    class Response:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int) -> list[bytes]:
            return [b"{}"]

        def close(self) -> None:
            return None

    def get(url: str, **kwargs: Any) -> Response:
        calls.append((url, kwargs["headers"]))
        return Response()

    monkeypatch.setattr(client.session, "get", get)

    client.get_bytes("https://api.github.com/repos/example/project/releases")
    client.get_bytes("https://pypi.org/pypi/example/json")

    assert calls[0][1] == {
        "Authorization": "Bearer test-token",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    assert calls[1][1] is None


class _FakeClient:
    def __init__(self, payload: Any):
        self.payload = payload

    def get_json(self, url: str) -> Any:
        return self.payload

    def get_text(self, url: str) -> str:
        return str(self.payload)


def test_pypi_source_excludes_prereleases_and_yanked_releases() -> None:
    source = VersionSources(
        _FakeClient(  # type: ignore[arg-type]
            {
                "releases": {
                    "1.0.0": [{"yanked": False}],
                    "1.1.0": [{"yanked": True}],
                    "1.2.0rc1": [{"yanked": False}],
                    "9.9.9": [],
                }
            }
        )  # type: ignore[arg-type]
    )

    resolution = source.resolve(_declaration())

    assert resolution.latest == "1.0.0"
    assert resolution.latest_prerelease == "1.2.0rc1"


def test_malformed_source_response_is_isolated_as_source_error() -> None:
    source = VersionSources(_FakeClient(["not", "a", "mapping"]))  # type: ignore[arg-type]

    with pytest.raises(SourceError, match="Malformed pypi response"):
        source.resolve(_declaration())


def test_npm_latest_must_be_a_stable_semantic_version() -> None:
    source = VersionSources(_FakeClient({"version": "3.0.0-rc.1"}))  # type: ignore[arg-type]

    with pytest.raises(SourceError, match="prerelease"):
        source.resolve(_declaration(source_kind="npm", name="example"))


def test_python_toolchain_uses_published_stable_python_org_releases() -> None:
    source = VersionSources(
        _FakeClient(  # type: ignore[arg-type]
            [
                {
                    "name": "Python 3.14.7",
                    "is_published": True,
                    "pre_release": False,
                },
                {
                    "name": "Python 3.15.0rc1",
                    "is_published": True,
                    "pre_release": True,
                },
                {
                    "name": "Python 3.99.0",
                    "is_published": False,
                    "pre_release": False,
                },
            ]
        )
    )

    resolution = source.resolve(_declaration(source_kind="python-toolchain", current="3.14"))

    assert resolution.latest == "3.14.7"
    assert resolution.source_url.startswith("https://www.python.org/")


def test_deferrals_require_reason_status_and_future_review_date(tmp_path: Path) -> None:
    path = tmp_path / "tools" / "version_audit"
    path.mkdir(parents=True)
    (path / "deferrals.json").write_text(
        json.dumps(
            {
                "python:demo": {
                    "status": "deferred",
                    "reason": "Waiting for upstream compatibility",
                    "review_date": "2999-01-01",
                },
                "python:expired": {
                    "status": "deferred",
                    "reason": "Past review date",
                    "review_date": "2000-01-01",
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = audit._load_deferrals(tmp_path)

    assert set(loaded) == {"python:demo"}
    assert loaded["python:demo"]["review_date"] == "2999-01-01"


def test_malformed_deferral_fails_the_audit_closed(tmp_path: Path) -> None:
    path = tmp_path / "tools" / "version_audit"
    path.mkdir(parents=True)
    (path / "deferrals.json").write_text(
        '{"python:demo":{"status":"deferred","reason":""}}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="reason"):
        audit._load_deferrals(tmp_path)


def test_emr_source_accepts_documentation_display_labels() -> None:
    source = VersionSources(
        _FakeClient("<h2>EMR Serverless 7.13.0</h2><a>EMR Serverless 7.12.0</a>")  # type: ignore[arg-type]
    )

    resolution = source.resolve(_declaration(source_kind="emr-serverless", current="emr-7.12.0"))

    assert resolution.latest == "emr-7.13.0"


def test_lambda_source_excludes_unreleased_python_runtime() -> None:
    class LambdaClient:
        def get_text(self, url: str) -> str:
            return "Python 3.14 Python 3.15"

        def get_json(self, url: str) -> Any:
            return [
                {
                    "name": "Python 3.14.6",
                    "is_published": True,
                    "pre_release": False,
                }
            ]

    source = VersionSources(LambdaClient())  # type: ignore[arg-type]

    resolution = source.resolve(_declaration(source_kind="lambda-runtime", current="3.14"))

    assert resolution.latest == "3.14"


def test_aurora_source_stays_on_declared_engine_line() -> None:
    source = VersionSources(_FakeClient({}))  # type: ignore[arg-type]

    resolution = source.resolve(_declaration(source_kind="aws-cdk-aurora", current="3.12.0"))

    assert resolution.latest is not None
    assert resolution.latest.startswith("3.")


@pytest.mark.parametrize(
    ("pinned_digest", "reference_verified"),
    [
        ("sha256:" + ("1" * 64), True),
        ("sha256:" + ("f" * 64), False),
        ("sha256:" + ("A" * 64), False),
        ("", False),
    ],
)
def test_openemr_source_requires_official_release_and_arm64_digest(
    pinned_digest: str,
    reference_verified: bool,
) -> None:
    class OpenEmrClient:
        def get_json(self, url: str) -> object:
            if "hub.docker.com" in url:
                return {
                    "results": [
                        {
                            "name": "8.1.1",
                            "images": [
                                {
                                    "architecture": "arm64",
                                    "os": "linux",
                                    "digest": "sha256:" + ("1" * 64),
                                }
                            ],
                        },
                        {
                            "name": "8.1.2",
                            "images": [
                                {
                                    "architecture": "arm64",
                                    "os": "linux",
                                    "digest": "sha256:" + ("2" * 64),
                                }
                            ],
                        },
                        {
                            "name": "8.1.3",
                            "images": [
                                {
                                    "architecture": "arm64",
                                    "os": "linux",
                                    "digest": "sha256:" + ("3" * 64),
                                }
                            ],
                        },
                        {
                            "name": "8.1.4",
                            "images": [
                                {
                                    "architecture": "amd64",
                                    "os": "linux",
                                    "digest": "sha256:" + ("4" * 64),
                                }
                            ],
                        },
                    ],
                    "next": None,
                }
            if "api.github.com/repos/openemr/openemr/releases" in url:
                return [
                    {"tag_name": "v8_1_1", "draft": False, "prerelease": False},
                    {"tag_name": "v8_1_2", "draft": False, "prerelease": False},
                    {"tag_name": "v8_1_3", "draft": True, "prerelease": False},
                ]
            raise AssertionError(f"Unexpected URL: {url}")

        def get_text(self, url: str) -> str:
            raise AssertionError(f"Unexpected URL: {url}")

    resolution = VersionSources(OpenEmrClient()).resolve(  # type: ignore[arg-type]
        _declaration(
            identifier="container:openemr",
            name="openemr/openemr",
            category="container",
            current="8.1.1",
            source_kind="openemr-container",
            metadata={"arm64_digest": pinned_digest},
        )
    )

    assert resolution.latest == "8.1.2"
    assert resolution.latest_prerelease is None
    assert resolution.current_reference_verified is reference_verified


@pytest.mark.parametrize(
    ("current", "latest", "source_kind", "expected"),
    [
        ("v6", "v6.2.0", "go-toolchain", Status.CURRENT),
        ("24", "v24.10.0", "node-toolchain", Status.CURRENT),
        ("3.14", "3.14.6", "python-toolchain", Status.CURRENT),
        ("1.25", "1.25.4", "github-release", Status.STABLE_UPDATE),
        ("1.0.0", "1.1.0", "github-release", Status.STABLE_UPDATE),
        ("2.0.0", "1.9.0", "github-release", Status.MANUAL_REVIEW),
    ],
)
def test_classification_handles_aliases_and_semver(
    current: str,
    latest: str,
    source_kind: str,
    expected: Status,
) -> None:
    declaration = _declaration(current=current, constraint=None, source_kind=source_kind)

    status, _ = audit._classify(  # pylint: disable=protected-access
        declaration,
        Resolution(latest=latest, source_url="https://example.test"),
    )

    assert status is expected


def test_exact_two_part_python_pin_does_not_track_future_patch_releases() -> None:
    declaration = _declaration(
        current="26.2",
        constraint="==26.2",
        source_kind="pypi",
    )

    status, _ = audit._classify(  # pylint: disable=protected-access
        declaration,
        Resolution(latest="26.2.1", source_url="https://example.test"),
    )

    assert status is Status.STABLE_UPDATE


def test_python_range_accepting_latest_is_current() -> None:
    declaration = _declaration(current=">=1,<3", constraint="<3,>=1")

    status, note = audit._classify(  # pylint: disable=protected-access
        declaration,
        Resolution(latest="2.4.0", source_url="https://example.test"),
    )

    assert status is Status.CURRENT
    assert "already accepts" in str(note)


def test_aurora_update_always_requires_compatibility_review() -> None:
    declaration = _declaration(source_kind="aws-cdk-aurora", current="3.10.0")

    status, _ = audit._classify(  # pylint: disable=protected-access
        declaration,
        Resolution(latest="3.12.0", source_url="https://example.test"),
    )

    assert status is Status.MANUAL_REVIEW


def test_current_action_tag_requires_immutable_sha_pin() -> None:
    declaration = _declaration(
        category="github-actions",
        source_kind="github-release",
        current="v7",
        metadata={"immutable_sha_pins": False},
    )

    status, note = audit._classify(  # pylint: disable=protected-access
        declaration,
        Resolution(latest="v7.0.1", source_url="https://example.test"),
    )

    assert status is Status.MANUAL_REVIEW
    assert "immutable commit SHA" in str(note)


def test_action_sha_must_match_its_labelled_github_tag() -> None:
    pinned_sha = "a" * 40
    tagged_sha = "b" * 40

    class GitHubClient:
        def get_json(self, url: str) -> object:
            if "/releases" in url:
                return [
                    {
                        "tag_name": "v1.0.0",
                        "draft": False,
                        "prerelease": False,
                    }
                ]
            if "/commits/v1.0.0" in url:
                return {"sha": tagged_sha}
            raise AssertionError(f"Unexpected URL: {url}")

    declaration = _declaration(
        name="actions/example",
        category="github-actions",
        current="v1.0.0",
        source_kind="github-release",
        metadata={
            "repository": "actions/example",
            "immutable_sha_pins": True,
            "revision_labels": {pinned_sha: "v1.0.0"},
        },
    )

    resolution = VersionSources(GitHubClient()).resolve(declaration)  # type: ignore[arg-type]
    status, note = audit._classify(declaration, resolution)

    assert resolution.current_reference_verified is False
    assert status is Status.MANUAL_REVIEW
    assert "does not match" in str(note)


def test_run_audit_isolates_source_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declarations = (
        _declaration(identifier="python:one", name="one"),
        _declaration(identifier="python:two", name="two"),
    )
    monkeypatch.setattr(audit, "collect_declarations", lambda root: declarations)
    monkeypatch.setattr(audit, "_load_deferrals", lambda root: {})

    class FakeSources:
        def __init__(self, client: object):
            pass

        def resolve(self, declaration: Declaration) -> Resolution:
            if declaration.name == "one":
                raise SourceError("source unavailable")
            return Resolution(latest="1.1.0", source_url="https://example.test")

    monkeypatch.setattr(audit, "VersionSources", FakeSources)

    report = audit.run_audit(tmp_path, generated_at="2026-01-01T00:00:00Z")

    assert report.partial_failure is True
    assert [item.status for item in report.findings] == [
        Status.UNABLE,
        Status.STABLE_UPDATE,
    ]


def test_offline_audit_never_constructs_http_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "collect_declarations", lambda root: (_declaration(),))
    monkeypatch.setattr(audit, "_load_deferrals", lambda root: {})
    monkeypatch.setattr(
        audit,
        "HttpClient",
        lambda *args, **kwargs: pytest.fail("HTTP client should not be created"),
    )

    report = audit.run_audit(tmp_path, online=False)

    assert report.findings[0].status is Status.UNABLE
    assert report.findings[0].note == "Network lookup disabled"


def test_markdown_escapes_table_values() -> None:
    finding = Finding(
        identifier="demo",
        name="demo|name",
        category="test",
        current="1.0",
        latest="2.0",
        status=Status.STABLE_UPDATE,
        definition="file:1",
        source_kind="test",
        source_url="https://user:password@example.test/releases?token=must-not-leak",
    )
    report = AuditReport(
        generated_at="2026-01-01T00:00:00Z",
        repository_root=".",
        findings=(finding,),
        selected_categories=("test",),
    )

    markdown = render_markdown(report)

    assert "demo\\|name" in markdown
    assert "password" not in markdown
    assert "must-not-leak" not in markdown


def test_cli_writes_reports_and_fail_on_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finding = Finding(
        identifier="demo",
        name="demo",
        category="test",
        current="1.0",
        latest="2.0",
        status=Status.STABLE_UPDATE,
        definition="file:1",
        source_kind="test",
        source_url="https://example.test",
    )
    report = AuditReport(
        generated_at="2026-01-01T00:00:00Z",
        repository_root=".",
        findings=(finding,),
        selected_categories=("test",),
    )
    monkeypatch.setattr("tools.version_audit.__main__.repository_root", lambda: tmp_path)
    monkeypatch.setattr(
        "tools.version_audit.__main__.collect_declarations",
        lambda root: (_declaration(category="test"),),
    )
    monkeypatch.setattr("tools.version_audit.__main__.run_audit", lambda *args, **kwargs: report)

    exit_code = main(
        [
            "--category",
            "test",
            "--json",
            "report.json",
            "--markdown",
            "report.md",
            "--quiet",
            "--fail-on-updates",
        ]
    )

    assert exit_code == EXIT_UPDATES
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["summary"]["updates_found"] is True
    assert (tmp_path / "report.md").read_text(encoding="utf-8").startswith("# Dependency")


def test_cli_offline_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tools.version_audit.__main__.repository_root", lambda: tmp_path)
    monkeypatch.setattr(
        "tools.version_audit.__main__.collect_declarations",
        lambda root: (_declaration(category="test"),),
    )
    monkeypatch.setattr(
        "tools.version_audit.__main__.run_audit",
        lambda *args, **kwargs: AuditReport(
            generated_at="2026-01-01T00:00:00Z",
            repository_root=".",
            findings=(),
            selected_categories=("test",),
        ),
    )

    assert main(["--offline", "--quiet"]) == EXIT_OK


def test_monthly_workflow_invokes_reusable_audit() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github" / "workflows" / "monthly-version-check.yml").read_text(encoding="utf-8")

    assert "python -m tools.version_audit" in workflow
    assert "pip list --outdated" not in workflow
    assert "issues.create" in workflow
