# Third-party notices / Avvisi di terze parti

PHAROS original code is licensed under Apache-2.0 (see [LICENSE](LICENSE)). This does not relicense third-party software, fonts, datasets, models or documents. The following inventory covers direct dependencies and externally downloaded components; it is not an exhaustive transitive-dependency software bill of materials (SBOM).

Il codice originale di PHAROS è distribuito con licenza Apache-2.0. Software, font, dati, modelli e documenti esterni mantengono le rispettive licenze. L'elenco non costituisce una SBOM completa delle dipendenze transitive.

## Python dependencies

These packages are installed by pip from their own distributions. Retain the license files and notices supplied by each installed distribution.

| Package | License | Upstream |
|---|---|---|
| FastAPI | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | BSD-3-Clause | https://github.com/encode/uvicorn |
| HTTPX | BSD-3-Clause | https://github.com/encode/httpx |
| pypdf | BSD-3-Clause | https://github.com/py-pdf/pypdf |
| python-multipart | Apache-2.0 | https://github.com/Kludex/python-multipart |
| tomlkit | MIT | https://github.com/python-poetry/tomlkit |
| osmium (PyOsmium) | BSD-2-Clause | https://github.com/osmcode/pyosmium |
| pytest (optional testing dependency) | MIT | https://github.com/pytest-dev/pytest |

Python, SQLite, system packages and transitive dependencies retain their own terms. Exact versions and notices depend on the installed environment.

## External applications and browser assets

| Component | Current pinned version | License / upstream |
|---|---|---|
| Qdrant | 1.18.3 | Apache-2.0 — https://github.com/qdrant/qdrant |
| Kiwix Tools / kiwix-serve | 3.8.2 Linux/macOS; 3.8.1 Windows x64 | GPL-3.0-or-later — https://github.com/kiwix/kiwix-tools |
| MapLibre GL JS | 6.16.0 | BSD-3-Clause — https://github.com/maplibre/maplibre-gl-js |
| PMTiles JS | 4.5.0 | BSD-3-Clause reference implementation — https://github.com/protomaps/PMTiles |
| Go PMTiles | 1.31.2 | BSD-3-Clause — https://github.com/protomaps/go-pmtiles |
| Noto Sans glyphs | Protomaps basemaps-assets revision 028c18f713baecad011301ff7a69acc39bcc2ae7 | SIL OFL 1.1 — https://github.com/protomaps/basemaps-assets and https://github.com/notofonts/latin-greek-cyrillic |

The PMTiles file-format specification is public domain/CC0 where applicable; individual tilesets have separate terms. Qdrant and Kiwix are separate executables. PHAROS downloads external applications and assets from upstream, rather than bundling their binaries in its source repository. Browser bundles may include additional third-party notices. Those notices must be retained. Do not redistribute a bundled binary, font, or offline installer without checking the relevant license, notice and source-code obligations.

## Geographic data and offline content

- OpenStreetMap data: © OpenStreetMap contributors, [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/). See https://www.openstreetmap.org/copyright for attribution and database obligations.
- Geofabrik extracts: https://download.geofabrik.de/ — derived from OpenStreetMap; retain appropriate attribution and comply with ODbL when publicly using or distributing derived databases.
- Protomaps basemaps: https://protomaps.com/ — source datasets and tileset terms remain applicable; see https://docs.protomaps.com/basemaps/attribution. PHAROS's software license does not grant rights in the map data.
- Kiwix ZIM archives: licenses differ by publication and content. Check the original archive's notices and source site before redistribution. Kiwix's software license does not cover all content in ZIM files.
- User documents, GPX files, AI models and generated outputs: their respective terms apply. PHAROS does not grant additional rights in them.

## Release compliance checklist

Before public release, confirm the exact upstream package notices (including transitive notices in browser bundles), preserve the required license texts with locally served assets, and verify the pinned font provenance and notices. Verify map attribution, any distributed third-party binaries, and the installed Python dependency inventory. This inventory is informational and does not claim that all redistribution obligations have already been fulfilled.

Prima della pubblicazione, verificare e conservare gli avvisi originali delle risorse cartografiche, le attribuzioni dei dati e le licenze delle dipendenze effettivamente installate.

## Local cartographic notices

The PHAROS package includes verbatim upstream notices in `pharos/third_party_licenses/`: MapLibre GL JS v6.16.0, Go PMTiles v1.31.2, Noto Sans OFL at the pinned basemaps-assets revision, and the PMTiles reference implementation license (upstream main, blob ec3d69a55220793cb4b2074ebe202df25f9cb417; not independently verified against the exact npm 4.5.0 tarball). The map viewer installer copies these into `storage.database_dir/pharos-managed-services/maps-viewer/licenses/` even when its existing assets are ready, without network access. New npm downloads also preserve regular root-level license/notice/copyright/copying files from the integrity-verified archives. Existing browser assets, fonts and map data are not replaced by the notice-only operation. Exact npm archive contents and all transitive bundled notices still require release verification; the presence of these files is not a complete SBOM or legal audit.

## 0.8 source-distribution review (2026-11-26)

Upstream project metadata and license statements were reviewed for the direct Python dependencies and pinned external applications. The listed SPDX licenses are consistent with those statements. This review is for the source-only PHAROS distribution, not a redistribution of a complete preinstalled environment. Kiwix is downloaded from its publisher and executed separately. Existing notice files and the install-time extraction of upstream browser package notices remain in place. The exact npm tarball notice contents and installed transitive dependency inventory were not independently downloaded in the audit environment; no complete SBOM or vulnerability clearance is asserted. Record actual installed package versions and notices from each qualified platform. Preserve all upstream license notices and map/content attribution. Do not distribute bundled third-party executables or datasets without a separate review.

### Installed Python environment review

The 0.8 RC8 Ubuntu qualification environment was audited with pip-audit 2.10.1: 30 external distributions checked with no known vulnerabilities reported and no skipped packages. All 31 distributions including PHAROS reported license metadata and license/notice files. Transitive licenses include MPL-2.0 (certifi), PSF-2.0 (typing_extensions), and Apache-2.0 OR BSD-2-Clause (packaging), alongside MIT, Apache and BSD licenses. Retain upstream notices. This is a point-in-time Python environment inventory, not a guarantee for future dependency resolutions or external executables and JavaScript bundles.
