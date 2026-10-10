"""Edge-case tests for the bounded version-audit external sources."""

from __future__ import annotations

import builtins
from typing import Any

import pytest
import requests

from tools.version_audit import sources as sources_module
from tools.version_audit.models import Declaration
from tools.version_audit.sources import (
    HttpClient,
    Resolution,
    SourceError,
    VersionSources,
    _go_proxy_escape,
    _highest,
)

SHA_A = "a" * 40
SHA_B = "b" * 40


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


class _Response:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
        error: Exception | None = None,
    ):
        self.status_code = status_code
        self.headers = headers or {}
        self.chunks = chunks if chunks is not None else [b"{}"]
        self.error = error
        self.closed = False

    def raise_for_status(self) -> None:
        if self.error is not None:
            raise self.error

    def iter_content(self, chunk_size: int) -> list[bytes]:
        return self.chunks

    def close(self) -> None:
        self.closed = True


def _client_with(monkeypatch: pytest.MonkeyPatch, responses: list[_Response], **kwargs: Any) -> HttpClient:
    client = HttpClient(**kwargs)
    queue = list(responses)
    requested: list[str] = []

    def get(url: str, **_: Any) -> _Response:
        requested.append(url)
        return queue.pop(0)

    monkeypatch.setattr(client.session, "get", get)
    client.requested = requested  # type: ignore[attr-defined]
    return client


class _MappingClient:
    """Return canned payloads by URL substring and record every request."""

    def __init__(self, routes: dict[str, Any]):
        self.routes = routes
        self.requested: list[str] = []

    def _lookup(self, url: str) -> Any:
        self.requested.append(url)
        for fragment, payload in self.routes.items():
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"Unexpected URL: {url}")

    def get_json(self, url: str) -> Any:
        return self._lookup(url)

    def get_text(self, url: str) -> str:
        return self._lookup(url)


def _sources(routes: dict[str, Any]) -> tuple[VersionSources, _MappingClient]:
    client = _MappingClient(routes)
    return VersionSources(client), client  # type: ignore[arg-type]


def test_highest_ignores_invalid_and_wrong_channel_versions() -> None:
    values = ["not-a-version", "1.0.0", "2.0.0rc1", "v1.5.0"]

    assert _highest(values) == "v1.5.0"
    assert _highest(values, prerelease=True) == "2.0.0rc1"
    assert _highest(["junk"]) is None


def test_go_proxy_escape_encodes_uppercase_letters() -> None:
    assert _go_proxy_escape("github.com/Azure/SDK") == "github.com/!azure/!s!d!k"


def test_http_client_requires_positive_timeout() -> None:
    with pytest.raises(ValueError, match="positive"):
        HttpClient(timeout_seconds=0)


def test_http_client_caches_successful_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _Response(chunks=[b'{"a":', b" 1}"])
    client = _client_with(monkeypatch, [response])

    assert client.get_json("https://pypi.org/pypi/demo/json") == {"a": 1}
    assert client.get_json("https://pypi.org/pypi/demo/json") == {"a": 1}
    assert client.requested == ["https://pypi.org/pypi/demo/json"]  # type: ignore[attr-defined]
    assert response.closed is True


def test_http_client_follows_allowlisted_relative_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    redirect = _Response(status_code=302, headers={"Location": "/pypi/other/json"})
    final = _Response(chunks=[b"ok"])
    client = _client_with(monkeypatch, [redirect, final])

    assert client.get_text("https://pypi.org/pypi/demo/json") == "ok"
    assert client.requested == [  # type: ignore[attr-defined]
        "https://pypi.org/pypi/demo/json",
        "https://pypi.org/pypi/other/json",
    ]
    assert redirect.closed is True


