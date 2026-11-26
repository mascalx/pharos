"""Install and serve pinned, locally cached offline MapLibre/PMTiles browser assets.

Only the official npm registry supplies packages; each tarball is checked
against its publisher-provided SHA-512 integrity before selective extraction.
The frontend never loads third-party CDNs. The setup is optional when an
existing offline install is upgraded without Internet access.
"""
from __future__ import annotations

import base64
import hashlib
from urllib.parse import quote
import io
import json
import os
import re
import tarfile
import tempfile
import threading
import urllib.request
from pathlib import Path

from .kiwix_runtime import _PublisherRedirects

PACKAGES = {
    "maplibre-gl": ("6.16.0", {
        "dist/maplibre-gl.mjs": "maplibre-gl.mjs",
        "dist/maplibre-gl-worker.mjs": "maplibre-gl-worker.mjs",
        "dist/maplibre-gl.css": "maplibre-gl.css",
    }),
    "pmtiles": ("4.5.0", {"dist/pmtiles.js": "pmtiles.js"}),
}
ASSET_FILES = ("maplibre-gl.mjs", "maplibre-gl-worker.mjs", "maplibre-gl.css", "pmtiles.js")
# Pin the official Protomaps basemaps-assets git revision and published blob
# digests. These five Noto Sans ranges cover Latin, Greek and Cyrillic names.
# Additional scripts can be installed in a future release.
FONT_COMMIT = "028c18f713baecad011301ff7a69acc39bcc2ae7"
FONT_STACK = "Noto Sans Regular"
FONT_BLOBS = {
    "0-255.pbf": "7f65901599b368dc8c1d70d5fed9642148db9836",
    "256-511.pbf": "f0302889321b2fb9f83e13b5df1a9a6b0b10e6f3",
    "512-767.pbf": "5cde89d339b3cf0be2039f376fe20cadc75e533e",
    "768-1023.pbf": "a1d0bd9140db88a231ba88991e3d3e2191387448",
    "1024-1279.pbf": "d2ae7ab5bc345cbffad661e017e9444a27f78444",
}
MAX_FONT_BYTES = 1024 * 1024
FONT_MANIFEST = "fonts-manifest.json"

MANIFEST = "manifest.json"
MAX_META_BYTES = 384 * 1024
MAX_TARBALL_BYTES = 50 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024
_lock = threading.RLock()


class MapViewerAssetError(RuntimeError):
    pass


def asset_directory(config) -> Path:
    return config.storage.database_dir / "pharos-managed-services" / "maps-viewer"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ready(config) -> bool:
    root = asset_directory(config)
    if root.is_symlink():
        return False
    manifest = root / MANIFEST
    if manifest.is_symlink():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("packages") != {key: value[0] for key, value in PACKAGES.items()}:
            return False
        checksums = data["sha256"]
        for name in ASSET_FILES:
            path = root / name
            if path.is_symlink() or not path.is_file():
                return False
            expected = checksums.get(name)
            if not isinstance(expected, str) or not re.fullmatch("[a-f0-9]{64}", expected):
                return False
            if _sha256(path) != expected:
                return False
        return True
    except (OSError, KeyError, ValueError, TypeError):
        return False



def font_directory(config) -> Path:
    return asset_directory(config) / "fonts" / FONT_STACK


def labels_ready(config) -> bool:
    directory = font_directory(config)
    manifest = directory.parent / FONT_MANIFEST
    if directory.is_symlink() or directory.parent.is_symlink() or manifest.is_symlink():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("revision") != FONT_COMMIT or data.get("blobs") != FONT_BLOBS:
            return False
        for filename in FONT_BLOBS:
            path = directory / filename
            if not path.is_file() or path.is_symlink():
                return False
            if _sha256(path) != data["sha256"][filename]:
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _download_official_font(filename: str, target: Path) -> None:
    # Immutable official GitHub ref, rather than a mutable CDN URL.
    url = ("https://raw.githubusercontent.com/protomaps/basemaps-assets/"
           + FONT_COMMIT + "/fonts/" + quote(FONT_STACK) + "/" + filename)
    request = urllib.request.Request(url, headers={"User-Agent": "PHAROS-Maps/0.5"})
    with _opener().open(request, timeout=35) as response:
        if response.status != 200:
            raise MapViewerAssetError("maps_font_download_failed")
        contents = response.read(MAX_FONT_BYTES + 1)
    if not contents or len(contents) > MAX_FONT_BYTES:
        raise MapViewerAssetError("maps_font_invalid")
    # Match the Git blob object supplied by the official publisher. This
    # legacy Git SHA-1 is not a digital signature; origin is HTTPS and the
    # repository revision is pinned. SHA-256 is retained for local checks.
    blob = hashlib.sha1(b"blob " + str(len(contents)).encode() +
                        b"\0" + contents).hexdigest()
    if blob != FONT_BLOBS[filename]:
        raise MapViewerAssetError("maps_font_checksum_mismatch")
    target.write_bytes(contents)


