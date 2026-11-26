"""Local upstream notice retention; synthetic archives, no network."""
import io
import tarfile
from pathlib import Path

import pytest
from pharos import maps_viewer_assets as assets
from pharos.config import load_config


def test_package_notices_retained(tmp_path):
    archive = tmp_path / "package.tgz"
    output = tmp_path / "out"
    output.mkdir()
    with tarfile.open(archive, "w:gz") as tar:
        for name in ("package/LICENSE.txt", "package/NOTICE", "package/other.js"):
            data = name.encode()
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    assets._extract_package_notices(archive, "pmtiles", output)
    assert {p.name for p in output.iterdir()} == {"pmtiles-license.txt", "pmtiles-notice"}
    assert (output / "pmtiles-notice").read_bytes() == b"package/NOTICE"


def test_notice_symlink_rejected(tmp_path):
    archive = tmp_path / "package.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo("package/LICENSE")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        tar.addfile(member)
    with pytest.raises(assets.MapViewerAssetError):
        assets._extract_package_notices(archive, "pmtiles", tmp_path)


def test_existing_assets_notice_only(prepared, monkeypatch):
    cfg = load_config(prepared)
    root = assets.asset_directory(cfg)
    root.mkdir(parents=True)
    monkeypatch.setattr(assets, "ready", lambda config: True)
    def no_network(*args):
        raise AssertionError("unexpected network")
    monkeypatch.setattr(assets, "_metadata", no_network)
    existing = root / "pmtiles.js"
    existing.write_bytes(b"existing")
    assert assets.install(cfg)["installed"]
    assert existing.read_bytes() == b"existing"
    assert (root / "licenses" / "noto-sans-OFL.txt").is_file()
    assert (root / "licenses" / "maplibre-gl-6.16.0.txt").is_file()