def test_http_client_rejects_redirect_without_location(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with(monkeypatch, [_Response(status_code=301)])

    with pytest.raises(SourceError, match="omitted its destination"):
        client.get_bytes("https://pypi.org/pypi/demo/json")


def test_http_client_rejects_redirect_off_the_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with(
        monkeypatch,
        [_Response(status_code=307, headers={"Location": "https://evil.example/steal"})],
    )

    with pytest.raises(SourceError, match="authoritative host policy"):
        client.get_bytes("https://pypi.org/pypi/demo/json")


def test_http_client_enforces_redirect_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    redirects = [_Response(status_code=308, headers={"Location": f"/hop/{index}"}) for index in range(6)]
    client = _client_with(monkeypatch, redirects)

    with pytest.raises(SourceError, match="redirect limit"):
        client.get_bytes("https://pypi.org/start")
    assert len(client.requested) == 6  # type: ignore[attr-defined]


def test_http_client_converts_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _Response(status_code=404, error=requests.HTTPError("404 Not Found"))
    client = _client_with(monkeypatch, [response])

    with pytest.raises(SourceError, match="404"):
        client.get_bytes("https://pypi.org/pypi/missing/json")
    assert response.closed is True


def test_http_client_rejects_declared_oversized_response(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with(
        monkeypatch,
        [_Response(headers={"Content-Length": "11"})],
        max_bytes=10,
    )

    with pytest.raises(SourceError, match="exceeds 10 bytes"):
        client.get_bytes("https://pypi.org/pypi/demo/json")


def test_http_client_rejects_streamed_oversized_response(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with(
        monkeypatch,
        [_Response(chunks=[b"12345", b"678901"])],
        max_bytes=10,
    )

    with pytest.raises(SourceError, match="exceeds 10 bytes"):
        client.get_bytes("https://pypi.org/pypi/demo/json")


def test_http_client_reports_invalid_json_and_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with(monkeypatch, [_Response(chunks=[b"not json"]), _Response(chunks=[b"\xff\xfe"])])

    with pytest.raises(SourceError, match="Invalid JSON"):
        client.get_json("https://pypi.org/one")
    with pytest.raises(SourceError, match="Invalid UTF-8"):
        client.get_text("https://pypi.org/two")


@pytest.mark.parametrize(
    ("source_kind", "message"),
    [
        ("manual", "requires manual review"),
        ("something-new", "No resolver is configured"),
    ],
)
def test_resolve_rejects_unresolvable_kinds(source_kind: str, message: str) -> None:
    source, client = _sources({})

    with pytest.raises(SourceError, match=message):
        source.resolve(_declaration(source_kind=source_kind))
    assert client.requested == []


def test_resolve_surfaces_inventory_errors() -> None:
    source, _ = _sources({})

    with pytest.raises(SourceError, match="Invalid PEP 508"):
        source.resolve(
            _declaration(source_kind="inventory-error", metadata={"error": "Invalid PEP 508 requirement"}),
        )
    with pytest.raises(SourceError, match="Inventory parsing failed"):
        source.resolve(_declaration(source_kind="inventory-error", metadata={}))


def test_pypi_skips_invalid_and_fully_yanked_versions() -> None:
    source, client = _sources(
        {
            "pypi.org": {
                "releases": {
                    "not!valid": [{"yanked": False}],
                    "2.0.0": [{"yanked": True}, "bogus"],
                    "1.5.0": [{"yanked": False}],
                }
            }
        }
    )

    resolution = source.resolve(_declaration(metadata={"normalized_name": "Demo Pkg"}))

    assert resolution.latest == "1.5.0"
    assert resolution.latest_prerelease is None
    assert client.requested == ["https://pypi.org/pypi/Demo%20Pkg/json"]


def test_pypi_requires_a_stable_release() -> None:
    source, _ = _sources({"pypi.org": {"releases": {"1.0.0rc1": [{"yanked": False}]}}})

    with pytest.raises(SourceError, match="No non-yanked stable release"):
        source.resolve(_declaration())


def test_go_proxy_resolves_escaped_module_versions() -> None:
    source, client = _sources({"proxy.golang.org": "v1.2.0\n\nv1.10.0\nv1.11.0-rc.1\n"})

    resolution = source.resolve(
        _declaration(source_kind="go-proxy", name="github.com/Foo/bar", metadata={"module": "github.com/Foo/bar"}),
    )

    assert resolution.latest == "v1.10.0"
    assert resolution.latest_prerelease == "v1.11.0-rc.1"
    assert client.requested == ["https://proxy.golang.org/github.com/!foo/bar/@v/list"]


def test_go_proxy_requires_stable_versions() -> None:
    source, _ = _sources({"proxy.golang.org": "v2.0.0-beta.1\n"})

    with pytest.raises(SourceError, match="No stable semantic versions"):
        source.resolve(_declaration(source_kind="go-proxy", metadata={}))


def test_github_release_filters_drafts_tags_and_prereleases_and_caches() -> None:
    source, client = _sources(
        {
            "/releases": [
                {"tag_name": "v9.0.0", "draft": True},
                {"tag_name": ""},
                {"tag_name": "nightly"},
                {"tag_name": "v3.0.0", "prerelease": True},
                {"tag_name": "v2.1.0-rc.1"},
                {"tag_name": "v2.0.0"},
            ]
        }
    )
    declaration = _declaration(
        source_kind="github-release",
        category="toolchains",
        metadata={"repository": "https://github.com/owner/project.git"},
    )

    first = source.resolve(declaration)
    second = source.resolve(declaration)

    assert first.latest == "v2.0.0"
    # A GitHub-flagged prerelease with a stable-looking tag is never promoted to latest stable.
    assert first.latest_prerelease == "v2.1.0-rc.1"
    assert second == first
    assert client.requested == ["https://api.github.com/repos/owner/project/releases?per_page=100"]


def test_github_release_falls_back_to_tags_when_releases_fail() -> None:
    source, client = _sources(
        {
            "/releases": SourceError("rate limited"),
            "/tags": [{"name": "v1.4.0"}, {"name": ""}, {"name": "v1.3.0"}],
        }
    )

    resolution = source.resolve(_declaration(source_kind="github-release", name="owner/project", metadata={}))

    assert resolution.latest == "v1.4.0"
    assert resolution.source_url == "https://api.github.com/repos/owner/project/tags?per_page=100"
    assert len(client.requested) == 2


def test_github_release_requires_a_stable_tag() -> None:
    source, _ = _sources({"/releases": [], "/tags": [{"name": "latest"}]})

    with pytest.raises(SourceError, match="No stable semantic release"):
        source.resolve(_declaration(source_kind="github-release", name="owner/project", metadata={}))


def test_github_release_rejects_invalid_slug_and_non_list_response() -> None:
    source, client = _sources({"/releases": {"message": "Not Found"}})

    with pytest.raises(SourceError, match="Invalid GitHub repository slug"):
        source.resolve(_declaration(source_kind="github-release", name="no-slash", metadata={}))
    assert client.requested == []
    with pytest.raises(SourceError, match="Unexpected GitHub response"):
        source.resolve(_declaration(source_kind="github-release", name="owner/project", metadata={}))


def _pinned_action(labels: Any) -> Declaration:
    return _declaration(
        name="actions/example",
        category="github-actions",
        current="v1.0.0",
        source_kind="github-release",
        metadata={"repository": "actions/example", "immutable_sha_pins": True, "revision_labels": labels},
    )


def test_github_action_pin_verified_when_sha_matches_label() -> None:
    source, client = _sources(
        {
            "/releases": [{"tag_name": "v1.0.0"}],
            "/commits/v1.0.0": {"sha": SHA_A.upper()},
        }
    )

    resolution = source.resolve(_pinned_action({SHA_A: "v1.0.0"}))

    assert resolution.current_reference_verified is True
    assert resolution.source_url.endswith("/releases?per_page=100")
    assert client.requested[-1] == "https://api.github.com/repos/actions/example/commits/v1.0.0"


@pytest.mark.parametrize(
    ("labels", "message"),
    [
        ({}, "lack version labels"),
        (["not", "a", "mapping"], "lack version labels"),
        ({"short": "v1.0.0"}, "Invalid immutable GitHub pin metadata"),
        ({SHA_A: ""}, "Invalid immutable GitHub pin metadata"),
    ],
)
def test_github_action_pin_metadata_must_be_well_formed(labels: Any, message: str) -> None:
    source, _ = _sources({"/releases": [{"tag_name": "v1.0.0"}]})

    with pytest.raises(SourceError, match=message):
        source.resolve(_pinned_action(labels))


@pytest.mark.parametrize("commit", [{"sha": "not-a-sha"}, ["unexpected"]])
def test_github_action_pin_requires_github_to_resolve_label(commit: Any) -> None:
    source, _ = _sources({"/releases": [{"tag_name": "v1.0.0"}], "/commits/": commit})

    with pytest.raises(SourceError, match="did not resolve actions/example@v1.0.0"):
        source.resolve(_pinned_action({SHA_A: "v1.0.0"}))


def test_npm_resolves_stable_latest_for_scoped_packages() -> None:
    source, client = _sources({"registry.npmjs.org": {"version": "2.1.0"}})

    resolution = source.resolve(
        _declaration(source_kind="npm", name="@aws-cdk/x", metadata={"package": "@aws-cdk/x"}),
    )

    assert resolution == Resolution(latest="2.1.0", source_url="https://registry.npmjs.org/@aws-cdk%2Fx/latest")
    assert client.requested == ["https://registry.npmjs.org/@aws-cdk%2Fx/latest"]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"version": ""}, "did not return a latest version"),
        ({}, "did not return a latest version"),
        ({"version": "not.a.version!"}, "invalid latest version"),
    ],
)
def test_npm_rejects_missing_or_invalid_latest(payload: dict[str, str], message: str) -> None:
    source, _ = _sources({"registry.npmjs.org": payload})

    with pytest.raises(SourceError, match=message):
        source.resolve(_declaration(source_kind="npm", metadata={}))


