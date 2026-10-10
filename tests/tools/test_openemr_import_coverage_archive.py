"""Archive safety checks for OpenEMR site archives (tar and zip)."""

from __future__ import annotations

import base64
import io
import stat
import tarfile
import zipfile

import pytest

from tools._shared import ToolError
from tools.openemr_import import archive
from tools.openemr_import.archive import (
    _is_active_content,
    _parse_version,
    _safe_member_name,
    _valid_drive_key,
    scan_sites_archive,
)
from tools.openemr_import.models import ArchiveLimits

_KEY_A = b"007" + base64.b64encode(b"a" * 96)
_KEY_B = b"007" + base64.b64encode(b"b" * 96)


def _tar(members: list[tuple[str, bytes | None]], *, mode: str = "w") -> io.BytesIO:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode=mode) as handle:
        for name, content in members:
            info = tarfile.TarInfo(name)
            if content is None:
                info.type = tarfile.DIRTYPE
                info.mode = 0o700
                handle.addfile(info)
            else:
                info.size = len(content)
                info.mode = 0o600
                handle.addfile(info, io.BytesIO(content))
    output.seek(0)
    return output


def _zip(members: list[tuple[str, bytes]], *, compression: int = zipfile.ZIP_STORED) -> io.BytesIO:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as handle:
        for name, content in members:
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            info.compress_type = compression
            handle.writestr(info, content)
    output.seek(0)
    return output


def _valid_site_members(prefix: str = "sites/default") -> list[tuple[str, bytes]]:
    return [
        (f"{prefix}/sqlconf.php", b"<?php ?>"),
        (f"{prefix}/documents/1/report.pdf", b"%PDF-1.4"),
        (f"{prefix}/documents/logs_and_misc/methods/sevena", _KEY_A),
        (f"{prefix}/documents/logs_and_misc/methods/sevenb", _KEY_B),
    ]


@pytest.mark.parametrize(
    ("name", "message"),
    (
        ("sites/default/\x00evil", "unsafe member path"),
        ("sites\\default\\evil", "unsafe member path"),
        ("/etc/passwd", "absolute or empty"),
        ("C:/Windows/evil", "absolute or empty"),
        ("./", "absolute or empty"),
        ("sites/default/../../escape", "path traversal"),
        ("sites/..", "path traversal"),
    ),
)
def test_unsafe_member_names_are_refused(name: str, message: str) -> None:
    with pytest.raises(ToolError, match=message):
        _safe_member_name(name)


def test_leading_dot_slash_prefixes_are_normalized() -> None:
    assert _safe_member_name("././sites/default/sqlconf.php").as_posix() == "sites/default/sqlconf.php"


def test_version_parsing_requires_core_fields_and_normalizes_suffixes() -> None:
    assert _parse_version(b"$v_major = '8';\n$v_minor = '2';") == (None, None)
    assert _parse_version(
        b"$v_major = '8'; $v_minor = '2'; $v_patch = '0'; $v_realpatch = '3'; $v_tag = '-dev'; $v_database = 541;"
    ) == ("8.2.0.3-dev", 541)
    assert _parse_version(b"$v_major = 8; $v_minor = 2; $v_patch = 0; $v_database = 'abc';") == ("8.2.0", None)
    assert _parse_version(b"$v_major = 8; $v_minor = 2; $v_patch = 0;") == ("8.2.0", None)


def test_referral_template_html_is_not_active_but_other_html_is() -> None:
    from pathlib import PurePosixPath

    template = PurePosixPath("sites/default/referral_template.html")
    other = PurePosixPath("sites/default/documents/page.html")

    assert _is_active_content(template, 0o600, b"Dear colleague") is False
    assert _is_active_content(other, 0o600, b"Dear colleague") is True
    assert _is_active_content(template, 0o600, b"<script>alert(1)</script>") is True


def test_drive_key_requires_prefix_valid_base64_and_length() -> None:
    assert _valid_drive_key(_KEY_A) is True
    assert _valid_drive_key(b"008" + base64.b64encode(b"a" * 96)) is False
    assert _valid_drive_key(b"007" + b"not*base64!") is False
    assert _valid_drive_key(b"007" + base64.b64encode(b"short")) is False


def test_tar_scan_collects_site_inventory_and_application_facts() -> None:
    members: list[tuple[str, bytes | None]] = [
        (".", None),
        ("sites", None),
        ("sites/README", b"top-level file under sites"),
        ("sites/default", None),
        ("version.php", b"$v_major = '8'; $v_minor = '2'; $v_patch = '0'; $v_database = 541;"),
        ("custom/hooks.php", b"<?php"),
        ("bundle.zip", b"nested"),
        *_valid_site_members(),
        ("sites/default/documents/certificates/ca.pem", b"cert"),
        ("sites/default/documents/edi/claim.txt", b"edi"),
        ("sites/default/images/logo.png", b"\x89PNG"),
        ("sites/default/images/payload.sh", b"echo hi"),
        ("sites/other/config.php", b"<?php"),
    ]

    scan = scan_sites_archive(_tar(members), format_hint="openemr.tar", limits=ArchiveLimits())

    by_id = {site.site_id: site for site in scan.sites}
    default = by_id["default"]
    assert scan.openemr_version == "8.2.0"
    assert scan.database_version == 541
    assert scan.nested_archive_count == 1
    assert scan.custom_code_detected is True
    assert scan.ignored_application_file_count == 3
    assert default.has_encryption_keys is True
    assert default.certificate_count == 1
    assert default.edi_file_count == 1
    assert default.executable_file_count == 1
    assert by_id["other"].has_sqlconf is False
    assert "Site 'other' is missing its sqlconf.php marker" in scan.unsupported_content
    assert "Site 'other' is missing its documents directory" in scan.unsupported_content
    assert any("executable or script-like" in item for item in scan.manual_review)
    assert any("Custom executable application content" in item for item in scan.manual_review)


