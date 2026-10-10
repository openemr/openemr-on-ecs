"""Bounded external sources used by the version audit."""

from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import requests
from packaging.version import InvalidVersion, Version

from .models import Declaration

_ALLOWED_SOURCE_HOSTS = {
    "api.github.com",
    "docs.aws.amazon.com",
    "go.dev",
    "hub.docker.com",
    "nodejs.org",
    "proxy.golang.org",
    "pypi.org",
    "www.python.org",
    "registry.npmjs.org",
}


class SourceError(RuntimeError):
    """Raised when one external source cannot produce a trustworthy result."""


@dataclass(frozen=True)
class Resolution:
    """Latest stable and prerelease values returned by a source."""

    latest: str | None
    source_url: str
    latest_prerelease: str | None = None
    note: str | None = None
    current_reference_verified: bool | None = None


def _version(value: str) -> Version:
    normalized = value.strip().removeprefix("v")
    return Version(normalized)


def _highest(values: list[str], *, prerelease: bool = False) -> str | None:
    parsed: list[tuple[Version, str]] = []
    for value in values:
        try:
            candidate = _version(value)
        except InvalidVersion:
            continue
        if bool(candidate.is_prerelease or candidate.is_devrelease) != prerelease:
            continue
        parsed.append((candidate, value))
    return max(parsed, default=(None, None), key=lambda item: item[0])[1]  # type: ignore[arg-type,return-value]


def _go_proxy_escape(module: str) -> str:
    escaped: list[str] = []
    for character in module:
        if character.isupper():
            escaped.extend(("!", character.lower()))
        else:
            escaped.append(character)
    return "".join(escaped)


def _validate_source_url(url: str) -> str:
    """Require an HTTPS URL on the audit's fixed authoritative host allowlist."""

    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_SOURCE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.fragment
    ):
        raise SourceError("Version source URL is outside the authoritative host policy")
    return url


class HttpClient:
    """Small HTTP client with fixed timeouts, response limits, and caching."""

    def __init__(
        self,
        timeout_seconds: float = 12.0,
        max_bytes: int = 5_000_000,
        github_token: str | None = None,
    ):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json, text/plain;q=0.9, text/html;q=0.8",
                "User-Agent": "openemr-on-ecs-version-audit/1",
            }
        )
        self.github_token = github_token if github_token is not None else os.environ.get("GITHUB_TOKEN", "")
        self._cache: dict[str, bytes] = {}

    def get_bytes(self, url: str) -> bytes:
        """Fetch a bounded response or raise a source-local error."""

        if url in self._cache:
            return self._cache[url]
        response: requests.Response | None = None
        try:
            current_url = _validate_source_url(url)
            for _ in range(6):
                request_headers = None
                if urlparse(current_url).hostname == "api.github.com" and self.github_token:
                    request_headers = {
                        "Authorization": f"Bearer {self.github_token}",
                        "X-GitHub-Api-Version": "2022-11-28",
                    }
                response = self.session.get(
                    current_url,
                    timeout=self.timeout_seconds,
                    stream=True,
                    allow_redirects=False,
                    headers=request_headers,
                )
                if response.status_code not in {301, 302, 303, 307, 308}:
                    break
                location = response.headers.get("Location")
                response.close()
                response = None
                if not location:
                    raise SourceError("Version source redirect omitted its destination")
                current_url = _validate_source_url(urljoin(current_url, location))
            else:
                raise SourceError("Version source exceeded the redirect limit")
            assert response is not None  # the loop only breaks with a live response
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > self.max_bytes:
                raise SourceError(f"Response exceeds {self.max_bytes} bytes")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                total += len(chunk)
                if total > self.max_bytes:
                    raise SourceError(f"Response exceeds {self.max_bytes} bytes")
                chunks.append(chunk)
            body = b"".join(chunks)
        except (requests.RequestException, ValueError) as exc:
            raise SourceError(str(exc)) from exc
        finally:
            if response is not None:
                response.close()
        self._cache[url] = body
        return body

    def get_json(self, url: str) -> Any:
        """Fetch and decode a bounded JSON response."""

        try:
            return json.loads(self.get_bytes(url))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceError(f"Invalid JSON from {url}: {exc}") from exc

    def get_text(self, url: str) -> str:
        """Fetch and decode bounded UTF-8 text."""

        try:
            return self.get_bytes(url).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SourceError(f"Invalid UTF-8 from {url}") from exc