def _image(digest: str, architecture: str = "arm64") -> dict[str, str]:
    return {"architecture": architecture, "os": "linux", "digest": digest}


def test_openemr_container_paginates_and_matches_prereleases() -> None:
    digest = "sha256:" + "1" * 64
    first_page = {
        "results": [
            {"name": "latest", "images": [_image(digest)]},
            {"name": "8.1.1", "images": [_image(digest)]},
            {"name": "8.2.0rc1", "images": [_image("sha256:" + "2" * 64)]},
        ],
        "next": "https://hub.docker.com/v2/repositories/openemr/openemr/tags?page=2",
    }
    second_page = {
        "results": [{"name": "8.1.2", "images": [_image("sha256:" + "3" * 64)]}],
        "next": None,
    }

    class Client:
        def __init__(self) -> None:
            self.requested: list[str] = []

        def get_json(self, url: str) -> Any:
            self.requested.append(url)
            if "page=2" in url:
                return second_page
            if "hub.docker.com" in url:
                return first_page
            return [
                {"tag_name": "v8_1_1"},
                {"tag_name": "v8_1_2"},
                {"tag_name": "v8_2_0_rc1", "prerelease": True},
                {"tag_name": "not-a-release"},
                {"tag_name": "v9_0_0", "draft": True},
            ]

    client = Client()
    resolution = VersionSources(client).resolve(  # type: ignore[arg-type]
        _declaration(
            name="openemr/openemr",
            category="containers",
            current="8.1.1",
            source_kind="openemr-container",
            metadata={"arm64_digest": digest},
        )
    )

    assert resolution.latest == "8.1.2"
    assert resolution.latest_prerelease == "8.2.0rc1"
    assert resolution.current_reference_verified is True
    assert client.requested[1].endswith("page=2")