def install_fonts(config) -> bool:
    with _lock:
        if labels_ready(config):
            return True
        root = asset_directory(config)
        if root.is_symlink():
            raise MapViewerAssetError("maps_viewer_storage_invalid")
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".map-fonts-", dir=root) as tmp:
            staging = Path(tmp)
            digests = {}
            for filename in FONT_BLOBS:
                local = staging / filename
                _download_official_font(filename, local)
                digests[filename] = _sha256(local)
            directory = font_directory(config)
            directory.mkdir(parents=True, exist_ok=True)
            for filename in FONT_BLOBS:
                os.replace(staging / filename, directory / filename)
            meta = staging / FONT_MANIFEST
            meta.write_text(json.dumps({"revision": FONT_COMMIT,
                "blobs": FONT_BLOBS, "sha256": digests}, sort_keys=True)+"\n",
                encoding="utf-8")
            os.replace(meta, directory.parent / FONT_MANIFEST)
        return labels_ready(config)


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _PublisherRedirects())


def _metadata(package: str, version: str) -> dict:
    url = f"https://registry.npmjs.org/{package}/{version}"
    request = urllib.request.Request(url, headers={"User-Agent": "PHAROS-Maps/0.4"})
    with _opener().open(request, timeout=35) as response:
        if response.status != 200:
            raise MapViewerAssetError("maps_viewer_registry_unavailable")
        body = response.read(MAX_META_BYTES + 1)
        if len(body) > MAX_META_BYTES:
            raise MapViewerAssetError("maps_viewer_registry_invalid")
    try:
        data = json.loads(body.decode("utf-8"))
        dist = data["dist"]
        tarball = dist["tarball"]
        integrity = dist["integrity"]
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise MapViewerAssetError("maps_viewer_registry_invalid") from exc
    expected = f"https://registry.npmjs.org/{package}/-/{package}-{version}.tgz"
    if (data.get("name") != package or data.get("version") != version or
            tarball != expected or not isinstance(integrity, str) or
            not integrity.startswith("sha512-")):
        raise MapViewerAssetError("maps_viewer_registry_invalid")
    return {"url": tarball, "integrity": integrity}


def _download_package(url: str, integrity: str, target: Path):
    try:
        expected = base64.b64decode(integrity[7:], validate=True)
    except (ValueError, TypeError) as exc:
        raise MapViewerAssetError("maps_viewer_integrity_invalid") from exc
    if len(expected) != 64:
        raise MapViewerAssetError("maps_viewer_integrity_invalid")
    digest = hashlib.sha512()
    request = urllib.request.Request(url, headers={"User-Agent": "PHAROS-Maps/0.4"})
    with _opener().open(request, timeout=75) as response, target.open("wb") as output:
        if response.status != 200:
            raise MapViewerAssetError("maps_viewer_download_failed")
        total = 0
        while block := response.read(1024 * 1024):
            total += len(block)
            if total > MAX_TARBALL_BYTES:
                raise MapViewerAssetError("maps_viewer_package_too_large")
            digest.update(block)
            output.write(block)
    if digest.digest() != expected:
        raise MapViewerAssetError("maps_viewer_checksum_mismatch")


def _extract_assets(archive: Path, wanted: dict[str, str], output: Path):
    with tarfile.open(archive, mode="r:gz") as tar:
        for original, public in wanted.items():
            members = [m for m in tar.getmembers()
                       if m.name == "package/" + original and m.isfile()]
            if len(members) != 1 or members[0].size <= 0 or members[0].size > MAX_MEMBER_BYTES:
                raise MapViewerAssetError("maps_viewer_package_invalid")
            item = tar.extractfile(members[0])
            if item is None:
                raise MapViewerAssetError("maps_viewer_package_invalid")
            with item, (output / public).open("wb") as stream:
                remaining = members[0].size
                while remaining:
                    part = item.read(min(1024 * 1024, remaining))
                    if not part:
                        raise MapViewerAssetError("maps_viewer_package_invalid")
                    remaining -= len(part)
                    stream.write(part)


