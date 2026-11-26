"""Managed offline map-region extraction jobs.

This uses the official pinned go-pmtiles release, downloaded and verified on
demand. Source is a pinned official Protomaps basemap snapshot. Only user
selected bounded regions can be extracted. The jobs live in the existing
notifications SQLite database but use an independent worker from Kiwix.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

from .download_jobs import JobError
from .kiwix_runtime import _PublisherRedirects
from .maps import map_directory, _valid_pmtiles
from .maps_catalog import validate_source
from .maps_areas import get_area, AreaCatalogError
from .maps_coverage import metadata_for_archive
from .maps_search import (GeoSearchError, valid_source_url, fetch_osm_source,
                          build_index, index_file, list_indexes)
from .maps_coverage import valid_bbox
from .storage import check_data_volume

TOOL_VERSION = "1.31.2"
TOOL_RELEASE = "https://github.com/protomaps/go-pmtiles/releases/download/v1.31.2/"
# Protomaps documents this real build snapshot. It is deliberately pinned to
# avoid guessing which nightly exists. Replacing it requires an explicit review.
SOURCE_URL = ""  # Legacy constant retained for test imports only; never used for download.
ASSETS = {
    ("linux", "x86_64"): ("go-pmtiles_1.31.2_Linux_x86_64.tar.gz", "3ed7dbf4ec2e6dfe5e25b6f70d1ffc932729f93c86db353bf514dd71010a312f"),
    ("linux", "arm64"): ("go-pmtiles_1.31.2_Linux_arm64.tar.gz", "f8bd47e7ea866863489cad588fbaf2f31f42e5821f7a03f009b3769f05801cb1"),
    ("darwin", "x86_64"): ("go-pmtiles-1.31.2_Darwin_x86_64.zip", "1f0dc02eee6c58312dd6c509faee1b5c32f0596568af1bf51f1b034e7a88a65b"),
    ("darwin", "arm64"): ("go-pmtiles-1.31.2_Darwin_arm64.zip", "40528f7f616fcbf91207cd48c8fc023d213f6d86c0cbf1f748732803d1880f3d"),
    ("windows", "x86_64"): ("go-pmtiles_1.31.2_Windows_x86_64.zip", "a658baa4d7e55020aef6ca17bd9ff9faa1582671266b36f58c52db0ac8e785a1"),
    ("windows", "arm64"): ("go-pmtiles_1.31.2_Windows_arm64.zip", "8780a17453c63af757917a694cbbb50b943db89cc3f1b07e6fd62c1ff8e6963b"),
}
REGIONS = (
    {"id": "ravenna", "name": "Ravenna e dintorni", "bbox": [11.95, 44.17, 12.65, 44.65], "zoom": 14},
    {"id": "emilia-romagna", "name": "Emilia-Romagna", "bbox": [9.17, 43.73, 12.77, 45.14], "zoom": 12},
    {"id": "italy", "name": "Italia", "bbox": [6.55, 35.45, 18.60, 47.15], "zoom": 10},
    {"id": "europe", "name": "Europa", "bbox": [-12.0, 34.0, 34.0, 72.0], "zoom": 7},
)
MAX_TOOL_BYTES = 35 * 1024 * 1024
MAX_EXECUTABLE_BYTES = 128 * 1024 * 1024
MAX_OUTPUT_BYTES = 12 * 1024**3


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _platform_asset():
    arch = platform.machine().lower()
    arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(arch, arch)
    return ASSETS.get((platform.system().lower(), arch))


def _download_tool(url: str, target: Path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _PublisherRedirects())
    request = urllib.request.Request(url, headers={"User-Agent": "PHAROS-Maps/0.1"})
    with opener.open(request, timeout=45) as response, target.open("wb") as output:
        length = response.headers.get("Content-Length", "")
        if length.isdigit() and int(length) > MAX_TOOL_BYTES:
            raise JobError("maps_tool_too_large")
        total = 0
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_TOOL_BYTES:
                raise JobError("maps_tool_too_large")
            output.write(chunk)


def _extract_executable(archive: Path, destination: Path):
    binary = "pmtiles.exe" if platform.system().lower() == "windows" else "pmtiles"
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as package:
            matches = [entry for entry in package.infolist()
                       if Path(entry.filename).name == binary and not entry.is_dir()]
            if len(matches) != 1 or matches[0].file_size > MAX_EXECUTABLE_BYTES:
                raise JobError("maps_tool_archive_invalid")
            with package.open(matches[0]) as src, destination.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive, "r:gz") as package:
            matches = [member for member in package.getmembers()
                       if Path(member.name).name == binary and member.isfile()]
            if len(matches) != 1 or matches[0].size > MAX_EXECUTABLE_BYTES:
                raise JobError("maps_tool_archive_invalid")
            source = package.extractfile(matches[0])
            if source is None:
                raise JobError("maps_tool_archive_invalid")
            with source, destination.open("wb") as dst:
                shutil.copyfileobj(source, dst)
    if destination.stat().st_size == 0 or destination.stat().st_size > MAX_EXECUTABLE_BYTES:
        raise JobError("maps_tool_archive_invalid")
    if platform.system().lower() != "windows":
        destination.chmod(0o700)


def selected_region(region_id: str) -> dict:
    for region in REGIONS:
        if region["id"] == region_id:
            return dict(region)
    raise JobError("maps_region_unknown")


class MapJobs:
    """Durable map work queue, isolated from the existing Kiwix worker."""
    def __init__(self, config, jobs):
        self.config = config
        self.jobs = jobs
        self.tool_dir = config.storage.database_dir / "pharos-managed-services" / "maps"
        self.tool = self.tool_dir / ("pmtiles.exe" if platform.system().lower() == "windows" else "pmtiles")
        self.manifest = self.tool_dir / "install.json"
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._worker = None
        self._process = None
        self._cancel = set()
        # Jobs can have been running when PHAROS was forcibly terminated.
        with self.jobs._lock, self.jobs._db() as db:
            db.execute("""UPDATE jobs SET status='queued',detail='resuming_download',
                          updated_at=? WHERE kind IN ('download-map','index-map') AND status='running'""",
                       (int(time.time()),))

    def submit(self, region_id: str, source_id: str, zoom: int | None = None, include_search: bool = False):
        try:
            region = get_area(region_id)
        except AreaCatalogError as exc:
            raise JobError(str(exc)) from exc
        try:
            validate_source(source_id)
        except ValueError as exc:
            raise JobError(str(exc)) from exc
        pbf_url = None
        if include_search:
            # This is a user-selected, independent data download.
            from .maps_areas import areas as area_catalog
            cache = self.config.storage.database_dir / "maps-geography.json"
            match = next((a for a in area_catalog(cache_path=cache)["items"]
                          if a["id"] == region_id), None)
            pbf_url = valid_source_url(match.get("pbf_url") if match else None)
            if not pbf_url:
                raise JobError("maps_search_source_unavailable")
        base_zoom = region["zoom"]
        if zoom is None:
            zoom = base_zoom
        if type(zoom) is not int or not base_zoom <= zoom <= 15:
            raise JobError("maps_zoom_invalid")
        archive_id = region_id if zoom == base_zoom else region_id + "-z" + str(zoom)
        if not check_data_volume(self.config.storage).available:
            raise JobError("data_volume_unavailable")
        target = map_directory(self.config) / (archive_id + ".pmtiles")
        if include_search and index_file(self.config, region_id).is_file():
            raise JobError("maps_search_already_ready")
        with self.jobs._lock, self.jobs._db() as db:
            if target.exists():
                raise JobError("maps_region_exists")
            row = db.execute("""SELECT 1 FROM jobs WHERE kind='download-map' AND
                             label=? AND status IN ('queued','running') LIMIT 1""",
                             (archive_id,)).fetchone()
            if row:
                raise JobError("maps_region_already_queued")
            jobid = uuid.uuid4().hex
            now = int(time.time())
            db.execute("""INSERT INTO jobs (id,kind,status,label,params,created_at,updated_at)
                          VALUES (?,?,?,?,?,?,?)""",
                       (jobid, "download-map", "queued", archive_id,
                        json.dumps({"region": region_id, "source": source_id,
                                    "bbox": region["bbox"], "zoom": zoom,
                                    "name": region["name"], "include_search": include_search,
                                    "pbf_url": pbf_url}), now, now))
        self.resume()
        self._wake.set()
        return {"id": jobid, "status": "queued"}

    def submit_index(self, region_id: str, *, refresh: bool = False):
        """Queue initial or replacement index; keep old one until publish."""
        from .maps_areas import get_area as lookup_area
        try:
            area = lookup_area(region_id,
                cache_path=self.config.storage.database_dir / "maps-geography.json")
        except AreaCatalogError as exc:
            raise JobError(str(exc)) from exc
        pbf_url = valid_source_url(area.get("pbf_url"))
        if not pbf_url:
            raise JobError("maps_search_source_unavailable")
        if not check_data_volume(self.config.storage).available:
            raise JobError("data_volume_unavailable")
        existing = next((item for item in list_indexes(self.config)["items"]
                         if item["area"] == region_id), None)
        if index_file(self.config, region_id).exists() and not refresh:
            raise JobError("maps_search_already_ready")
        archive_dir = map_directory(self.config)
        if not archive_dir.is_dir() or not any(
                meta["id"].split("-z")[0] == region_id
                for meta in (metadata_for_archive(p)
                             for p in archive_dir.glob("*.pmtiles"))
                if meta is not None):
            # A known regional PMTiles is required for this dedicated action.
            # The normal combined workflow covers a region without a map.
            raise JobError("maps_search_map_missing")
        with self.jobs._lock, self.jobs._db() as db:
            rows = db.execute("""SELECT params FROM jobs WHERE
                kind IN ('download-map','index-map')
                AND status IN ('queued','running')""").fetchall()
            if any(__import__("json").loads(row["params"]).get("region") == region_id
                   for row in rows):
                raise JobError("maps_search_already_queued")
            jobid = uuid.uuid4().hex
            now = int(time.time())
            db.execute("""INSERT INTO jobs
                (id,kind,status,label,params,created_at,updated_at)
                VALUES (?, 'index-map', 'queued', ?, ?, ?, ?)""",
                (jobid, region_id, json.dumps({
                    "region": region_id, "bbox": area["bbox"], "zoom": area["zoom"],
                    "name": area["name"], "pbf_url": pbf_url,
                    "refresh_index": bool(refresh),
                    "base_index_created_at": (existing.get("created_at")
                                              if existing else None)}), now, now))
        self.resume()
        self._wake.set()
        return {"id": jobid, "status": "queued"}

    def resume(self):
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._stop.clear()
            self._worker = threading.Thread(target=self._loop, daemon=True,
                                             name="pharos-map-download-queue")
            self._worker.start()
            self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()
        with self._lock:
            if self._process and self._process.poll() is None:
                self._process.terminate()
            worker = self._worker
        if worker and worker.is_alive():
            worker.join(timeout=5)

    def cancel(self, jobid: str):
        with self.jobs._lock, self.jobs._db() as db:
            row = db.execute("SELECT kind,status FROM jobs WHERE id=?", (jobid,)).fetchone()
            if row is None or row["kind"] not in ("download-map", "index-map"):
                raise JobError("job_unknown")
            if row["status"] == "queued":
                self.jobs._change(jobid, status="cancelled", detail="job_cancelled")
                self._wake.set()
                return {"id": jobid, "status": "cancelled"}
            if row["status"] != "running":
                return {"id": jobid, "status": row["status"]}
        with self._lock:
            self._cancel.add(jobid)
            if self._process and self._process.poll() is None:
                self._process.terminate()
        return {"id": jobid, "status": "cancelling"}

    def _tool_ready(self):
        asset = _platform_asset()
        if asset is None:
            raise JobError("maps_platform_unsupported")
        if self.tool.is_symlink():
            raise JobError("maps_tool_untrusted")
        try:
            record = json.loads(self.manifest.read_text(encoding="utf-8"))
            if record.get("asset") == asset[0] and record.get("version") == TOOL_VERSION and self.tool.is_file() and _sha256(self.tool) == record.get("sha256"):
                return self.tool
        except (OSError, ValueError, TypeError):
            pass
        self.tool_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="pmtiles-", dir=self.tool_dir) as temp:
            archive = Path(temp) / asset[0]
            executable = Path(temp) / self.tool.name
            _download_tool(TOOL_RELEASE + asset[0], archive)
            if _sha256(archive) != asset[1]:
                raise JobError("maps_tool_checksum_mismatch")
            _extract_executable(archive, executable)
            digest = _sha256(executable)
            os.replace(executable, self.tool)
            temporary_manifest = Path(temp) / "install.json"
            temporary_manifest.write_text(json.dumps({"version": TOOL_VERSION, "asset": asset[0], "sha256": digest}), encoding="utf-8")
            os.replace(temporary_manifest, self.manifest)
        return self.tool

    def _loop(self):
        while not self._stop.is_set():
            with self.jobs._lock, self.jobs._db() as db:
                row = db.execute("""SELECT id,kind,label,params FROM jobs WHERE kind IN ('download-map','index-map')
                         AND status='queued' ORDER BY rowid LIMIT 1""").fetchone()
                if row:
                    db.execute("UPDATE jobs SET status='running',detail='',updated_at=? WHERE id=?",
                               (int(time.time()), row["id"]))
            if row is None:
                self._wake.wait(1)
                self._wake.clear()
                continue
            jobid = row["id"]
            try:
                params = json.loads(row["params"])
                # Existing queued dev15 jobs retain their legacy bbox. New jobs
                # store a frozen geographic extent at submission time.
                region = ({"id": row["label"], "bbox": params["bbox"],
                           "zoom": params["zoom"], "name": params.get("name", row["label"])}
                          if "bbox" in params and "zoom" in params
                          else selected_region(row["label"]))
                region["search_area_id"] = params.get("region", region["id"])
                region["include_search"] = bool(params.get("include_search"))
                region["index_only"] = row["kind"] == "index-map"
                region["refresh_index"] = bool(params.get("refresh_index"))
                region["base_index_created_at"] = params.get("base_index_created_at")
                region["pbf_url"] = params.get("pbf_url")
                self._run(jobid, region, params.get("source", ""))
            except Exception as exc:
                if self._stop.is_set():
                    self.jobs._change(jobid, status="queued", detail="resuming_download")
                elif jobid in self._cancel:
                    self.jobs._change(jobid, status="cancelled", detail="job_cancelled")
                else:
                    # Never put remote response bodies, local paths or secrets in notifications.
                    import logging
                    logging.getLogger(__name__).warning("Map job failed (%s): %s", type(exc).__name__, str(exc)[:1200])
                    safe = {"maps_source_unavailable", "maps_source_invalid",
                            "maps_source_connection_failed", "maps_extract_failed",
                            "maps_archive_invalid", "maps_archive_too_large",
                            "maps_tool_checksum_mismatch", "maps_tool_archive_invalid",
                            "maps_tool_too_large", "maps_tool_download_failed", "maps_platform_unsupported",
                            "data_volume_unavailable", "maps_storage_symlink_forbidden",
                            "maps_search_source_unavailable", "maps_search_download_failed",
                            "maps_search_checksum_unavailable", "maps_search_snapshot_unavailable",
                            "maps_search_checksum_mismatch",
                            "maps_search_source_too_large", "maps_search_parser_missing",
                            "maps_search_storage_unsafe", "maps_search_index_invalid",
                            "maps_search_cancelled", "maps_search_disk_space_low"}
                    code = str(exc) if isinstance(exc, (JobError, GeoSearchError)) and str(exc) in safe else "maps_download_failed"
                    self.jobs._change(jobid, status="failed", detail=code)
            finally:
                self._cancel.discard(jobid)

    def _run(self, jobid: str, region: dict, source_id: str):
        """One durable user operation, two independent publishable artifacts."""
        if region.get("index_only"):
            self._run_index(jobid, region)
            return
        if region.get("include_search"):
            target = map_directory(self.config) / (region["id"] + ".pmtiles")
            if target.exists():
                meta = metadata_for_archive(target)
                if (not meta or meta["id"] != region["id"] or
                        meta["bbox"] != region["bbox"] or
                        meta["source"] != source_id):
                    raise JobError("maps_region_exists")
            else:
                self._run_map(jobid, region, source_id, finish=False)
            self._run_index(jobid, region)
            return
        self._run_map(jobid, region, source_id, finish=True)

    def _run_index(self, jobid: str, region: dict):
        area_id = region.get("search_area_id", region["id"])
        existing = next((item for item in list_indexes(self.config)["items"]
                         if item["area"] == area_id), None)
        base_created = region.get("base_index_created_at")
        already_republished = (
            region.get("refresh_index") and type(base_created) is int
            and existing and existing.get("created_at", 0) > base_created)
        if existing and (not region.get("refresh_index") or already_republished):
            # On resume, skip only if THIS job already published a newer
            # verified generation. An explicit v3 rebuild must not be
            # silently skipped just because the index is already v3.
            self.jobs._change(jobid, status="completed", detail="",
                              progress=100, bytes_done=0, bytes_total=None)
            return
        if not valid_source_url(region.get("pbf_url")):
            raise GeoSearchError("maps_search_source_unavailable")
        def cancelled():
            return self._stop.is_set() or jobid in self._cancel
        self.jobs._change(jobid, detail="maps_search_downloading", progress=51,
                          bytes_done=0, bytes_total=None)
        def downloading(done, total):
            self.jobs._change(jobid, detail="maps_search_downloading",
                              bytes_done=done, bytes_total=total)
        pbf = fetch_osm_source(self.config, area_id, region["pbf_url"],
                               cancelled=cancelled, progress=downloading)
        self.jobs._change(jobid, detail="maps_search_importing", progress=55,
                          bytes_done=0, bytes_total=None)
        last_update = [0.0]
        last_stage = [""]
        def importing(count):
            reporting("maps_search_importing", count, None)
        def reporting(stage, done, total):
            now = time.monotonic()
            if stage != last_stage[0] or now - last_update[0] >= 2.0:
                # Progress is a *phase marker* only. The Map UI and
                # notifications keep their indeterminate progress bar.
                # bytes_done/total represent feature counts in these stages.
                values = {"maps_search_importing": 55,
                          "maps_search_building_fts": 75,
                          "maps_search_building_rtree": 89,
                          "maps_search_building_tags": 94,
                          "maps_search_verifying": 97}
                self.jobs._change(jobid, detail=stage,
                                  progress=values.get(stage, 55),
                                  bytes_done=int(done),
                                  bytes_total=int(total) if total is not None else None)
                last_update[0] = now
                last_stage[0] = stage
        build_index(self.config, area_id, region["bbox"], pbf,
                    cancelled=cancelled, progress=importing,
                    phase_progress=reporting)
        # PBF is a temporary source: keep only the indexed SQLite artifact.
        pbf.unlink(missing_ok=True)
        self.jobs._change(jobid, status="completed", progress=100,
                          detail="", bytes_done=0, bytes_total=None)

    def _run_map(self, jobid: str, region: dict, source_id: str, *, finish=True):
        if not check_data_volume(self.config.storage).available:
            raise JobError("data_volume_unavailable")
        self.jobs._change(jobid, detail="maps_preparing_tool", progress=1)
        try:
            tool = self._tool_ready()
        except JobError:
            raise
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise JobError("maps_tool_download_failed") from exc
        if self._stop.is_set() or jobid in self._cancel:
            raise JobError("maps_cancelled")
        directory = map_directory(self.config)
        if directory.is_symlink() or directory.parent.is_symlink():
            raise JobError("maps_storage_symlink_forbidden")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (region["id"] + ".pmtiles")
        partial = directory / ("." + region["id"] + "." + jobid + ".part")
        if target.exists():
            raise JobError("maps_region_exists")
        if not valid_bbox(region["bbox"]):
            raise JobError("maps_area_invalid")
        bbox = ",".join(str(point) for point in region["bbox"])
        # A job can wait in the FIFO queue while the latest build changes.
        # Revalidate the exact previously selected source before extraction.
        try:
            source_url = validate_source(source_id)
        except ValueError as exc:
            raise JobError(str(exc)) from exc
        args = [str(tool), "extract", source_url, str(partial),
                "--bbox=" + bbox, "--maxzoom=" + str(region["zoom"]),
                "--download-threads=4"]
        self.jobs._change(jobid, detail="maps_extracting", progress=5)
        try:
            # No shell; executable and source are pinned. Disable proxy env and
            # forward no credentials. Bounded output, silent captured stderr.
            env = {k: v for k, v in os.environ.items()
                   if k.lower() not in ("http_proxy", "https_proxy", "all_proxy",
                                        "no_proxy", "ftp_proxy")}
            with tempfile.TemporaryFile() as stderr_log:
                with self._lock:
                    self._process = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                                      stderr=stderr_log, env=env)
                    process = self._process
                while process.poll() is None:
                    if self._stop.is_set() or jobid in self._cancel:
                        process.terminate()
                    if partial.is_file():
                        size = partial.stat().st_size
                        if size > MAX_OUTPUT_BYTES:
                            process.terminate()
                            raise JobError("maps_archive_too_large")
                        self.jobs._change(jobid, bytes_done=size, detail="maps_extracting")
                    time.sleep(1)
                stderr_log.seek(0)
                stderr = stderr_log.read(16384).decode("utf-8", "replace").lower()
            if process.returncode != 0 or self._stop.is_set() or jobid in self._cancel:
                if "404" in stderr or "not found" in stderr:
                    raise JobError("maps_source_unavailable")
                if "timeout" in stderr or "connection reset" in stderr:
                    raise JobError("maps_source_connection_failed")
                # Preserve the precise CLI diagnostic in the local server journal,
                # not in API responses where upstream text may contain data.
                import logging
                logging.getLogger(__name__).error("PMTiles extraction exited %s: %s",
                    process.returncode, stderr[:1800].replace("\n", " "))
                raise JobError("maps_extract_failed")
            if not partial.is_file() or not _valid_pmtiles(partial):
                raise JobError("maps_archive_invalid")
            self.jobs._change(jobid, progress=95 if finish else 45, detail="maps_verifying")
            result = subprocess.run([str(tool), "verify", str(partial)],
                                    capture_output=True, timeout=120, env=env)
            if result.returncode != 0:
                raise JobError("maps_archive_invalid")
            if not check_data_volume(self.config.storage).available:
                raise JobError("data_volume_unavailable")
            metadata = {
                "schema": 1, "id": region["id"], "bbox": region["bbox"],
                "source": source_id, "requested_zoom": region["zoom"]
            }
            staged_meta = directory / ("." + region["id"] + "." + jobid + ".json.part")
            try:
                staged_meta.write_text(json.dumps(metadata, separators=(",", ":")), encoding="utf-8")
                os.replace(partial, target)
                os.replace(staged_meta, target.with_suffix(".json"))
            finally:
                staged_meta.unlink(missing_ok=True)
            self.jobs._change(jobid,
                              **({"status": "completed", "detail": "", "progress": 100}
                                 if finish else
                                 {"detail": "maps_search_downloading", "progress": 50}),
                              bytes_done=target.stat().st_size,
                              bytes_total=target.stat().st_size)
        finally:
            with self._lock:
                if self._process and self._process.poll() is None:
                    self._process.terminate()
                self._process = None
            partial.unlink(missing_ok=True)