def test_openemr_container_requires_list_of_releases() -> None:
    source, _ = _sources({"hub.docker.com": {"results": [], "next": None}, "api.github.com": {"bad": True}})

    with pytest.raises(SourceError, match="Unexpected OpenEMR GitHub releases response"):
        source.resolve(_declaration(source_kind="openemr-container", current="8.1.1", metadata={}))


def test_openemr_container_requires_an_official_arm64_release() -> None:
    source, _ = _sources(
        {
            "hub.docker.com": {
                "results": [{"name": "8.1.1", "images": [_image("sha256:" + "1" * 64, "amd64")]}],
                "next": None,
            },
            "api.github.com": [{"tag_name": "v8_1_1"}],
        }
    )

    with pytest.raises(SourceError, match="No ARM64 OpenEMR image"):
        source.resolve(_declaration(source_kind="openemr-container", current="8.1.1", metadata={}))


def test_emr_serverless_requires_release_labels() -> None:
    source, _ = _sources({"docs.aws.amazon.com": "<p>No labels here</p>"})

    with pytest.raises(SourceError, match="No EMR Serverless release labels"):
        source.resolve(_declaration(source_kind="emr-serverless"))


def test_lambda_runtime_requires_supported_python_versions() -> None:
    source, _ = _sources(
        {
            "docs.aws.amazon.com": "Node.js 22 only",
            "www.python.org": [{"name": "Python 3.14.1", "is_published": True}],
        }
    )

    with pytest.raises(SourceError, match="No supported Python runtimes"):
        source.resolve(_declaration(source_kind="lambda-runtime"))