class VersionSources:
    """Resolve repository declarations against authoritative public sources."""

    def __init__(self, client: HttpClient):
        self.client = client
        self._github_cache: dict[str, Resolution] = {}

    def resolve(self, declaration: Declaration) -> Resolution:
        """Dispatch a declaration to its source-specific resolver."""

        try:
            return self._resolve_unchecked(declaration)
        except SourceError:
            raise
        except (AttributeError, InvalidVersion, KeyError, TypeError, ValueError) as exc:
            raise SourceError(
                f"Malformed {declaration.source_kind} response for {declaration.name}",
            ) from exc

    def _resolve_unchecked(self, declaration: Declaration) -> Resolution:
        """Resolve one declaration while allowing the public wrapper to isolate parsing failures."""

        source_kind = declaration.source_kind
        if source_kind == "pypi":
            return self._pypi(declaration)
        if source_kind == "go-proxy":
            return self._go_proxy(declaration)
        if source_kind == "github-release":
            return self._github_release(declaration)
        if source_kind == "npm":
            return self._npm(declaration)
        if source_kind == "openemr-container":
            return self._openemr_container(declaration)
        if source_kind == "emr-serverless":
            return self._emr_serverless()
        if source_kind == "lambda-runtime":
            return self._lambda_runtime()
        if source_kind == "aws-cdk-aurora":
            return self._aurora_from_cdk(declaration)
        if source_kind == "python-toolchain":
            return self._python_toolchain()
        if source_kind == "node-toolchain":
            return self._node_toolchain()
        if source_kind == "go-toolchain":
            return self._go_toolchain()
        if source_kind == "inventory-error":
            raise SourceError(str(declaration.metadata.get("error", "Inventory parsing failed")))
        if source_kind == "manual":
            raise SourceError("Declaration uses a direct URL and requires manual review")
        raise SourceError(f"No resolver is configured for source kind {source_kind}")

    def _pypi(self, declaration: Declaration) -> Resolution:
        normalized = str(declaration.metadata.get("normalized_name") or declaration.name)
        url = f"https://pypi.org/pypi/{quote(normalized, safe='')}/json"
        data = self.client.get_json(url)
        releases = data.get("releases", {})
        stable: list[str] = []
        prerelease: list[str] = []
        for raw_version, files in releases.items():
            if not isinstance(files, list) or not files:
                continue
            try:
                parsed = Version(raw_version)
            except InvalidVersion:
                continue
            if all(not isinstance(item, dict) or bool(item.get("yanked")) for item in files):
                continue
            if parsed.is_prerelease or parsed.is_devrelease:
                prerelease.append(raw_version)
            else:
                stable.append(raw_version)
        latest = _highest(stable)
        if latest is None:
            raise SourceError(f"No non-yanked stable release found for {declaration.name}")
        return Resolution(
            latest=latest,
            latest_prerelease=_highest(prerelease, prerelease=True),
            source_url=url,
        )

    def _go_proxy(self, declaration: Declaration) -> Resolution:
        module = str(declaration.metadata.get("module") or declaration.name)
        escaped = _go_proxy_escape(module)
        url = f"https://proxy.golang.org/{quote(escaped, safe='!/')}/@v/list"
        versions = [line.strip() for line in self.client.get_text(url).splitlines() if line.strip()]
        latest = _highest(versions)
        if latest is None:
            raise SourceError(f"No stable semantic versions returned for {module}")
        return Resolution(
            latest=latest,
            latest_prerelease=_highest(versions, prerelease=True),
            source_url=url,
        )

    def _github_release(self, declaration: Declaration) -> Resolution:
        repository = str(declaration.metadata.get("repository") or declaration.name)
        repository = repository.removesuffix(".git").split("github.com/", 1)[-1].strip("/")
        if repository in self._github_cache:
            return self._verify_github_action_pin(
                declaration,
                repository,
                self._github_cache[repository],
            )
        if repository.count("/") != 1:
            raise SourceError(f"Invalid GitHub repository slug: {repository}")
        url = f"https://api.github.com/repos/{repository}/releases?per_page=100"
        try:
            data = self.client.get_json(url)
        except SourceError:
            data = []
        if not isinstance(data, list):
            raise SourceError(f"Unexpected GitHub response for {repository}")
        stable: list[str] = []
        prerelease: list[str] = []
        for release in data:
            if release.get("draft"):
                continue
            tag = str(release.get("tag_name", "")).strip()
            if not tag:
                continue
            try:
                parsed = _version(tag)
            except InvalidVersion:
                continue
            if release.get("prerelease") or parsed.is_prerelease or parsed.is_devrelease:
                prerelease.append(tag)
            else:
                stable.append(tag)
        latest = _highest(stable)
        if latest is None:
            tags_url = f"https://api.github.com/repos/{repository}/tags?per_page=100"
            tags = self.client.get_json(tags_url)
            stable = [str(item.get("name", "")) for item in tags if item.get("name")]
            latest = _highest(stable)
            url = tags_url
        if latest is None:
            raise SourceError(f"No stable semantic release found for {repository}")
        resolution = Resolution(
            latest=latest,
            latest_prerelease=_highest(prerelease, prerelease=True),
            source_url=url,
        )
        self._github_cache[repository] = resolution
        return self._verify_github_action_pin(declaration, repository, resolution)

    def _verify_github_action_pin(
        self,
        declaration: Declaration,
        repository: str,
        resolution: Resolution,
    ) -> Resolution:
        labels = declaration.metadata.get("revision_labels")
        if declaration.category not in {
            "github-actions",
            "pre-commit",
        } or not declaration.metadata.get("immutable_sha_pins"):
            return resolution
        if not isinstance(labels, dict) or not labels:
            raise SourceError(f"Immutable GitHub pins lack version labels for {repository}")
        for revision, label in labels.items():
            if (
                not isinstance(revision, str)
                or not re.fullmatch(r"[0-9a-fA-F]{40}", revision)
                or not isinstance(label, str)
                or not label
            ):
                raise SourceError(f"Invalid immutable GitHub pin metadata for {repository}")
            url = f"https://api.github.com/repos/{repository}/commits/{quote(label, safe='')}"
            commit = self.client.get_json(url)
            resolved_sha = str(commit.get("sha", "")) if isinstance(commit, dict) else ""
            if not re.fullmatch(r"[0-9a-fA-F]{40}", resolved_sha):
                raise SourceError(f"GitHub did not resolve {repository}@{label} to a commit")
            if resolved_sha.lower() != revision.lower():
                return Resolution(
                    latest=resolution.latest,
                    source_url=url,
                    latest_prerelease=resolution.latest_prerelease,
                    note=f"Immutable SHA does not match the labelled GitHub tag {label}",
                    current_reference_verified=False,
                )
        return Resolution(
            latest=resolution.latest,
            source_url=resolution.source_url,
            latest_prerelease=resolution.latest_prerelease,
            note=resolution.note,
            current_reference_verified=True,
        )

    def _npm(self, declaration: Declaration) -> Resolution:
        package = str(declaration.metadata.get("package") or declaration.name)
        url = f"https://registry.npmjs.org/{quote(package, safe='@')}/latest"
        data = self.client.get_json(url)
        version = str(data.get("version", "")).strip()
        if not version:
            raise SourceError(f"npm did not return a latest version for {package}")
        try:
            parsed = Version(version)
        except InvalidVersion as exc:
            raise SourceError(f"npm returned an invalid latest version for {package}") from exc
        if parsed.is_prerelease or parsed.is_devrelease:
            raise SourceError(f"npm latest points to a prerelease for {package}")
        return Resolution(latest=version, source_url=url)

    def _openemr_container(self, declaration: Declaration) -> Resolution:
        docker_url = "https://hub.docker.com/v2/repositories/openemr/openemr/tags?page_size=100"
        url = docker_url
        numeric_tags: list[str] = []
        prerelease: list[str] = []
        current_arm64_digests: set[str] = set()
        pages = 0
        while url and pages < 20:
            data = self.client.get_json(_validate_source_url(url))
            pages += 1
            for entry in data.get("results", []):
                tag = str(entry.get("name", "")).strip()
                arm64_images = [
                    image
                    for image in entry.get("images", [])
                    if str(image.get("architecture", "")).lower() == "arm64"
                    and str(image.get("os", "")).lower() == "linux"
                ]
                try:
                    parsed = Version(tag)
                except InvalidVersion:
                    continue
                if not arm64_images:
                    continue
                if tag == declaration.current:
                    current_arm64_digests.update(
                        str(image.get("digest", "")).lower() for image in arm64_images if image.get("digest")
                    )
                if parsed.is_prerelease or parsed.is_devrelease:
                    prerelease.append(tag)
                else:
                    numeric_tags.append(tag)
            url = data.get("next")
        releases_url = "https://api.github.com/repos/openemr/openemr/releases?per_page=100"
        releases = self.client.get_json(releases_url)
        official_versions: set[Version] = set()
        official_prereleases: set[Version] = set()
        if not isinstance(releases, list):
            raise SourceError("Unexpected OpenEMR GitHub releases response")
        for release in releases:
            if release.get("draft"):
                continue
            raw_tag = str(release.get("tag_name", "")).strip().removeprefix("v").replace("_", ".")
            try:
                parsed = Version(raw_tag)
            except InvalidVersion:
                continue
            if release.get("prerelease") or parsed.is_prerelease or parsed.is_devrelease:
                official_prereleases.add(parsed)
            else:
                official_versions.add(parsed)
        stable = [tag for tag in numeric_tags if _version(tag) in official_versions]
        prerelease = [tag for tag in prerelease if _version(tag) in official_prereleases]
        latest = _highest(stable)
        if latest is None:
            raise SourceError("No ARM64 OpenEMR image matched an official stable OpenEMR release")
        expected_digest = str(declaration.metadata.get("arm64_digest", "")).strip()
        digest_url = (
            f"https://hub.docker.com/v2/repositories/openemr/openemr/tags/{quote(declaration.current, safe='')}"
        )
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest):
            return Resolution(
                latest=latest,
                latest_prerelease=_highest(prerelease, prerelease=True),
                source_url=digest_url,
                note="OpenEMR ARM64 digest pin is missing or malformed",
                current_reference_verified=False,
            )
        if expected_digest not in current_arm64_digests:
            return Resolution(
                latest=latest,
                latest_prerelease=_highest(prerelease, prerelease=True),
                source_url=digest_url,
                note=(
                    f"Pinned ARM64 digest {expected_digest} does not match the current "
                    f"linux/arm64 manifest for openemr/openemr:{declaration.current}"
                ),
                current_reference_verified=False,
            )
        return Resolution(
            latest=latest,
            latest_prerelease=_highest(prerelease, prerelease=True),
            source_url=releases_url,
            note=(
                "Requires both an ARM64 Docker tag and a matching non-draft, non-prerelease "
                "OpenEMR GitHub release; numeric development tags are excluded and the pinned "
                "linux/arm64 digest matches the current tag"
            ),
            current_reference_verified=True,
        )

    def _emr_serverless(self) -> Resolution:
        url = "https://docs.aws.amazon.com/emr/latest/EMR-Serverless-UserGuide/release-versions.html"
        text = html.unescape(self.client.get_text(url))
        labels = sorted(
            set(re.findall(r"\bemr-(\d+\.\d+\.\d+)\b", text))
            | set(re.findall(r"EMR\s+Serverless(?:</?[^>]+>|\s)+(\d+\.\d+\.\d+)", text))
        )
        latest = _highest(labels)
        if latest is None:
            raise SourceError("No EMR Serverless release labels found in AWS documentation")
        return Resolution(latest=f"emr-{latest}", source_url=url)

    def _lambda_runtime(self) -> Resolution:
        url = "https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html"
        text = html.unescape(self.client.get_text(url))
        versions = sorted(set(re.findall(r"\bPython\s+(\d+\.\d+)\b", text)))
        python_latest = self._python_toolchain().latest
        if python_latest:
            stable_python = _version(python_latest)
            versions = [value for value in versions if _version(value).release[:2] <= stable_python.release[:2]]
        latest = _highest(versions)
        if latest is None:
            raise SourceError("No supported Python runtimes found in AWS Lambda documentation")
        return Resolution(
            latest=latest,
            source_url=url,
            note="AWS documentation support table; region availability still requires deployment review",
        )

    def _aurora_from_cdk(self, declaration: Declaration) -> Resolution:
        url = "https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.aws_rds/AuroraMysqlEngineVersion.html"
        try:
            from aws_cdk import aws_rds as rds
        except ImportError as exc:
            raise SourceError("aws-cdk-lib is not installed; Aurora values cannot be enumerated") from exc
        try:
            current_major = _version(declaration.current).major
        except InvalidVersion as exc:
            raise SourceError(f"Invalid declared Aurora version: {declaration.current}") from exc
        values: list[str] = []
        for attribute in dir(rds.AuroraMysqlEngineVersion):
            if not re.fullmatch(r"VER_\d+_\d+_\d+", attribute):
                continue
            value = attribute.removeprefix("VER_").replace("_", ".")
            if _version(value).major == current_major:
                values.append(value)
        latest = _highest(values)
        if latest is None:
            raise SourceError(f"Installed CDK library exposed no Aurora MySQL {current_major}.x engine constants")
        return Resolution(
            latest=latest,
            source_url=url,
            note=(
                "Latest value exposed by the declared CDK library; verify target-region availability "
                "and OpenEMR/Bedrock compatibility before adoption"
            ),
        )

    def _python_toolchain(self) -> Resolution:
        url = "https://www.python.org/api/v2/downloads/release/?is_published=true"
        data = self.client.get_json(url)
        stable_releases: list[str] = []
        for release in data:
            if release.get("pre_release") or not release.get("is_published"):
                continue
            match = re.fullmatch(r"Python (\d+\.\d+\.\d+)", str(release.get("name", "")))
            if match:
                stable_releases.append(match.group(1))
        latest_python = _highest(stable_releases)
        if latest_python is None:
            raise SourceError("No stable Python release returned")
        return Resolution(
            latest=latest_python,
            source_url=url,
            note="Latest published CPython maintenance release",
        )

    def _node_toolchain(self) -> Resolution:
        url = "https://nodejs.org/dist/index.json"
        data = self.client.get_json(url)
        lts_versions = [str(item.get("version", "")) for item in data if item.get("lts")]
        latest = _highest(lts_versions)
        if latest is None:
            raise SourceError("No active Node.js LTS release returned")
        return Resolution(latest=latest, source_url=url, note="Latest active Node.js LTS release")

    def _go_toolchain(self) -> Resolution:
        url = "https://go.dev/dl/?mode=json"
        data = self.client.get_json(url)
        versions = [str(item.get("version", "")).removeprefix("go") for item in data if item.get("stable")]
        latest = _highest(versions)
        if latest is None:
            raise SourceError("No stable Go toolchain release returned")
        return Resolution(latest=latest, source_url=url)