def test_tar_without_sites_tree_is_unsupported() -> None:
    scan = scan_sites_archive(_tar([("README", b"hi")]), format_hint="x.tar", limits=ArchiveLimits())

    assert scan.sites == ()
    assert scan.unsupported_content == ("No canonical sites/<site-id> tree was found",)


def test_invalid_site_identifier_is_refused() -> None:
    with pytest.raises(ToolError, match="invalid OpenEMR site identifier"):
        scan_sites_archive(_tar([("sites/bad.id/sqlconf.php", b"x")]), format_hint="x.tar", limits=ArchiveLimits())


@pytest.mark.parametrize(
    ("members", "limits", "message"),
    (
        (
            [("sites/default/a", b"1"), ("sites/default/a", b"2")],
            ArchiveLimits(),
            "duplicate member paths",
        ),
        (
            [("sites/default/A", b"1"), ("sites/default/a", b"2")],
            ArchiveLimits(),
            "case-colliding",
        ),
        (
            [("sites/default/a", b"1"), ("sites/default/b", b"2")],
            ArchiveLimits(max_members=1),
            "member-count limit",
        ),
        (
            [("sites/default/a", b"12345")],
            ArchiveLimits(max_member_bytes=4),
            "member-size limit",
        ),
        (
            [("sites/default/a", b"123"), ("sites/default/b", b"123")],
            ArchiveLimits(max_expanded_bytes=5),
            "expanded-size limit",
        ),
    ),
    ids=("duplicate", "case-collision", "member-count", "member-size", "expanded-size"),
)
def test_tar_limits_and_collisions_fail_closed(
    members: list[tuple[str, bytes | None]],
    limits: ArchiveLimits,
    message: str,
) -> None:
    with pytest.raises(ToolError, match=message):
        scan_sites_archive(_tar(members), format_hint="x.tar", limits=limits)


def test_malformed_tar_is_refused() -> None:
    with pytest.raises(ToolError, match="Malformed or unsupported tar"):
        scan_sites_archive(io.BytesIO(b"\x1f\x8b not really gzip"), format_hint="x.tar.gz", limits=ArchiveLimits())


def test_unreadable_tar_member_is_treated_as_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tarfile.TarFile, "extractfile", lambda self, member: None)
    members: list[tuple[str, bytes | None]] = [
        ("version.php", b"$v_major = '8'; $v_minor = '2'; $v_patch = '0';"),
        *_valid_site_members(),
    ]

    scan = scan_sites_archive(_tar(members), format_hint="x.tar", limits=ArchiveLimits())

    assert scan.openemr_version is None
    assert scan.sites[0].has_encryption_keys is False


def test_zip_scan_collects_the_same_inventory_as_tar() -> None:
    members = [
        ("version.php", b"$v_major = '8'; $v_minor = '2'; $v_patch = '0'; $v_database = 541;"),
        *_valid_site_members(),
    ]

    scan = scan_sites_archive(_zip(members), format_hint="sites.ZIP", limits=ArchiveLimits())

    assert scan.openemr_version == "8.2.0"
    assert scan.member_count == 5
    assert scan.sites[0].site_id == "default"
    assert scan.sites[0].has_encryption_keys is True
    assert scan.unsupported_content == ()


def test_zip_encrypted_members_are_refused() -> None:
    payload = bytearray(_zip([("sites/default/sqlconf.php", b"x")]).getvalue())
    central = payload.index(b"PK\x01\x02")
    payload[central + 8] |= 0x1

    with pytest.raises(ToolError, match="Encrypted zip members"):
        scan_sites_archive(io.BytesIO(bytes(payload)), format_hint="a.zip", limits=ArchiveLimits())


def test_zip_symlinks_are_refused() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as handle:
        info = zipfile.ZipInfo("sites/default/documents/link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        handle.writestr(info, "/etc/passwd")
    output.seek(0)

    with pytest.raises(ToolError, match="symlinks are not allowed"):
        scan_sites_archive(output, format_hint="a.zip", limits=ArchiveLimits())


def test_zip_compression_ratio_is_bounded() -> None:
    bomb = _zip([("sites/default/documents/zeros.bin", b"\0" * 100_000)], compression=zipfile.ZIP_DEFLATED)

    with pytest.raises(ToolError, match="compression-ratio limit"):
        scan_sites_archive(bomb, format_hint="a.zip", limits=ArchiveLimits(max_compression_ratio=10))


def test_zip_path_traversal_is_refused() -> None:
    with pytest.raises(ToolError, match="path traversal"):
        scan_sites_archive(_zip([("sites/../../etc/passwd", b"x")]), format_hint="a.zip", limits=ArchiveLimits())


def test_malformed_zip_is_refused() -> None:
    with pytest.raises(ToolError, match="Malformed or unsupported zip"):
        scan_sites_archive(io.BytesIO(b"not a zip"), format_hint="a.zip", limits=ArchiveLimits())


def test_scan_dispatches_by_format_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(archive, "_scan_zip", lambda fileobj, limits: calls.append("zip"))
    monkeypatch.setattr(archive, "_scan_tar", lambda fileobj, limits: calls.append("tar"))

    scan_sites_archive(io.BytesIO(), format_hint="Sites.Zip", limits=ArchiveLimits())
    scan_sites_archive(io.BytesIO(), format_hint="openemr.tar.gz", limits=ArchiveLimits())

    assert calls == ["zip", "tar"]