def test_aurora_requires_installed_cdk(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def blocked_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "aws_cdk":
            raise ImportError("aws_cdk unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    source, _ = _sources({})

    with pytest.raises(SourceError, match="aws-cdk-lib is not installed"):
        source.resolve(_declaration(source_kind="aws-cdk-aurora", current="3.12.0"))


def test_aurora_rejects_invalid_declared_version() -> None:
    source, _ = _sources({})

    with pytest.raises(SourceError, match="Invalid declared Aurora version"):
        source.resolve(_declaration(source_kind="aws-cdk-aurora", current="three"))


def test_aurora_requires_constants_on_the_declared_major() -> None:
    source, _ = _sources({})

    with pytest.raises(SourceError, match="no Aurora MySQL 99.x engine constants"):
        source.resolve(_declaration(source_kind="aws-cdk-aurora", current="99.0.0"))


def test_python_toolchain_requires_a_stable_release() -> None:
    source, _ = _sources({"www.python.org": [{"name": "Python 3.15.0a1", "is_published": True}]})

    with pytest.raises(SourceError, match="No stable Python release"):
        source.resolve(_declaration(source_kind="python-toolchain"))


def test_node_toolchain_returns_latest_lts() -> None:
    source, _ = _sources(
        {
            "nodejs.org": [
                {"version": "v25.0.0", "lts": False},
                {"version": "v24.3.0", "lts": "Krypton"},
                {"version": "v22.9.0", "lts": "Jod"},
            ]
        }
    )

    resolution = source.resolve(_declaration(source_kind="node-toolchain"))

    assert resolution.latest == "v24.3.0"
    assert resolution.note == "Latest active Node.js LTS release"


def test_node_toolchain_requires_an_lts_release() -> None:
    source, _ = _sources({"nodejs.org": [{"version": "v25.0.0", "lts": False}]})

    with pytest.raises(SourceError, match="No active Node.js LTS"):
        source.resolve(_declaration(source_kind="node-toolchain"))


def test_go_toolchain_returns_latest_stable() -> None:
    source, _ = _sources(
        {"go.dev": [{"version": "go1.27rc1", "stable": False}, {"version": "go1.26.2", "stable": True}]},
    )

    resolution = source.resolve(_declaration(source_kind="go-toolchain"))

    assert resolution == Resolution(latest="1.26.2", source_url="https://go.dev/dl/?mode=json")


def test_go_toolchain_requires_a_stable_release() -> None:
    source, _ = _sources({"go.dev": [{"version": "go1.27rc1", "stable": False}]})

    with pytest.raises(SourceError, match="No stable Go toolchain"):
        source.resolve(_declaration(source_kind="go-toolchain"))


def test_allowed_source_hosts_are_https_registries_only() -> None:
    for url in (
        "http://pypi.org/x",
        "https://user@pypi.org/x",
        "https://user:pw@pypi.org/x",
        "https://pypi.org:8443/x",
        "https://pypi.org/x#fragment",
    ):
        with pytest.raises(SourceError):
            sources_module._validate_source_url(url)
    assert sources_module._validate_source_url("https://pypi.org:443/x") == "https://pypi.org:443/x"