def _extract_package_notices(archive: Path, package: str, output: Path):
    """Preserve bounded, regular upstream license/notice files without executing them."""
    with tarfile.open(archive, mode="r:gz") as tar:
        selected = {}
        for member in tar.getmembers():
            parts = member.name.split("/")
            if len(parts) != 2 or parts[0] != "package":
                continue
            name = parts[1]
            if not re.fullmatch(r"(?:licen[cs]e|notice|copying|copyright)(?:[._-][A-Za-z0-9._-]+)?", name, re.I):
                continue
            key = name.lower()
            if key in selected or not member.isfile() or not 0 < member.size <= MAX_FONT_BYTES:
                raise MapViewerAssetError("maps_viewer_package_invalid")
            selected[key] = member
        if len(selected) > 32:
            raise MapViewerAssetError("maps_viewer_package_invalid")
        for key, member in selected.items():
            stream = tar.extractfile(member)
            if stream is None:
                raise MapViewerAssetError("maps_viewer_package_invalid")
            with stream:
                contents = stream.read(MAX_FONT_BYTES + 1)
            if len(contents) != member.size:
                raise MapViewerAssetError("maps_viewer_package_invalid")
            (output / (package + "-" + key)).write_bytes(contents)


def _publish_notices(root: Path, staging: Path):
    # The wheel includes upstream texts for installations already offline.
    directory = root / "licenses"
    if directory.is_symlink():
        raise MapViewerAssetError("maps_viewer_storage_invalid")
    directory.mkdir(exist_ok=True)
    bundled = Path(__file__).resolve().parent / "third_party_licenses"
    for source in sorted(bundled.glob("*.txt")):
        if source.is_file():
            (staging / source.name).write_bytes(source.read_bytes())
    for source in sorted(staging.iterdir()):
        if source.is_file():
            os.replace(source, directory / source.name)


def install(config) -> dict:
    """Explicit installer action. Existing good assets stay intact on failure."""
    with _lock:
        if ready(config):
            root = asset_directory(config)
            with tempfile.TemporaryDirectory(prefix=".maps-notices-", dir=root) as tmp:
                _publish_notices(root, Path(tmp))
            return status(config)
        root = asset_directory(config)
        if root.is_symlink():
            raise MapViewerAssetError("maps_viewer_storage_invalid")
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".maps-viewer-", dir=root) as tmp:
            staging = Path(tmp)
            notices = staging / "notices"
            notices.mkdir()
            for package, (version, wanted) in PACKAGES.items():
                info = _metadata(package, version)
                tarball = staging / (package + ".tgz")
                _download_package(info["url"], info["integrity"], tarball)
                _extract_assets(tarball, wanted, staging)
                _extract_package_notices(tarball, package, notices)
                tarball.unlink()
            _publish_notices(root, notices)
            hashes = {name: _sha256(staging / name) for name in ASSET_FILES}
            # Package assets may include a worker embedded in maplibre-gl.js;
            # no fonts, sprites or tiles are pulled from a CDN.
            for name in ASSET_FILES:
                os.replace(staging / name, root / name)
            output = {"packages": {key: value[0] for key, value in PACKAGES.items()},
                      "sha256": hashes}
            manifest = staging / MANIFEST
            manifest.write_text(json.dumps(output, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(manifest, root / MANIFEST)
        return status(config)


def status(config) -> dict:
    return {"installed": ready(config), "labels_installed": labels_ready(config),
            "packages": {name: version for name, (version, _) in PACKAGES.items()}}

def main():
    """Installer entry point; downloads once, then serves entirely offline."""
    import argparse
    from .config import load_config
    parser = argparse.ArgumentParser(description="Prepare PHAROS offline map viewer dependencies")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    config = load_config(args.config)
    info = install(config)
    print("PHAROS offline map viewer: " + ("ready" if info["installed"] else "unavailable"))
    if info["installed"]:
        try:
            if install_fonts(config):
                print("PHAROS offline map labels: ready")
        except (OSError, ValueError, TimeoutError, MapViewerAssetError) as exc:
            import logging
            logging.getLogger(__name__).warning("Offline map fonts unavailable: %s",
                                                 type(exc).__name__)
            print("PHAROS offline map labels: unavailable (map viewer remains usable)")


if __name__ == "__main__":
    main()
