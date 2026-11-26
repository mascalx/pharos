# PHAROS 0.8 — User Manual

English edition · 25 November 2026

Installation, configuration, everyday use and maintenance guide. The 0.8 designation identifies this manual edition; the actual installed software version is shown on Home and Diagnostics. This guide describes the 0.8.0 feature set qualified on Ubuntu. Windows remains experimental.

## 1. Introduction to PHAROS

PHAROS is a web platform for storing and consulting information, documents, encyclopedias and maps on your own infrastructure. Offline-first means that resources already downloaded can be used without Internet access; it does not mean that initial installation or every service is independent of the network. The PHAROS server performs operations and stores data, while a browser provides the interface. A mobile device can therefore use the server's resources without storing all the archives itself, provided it can reach the server through the local network or VPN.

### 1.1 Features

| Section | Purpose | Offline prerequisites |
|---|---|---|
| AI chat | Talk to a language model | Reachable local provider and available model |
| Knowledge Bases | Ask questions about documents and linked sources using RAG | Indexed documents, reachable Qdrant, embedding and chat models |
| Kiwix | Read ZIM libraries | Downloaded archive and working Kiwix service |
| Maps | View maps, find places, manage GPX and waypoints | Local PMTiles; local OSM indexes for search |
| Notifications | Monitor jobs and downloads | Core and local database |
| Diagnostics | Service health and updates | Local checks offline; GitHub for updates |
| Configuration | Application settings | Core and available data volume for operations requiring it |
| Translations | Interface languages | Local catalogs |

PHAROS is not a turn-by-turn navigation engine, a live weather service or cloud storage. It does not calculate road routes, driving times or traffic. The interface does not provide automatic OCR for scanned PDFs. AI answer quality depends on models and sources. Non-AI functions, such as reading a Kiwix archive or viewing a map, do not require a language model.

### 1.2 Quick start

If PHAROS has already been installed, open the address supplied by your administrator in a browser. From Home, open Diagnostics and check the data volume and services. To start a conversation, open AI chat, select a model and enter a question. To consult documents, open Knowledge Bases, activate RAG if required, create a KB and choose Add document; wait for indexing and select the KB in Chat. For an offline encyclopedia, open Kiwix, find an archive in the catalog, download it and open its title. For maps, download an area from Maps; include offline search data if you want to find addresses and places. Use Notifications to monitor background activities. Return to Home using the PHAROS logo at the top.

## 2. Requirements and planning

### 2.1 Platforms

The supported installer tested in the reference environment is Ubuntu Linux x86-64 with Python 3.11 or newer, Git and Python virtual environment support. Ubuntu may run on a PC or virtual machine. Windows x64 has an experimental native installer; it has not undergone the same qualification as Ubuntu. No qualified macOS installer is available. Docker is not required. Ubuntu services may require a user systemd session; system packages or the optional LAN proxy may require sudo with consent.

### 2.2 Sizing

There is no certified minimum hardware specification covering every workload. CPU, RAM and storage needs depend on the chosen archives and indexes; a separate AI device reduces inference load on the server. For an initial evaluation, use a few documents, a small ZIM and a limited map area. Before downloading large regions or a complete Wikipedia archive, measure available space, including temporary files, indexes and backups. Geographic indexing can take substantial resources and time. The browser displaying maps must support WebGL.

### 2.3 Storage and networking

Separate `data_dir` (original documents, ZIMs, maps and other persistent data) from `database_dir` (SQLite, Qdrant indexes, geographic indexes and operational files) and `cache_dir`. These directories must be separate and must not contain one another. A NAS can store bulky data; keep databases and indexes on a reliable local filesystem, not NFS. Prepare NAS mounts before installing, ensuring read and write access for the service user. Use `require_mount` and the volume identity marker to prevent writes into an empty mountpoint when the NAS is absent. A copy on the same NAS is not sufficient protection against NAS failure.

Allow Internet access during setup, content downloads and updates. Prepare all data and map viewer libraries before going offline. A local AI provider must be reachable from the PHAROS machine; a remote provider needs its own connectivity. Restrict server access to trusted devices on the LAN or VPN.

## 3. Installation

### 3.1 Fresh Ubuntu installation

Official public repository: https://github.com/mascalx/pharos . No account or manual clone required. Review the source, use a regular user and install curl if absent.

```bash
setup=$(mktemp) && curl -fsSL https://raw.githubusercontent.com/mascalx/pharos/main/install-pharos.sh -o "$setup" && bash "$setup" --language en
```

To configure Tiiny, replace the example address:

```bash
setup=$(mktemp) && curl -fsSL https://raw.githubusercontent.com/mascalx/pharos/main/install-pharos.sh -o "$setup" && bash "$setup" --language en --ai-url http://192.0.2.50
```

For an already mounted dedicated NAS:

```bash
setup=$(mktemp) && curl -fsSL https://raw.githubusercontent.com/mascalx/pharos/main/install-pharos.sh -o "$setup" && bash "$setup" --language en --data-dir /mnt/pharos-data --require-mount
```

Use a dedicated, initially empty directory, not shared with other applications.

The installer normally clones the source to `~/pharos`, prepares a separate virtual environment, installs PHAROS and its Python dependencies, and prepares Qdrant, Kiwix and offline map viewer resources. Qdrant and Kiwix are integrated into installation, not modules to purchase or install manually from Home. Large content archives are not downloaded automatically. Setup may request consent to configure a LAN proxy; read the final summary for the access address. If map viewer libraries could not be downloaded because of network problems, rerun the installer with Internet access.

### 3.2 Ubuntu installer options

| Option | Meaning |
|---|---|
| `--language it` / `en` | Initial language (English if omitted in the bootstrap) |
| `--ai-url URL` | AI provider base address |
| `--provider tiiny_direct` / `tiiny_gateway` / `openai_compatible` | Initial provider |
| `--embedding-model ID` | Records the initial embedding model; does not alone complete RAG activation |
| `--data-dir PATH` | Dedicated data directory |
| `--require-mount` | Requires mounted network storage |
| `--config PATH` | Non-standard TOML path |
| `--host 127.0.0.1` | Local listener; check the LAN proxy separately |
| `--port 8080` | Core port |
| `--skip-lan-proxy` | Do not configure the LAN proxy automatically |
| `--no-start` | Prepare without starting services, for advanced testing |

Fresh installation options are not a way to automatically overwrite an existing configuration. If a checkout already exists, the bootstrap invokes the conservative update procedure. Do not use `git reset --hard` to force an update.

### 3.3 Default Ubuntu locations

| Resource | Path |
|---|---|
| Source checkout | `~/pharos` |
| Configuration | `~/.config/pharos/config.toml` |
| Python environment | `~/.local/share/pharos/venv` |
| Data | `~/PHAROS-data` (or chosen path) |
| Databases | `~/.local/share/pharos/db` |
| Cache | `~/.cache/pharos` |
| Services | `pharos.service`, `pharos-qdrant.service` (user systemd) |

Secrets reside in the protected file specified by `security.secrets_file`, normally relative to the configuration directory. Do not edit installed virtual environment files directly. To inspect services and logs:

```bash
systemctl --user status pharos.service pharos-qdrant.service
journalctl --user -u pharos.service -n 100 --no-pager
journalctl --user -u pharos-qdrant.service -n 100 --no-pager
```

Open `http://127.0.0.1:8080/` on the server itself when using the default port. From other devices use the LAN address actually configured by setup, not the client's own `127.0.0.1`. Do not open public Internet ports. If a service fails to start, inspect logs and mounts before repeating setup.

### 3.4 Experimental Windows installation

Run this single command in PowerShell (no manual clone or GitHub account):

```powershell
$setup = Join-Path $env:TEMP ('pharos-setup-' + [guid]::NewGuid().ToString('N') + '.ps1'); Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/mascalx/pharos/main/install-pharos.ps1' -OutFile $setup -ErrorAction Stop; & $setup
```

Setup checks Git and 64-bit Python 3.11+ and offers to install missing prerequisites via winget. If winget is unavailable, use the official Git/Python installers. Review the official script before executing it. Respect your PowerShell execution policy.

With GitHub SSH already configured, use `-RepoUrl git@github.com:mascalx/pharos.git`. The installer normally creates `%LOCALAPPDATA%\PHAROS\source`, `venv`, `config` and `bin`, prepares Qdrant and Kiwix, and preserves an existing TOML. To start:

```powershell
& "$env:LOCALAPPDATA\PHAROS\source\scripts\start-windows.ps1"
```

Setup creates a current-user PHAROS shortcut in Start and on the desktop, using the existing PowerShell launcher. Use `-NoDesktopShortcut` to skip desktop shortcut creation (existing links are not removed). Click PHAROS to start, then keep the terminal open. Open `http://127.0.0.1:8080/`. The launcher does not create a persistent Windows service or automatically configure a LAN proxy. Ubuntu web updates are not supported on Windows. Use the local installer to update after backing up and stopping the running instance. Windows setup and complete map viewer preparation have not been qualified to the same extent as Ubuntu: use test data before trusting it with important information. If script execution is blocked, verify its provenance and the PowerShell policy with your administrator; do not indiscriminately disable protections.

## 4. Home and navigation

Home displays “Welcome to PHAROS”, tiles for eight functions and the installed version. Select a tile to open a section. The top logo returns to Home; the notification bar opens activities. The layout adapts to desktop and mobile displays. Map, document and Kiwix viewers have their own Close button. You do not need to keep the page open for a server-managed job to finish.

## 5. Configuration

Open Configuration. TOML is the persistent source of truth; the interface displays editable fields. After making changes, choose Save configuration. Compatible operational settings apply live; structural parameters may require a restart, indicated in the interface. Do not manually change paths on a populated system without a migration procedure and backup. Saving does not automatically move archives or indexes. Relative paths resolve against the TOML directory.

### 5.1 General, server and security

| TOML field | Meaning |
|---|---|
| `general.language` | UI language: Italian `it`, English `en` or an available custom language |
| `server.host` | Core listening address: loopback or network interface |
| `server.port` | Internal HTTP port, e.g. 8080 |
| `security.secrets_file` | Separate credentials file; not a web login password |

PHAROS does not provide multi-user authentication in this edition. The Security section does not enable a login. Anyone reaching the webapp can also use administrative functions; protect the server through networking and firewall rules. Coordinate address and port changes with the proxy and network rules.

### 5.2 Storage

| Field | Meaning |
|---|---|
| `storage.data_dir` | Original data and persistent archives; may be on a NAS |
| `storage.database_dir` | Databases, indexes and managed services; local filesystem |
| `storage.cache_dir` | Cache and supporting files |
| `storage.marker_id` | Volume identity, protected in the interface |
| `storage.require_marker` | Verify volume identity |
| `storage.require_mount` | Require mounted network storage where configured |

An unavailable data volume prevents operations requiring safe writes. Restore the correct mount before proceeding. Do not disable the marker to bypass the problem.

### 5.3 AI settings

| Field | Meaning / example value |
|---|---|
| `ai.enabled` | Enable AI provider |
| `ai.provider` | `tiiny_direct`, `tiiny_gateway` or `openai_compatible` |
| `ai.base_url` | Service URL, not the PHAROS browser URL |
| `ai.default_model` | Initially selected chat model ID |
| `ai.request_timeout_seconds` | Request timeout, e.g. 120 s |
| `ai.max_output_tokens` | Output limit, e.g. 1200 tokens |
| `ai.npu_policy` | `manual`, `protected` or `managed` |
| `ai.protected_models` | Comma-separated IDs reserved for protected model management |
| `ai.queue_limit` | Maximum FIFO queue size, e.g. 16 |
| `ai.npu_load_timeout_seconds` | Model loading timeout, e.g. 120 s |

The legacy `management_url` field is not used to contact TiinyOS directly when Gateway is selected and is hidden in the editor. The `manual` policy does not load models automatically; `protected` permits loading without automatic eviction of other models; `managed` does not yet imply a memory-aware automatic eviction algorithm. The queue shares the AI engine between requests and jobs; chat has priority over background ingestion. Increasing a timeout does not make inference faster.

### 5.4 Knowledge Base settings

| Field | Meaning / example value |
|---|---|
| `rag.enabled` | RAG enablement; use the KB activation procedure |
| `rag.embedding_model` | Embedding model ID; change through Knowledge Bases when active |
| `rag.qdrant_url` | Vector service URL, normally `http://127.0.0.1:6333` |
| `rag.chunk_chars` | Text fragment length in characters, e.g. 1100 |
| `rag.overlap_chars` | Fragment overlap, e.g. 150 |
| `rag.max_document_mb` | Per-file limit, default 12 MB, configurable from 1 to 40 |
| `rag.search_limit` | Result limit, default 5, configurable from 1 to 12 |

Changing chunk sizes does not automatically rebuild existing documents. Do not change Qdrant or the embedding model indiscriminately: indexes depend on their model and configuration. A chat model cannot substitute for an embedding model. The FTS5 text index is separate from the vector index.

### 5.5 Kiwix settings

`kiwix.server_url` identifies the Kiwix service reachable by Core, normally `http://127.0.0.1:8081`. `kiwix.browser_url` is an optional legacy/advanced setting; the integrated reader normally uses the PHAROS proxy, so there is no need to expose Kiwix on the LAN or configure the browser address. Retain the installer-generated values unless you have specific requirements. The managed service starts when valid archives exist.

## 6. Configuring AI providers

### 6.1 Tiiny Direct

Open Configuration, AI chat / provider settings. Enable AI, choose Tiiny Direct and enter the HTTP address of the Tiiny device without `/v1`, e.g. `http://192.0.2.50`. Enter the device's API key if required, then save. Refresh the model list, choose an available chat model and save. Check Diagnostics and try a simple question. The Tiiny catalog distinguishes installed models from those resident in NPU. Models not downloaded must be obtained through the device management tools; PHAROS does not replace the complete Tiiny catalog manager.

### 6.2 Tiiny Gateway

If an external Tiiny Gateway has already been installed, select Tiiny Gateway and enter its base URL, e.g. `http://192.0.2.60:9876`. Use the Gateway's API key; do not assume it is the same as the device's. PHAROS communicates with Gateway for inference and inventory; do not configure a second direct device route to bypass it. First confirm the Gateway works and is reachable from the PHAROS server.

### 6.3 OpenAI-compatible provider

Select OpenAI-compatible, enter the base URL expected by the compatible service and its key if required. The provider must expose a compatible API and the required models. Not all providers implement NPU inventory, thinking, embeddings or tool calling identically. General chat may work even if geographic tool calling does not. A remote provider may transmit questions and document excerpts outside the LAN, requires Internet access and may incur costs; review its terms and privacy before submitting confidential material.

### 6.4 API keys and provider switching

The previous key is not displayed. Use the New API key field and Set / replace when keeping the same destination. When changing provider or URL, enter the new key and save configuration together with the routing changes: PHAROS must not accidentally reuse the old key at the new destination. Leaving the key empty during a destination change may prompt for confirmation that the previous key will be cleared. Live switching is allowed only when the engine is idle; finish conversations, ingestion and reindexing before retrying. After changing provider, verify the catalog and both chat and embedding models. Existing indexes do not become compatible with a different embedding model merely because the provider changed.

## 7. AI chat

Open AI chat, select a model from the menu, enter a message and choose Send. The `*` symbol identifies the current selection according to the legend; `ᴺᴾᵁ` indicates residency in NPU when the provider supplies that information. Refresh rereads the catalog. When available, NPU load/unload buttons operate on the selected model. A model installed on disk is not necessarily ready to generate. Do not unload a model used by another job. If NPU memory is insufficient, reduce resident models or choose a smaller one.

### 7.1 Thinking and conversations

The Thinking selector appears when the model is recognized as compatible. Automatic leaves the decision to the provider; On or Off sends an explicit option. Not all models honor this control; compatibility errors require changing model or setting. Thinking may increase latency. Chat is kept in page memory, with a limited recent history, rather than a persistent conversation archive. New chat clears the context; reloading the page may lose it. Copy important answers before closing. Generated answers are not guaranteed to be correct.

### 7.2 Chat modes

- Normal chat: no KB selected and geographic search disabled. The model answers using its own capabilities.
- Document chat: select up to five KBs in the Knowledge Bases for this chat disclosure. PHAROS retrieves sources and supplies them to the model.
- Geographic chat: enable AI-assisted offline geographic search with no KB selected. The two modes cannot be combined in the same request.

Example questions: “Explain the difference between RAM and SSD” in normal chat; “How do I configure an AI provider according to the PHAROS manual?” with the manual's KB; “Where is the railway station in Ferrara?” with an appropriate geographic index and geographic mode enabled. Document and geographic answers have different prerequisites, described in the following chapters.

## 8. Knowledge Bases and RAG

RAG (Retrieval-Augmented Generation) retrieves relevant fragments from local content and uses them as answer context. It does not train the model, permanently teach it all documents or guarantee that every passage will be found. Qdrant stores vectors; the embedding model converts text into vectors; the chat model formulates an answer. Original documents remain available for verification.

### 8.1 Activate RAG

First configure and verify the AI provider and Qdrant. Open Knowledge Bases, refresh the embedding model list, select a compatible model and load it into NPU if necessary. Use the RAG activation button and confirm. Compatibility is verified by the system: presence in the catalog alone does not guarantee a valid embedding response. If activation fails, check credentials, model capabilities, NPU state and Qdrant. Do not use a generation-only model as the embedding model.

### 8.2 Create a Knowledge Base

In New Knowledge Base, enter a name and optional description, then create it. Use descriptive names such as “PHAROS manuals EN”, “Technical documentation” or “Household manuals”. KBs are separate collections; selecting them in Chat restricts retrieval to the chosen sources. Edit updates name and description; Delete requires confirmation and removes the collection and its documents/indexes. Do not use Delete to hide a KB. Linked ZIM archives are not separate copies to be deleted with the KB.

### 8.3 Add documents

1. In the KB card, choose Add document.
2. Select one text-based PDF, TXT or Markdown (`.md`) file. Upload begins automatically on selection. Cancelling the file picker starts nothing.
3. Wait for acquisition; subsequent indexing is a server-side background job. Monitor progress in the KB or Notifications.
4. Wait for indexed status before asking questions about the document. If indexing fails, check the message, size, extractable text and AI/Qdrant services.

The standard limit is 12 MB per document and can be changed in Configuration within supported bounds. Image-only PDFs require external OCR before import. Word, Excel, images and video are not direct ingestion formats in this procedure. Convert content to text-based PDF, TXT or Markdown. Add document handles one file selection at a time per KB, while its processing may continue in the background.

Click the document name to read it in the integrated viewer. Download retrieves the original; PDF preview also depends on browser capabilities. Markdown is shown as text in the reader. Delete removes the document from the KB and its derived data, after confirmation. The UI does not offer individual document renaming: choose an appropriate name before import. To replace a revision, import the new copy, verify it and remove the old one when no longer needed to avoid ambiguity in searches.

### 8.4 Ask document questions and check citations

Open AI chat, choose a chat model, expand Knowledge Bases for this chat and select up to five collections. Disable geographic search. Ask a specific question and include the product or procedure name when useful. For example: “According to the PHAROS manual, what does a full backup include?” Answers may contain references to documents and PDF pages. Open these references to verify the source; text files do not necessarily have page numbers. A citation alone does not prove every claim correct. If the system reports insufficient evidence, do not interpret it as proof that the information does not exist: rephrase, check selected sources and index status.

### 8.5 Text index and hybrid retrieval

In a KB card, below the text index status, choose the create or rebuild text index action and confirm. FTS5 indexes text from existing Qdrant chunks without computing new embeddings. Wait until the text index is ready. An index made stale by document changes should be rebuilt. The operation does not modify vectors or originals; a previous generation may remain available. It runs in the background. Do not create an FTS5 index of an entire ZIM: Kiwix sources use federated search.

Standard Chat uses default vector retrieval. Building the text index does not automatically select hybrid retrieval. The advanced APIs `POST /api/rag/search` and `POST /api/rag/answer` support `vector`, `candidate_fusion` and `hybrid_fts` through the `retrieval_mode` field; this is not a selector in standard Chat. Hybrid retrieval can help with exact identifiers and names, but does not guarantee better answers. If FTS5 is unavailable or invalid, hybrid mode may fall back to vector retrieval. The FTS5 index is local, with an operational limit of 300,000 chunks per KB; quality on languages not segmented by spaces has not been qualified.

### 8.6 Change the embedding model and reindex

The embedding model defines the vector space. To change it in an active KB system, use the Embedding model selector in Knowledge Bases, select the new model and choose Change model. Read the confirmation. PHAROS prepares new indexes in the background and attempts to switch only after verification; previous indexes are preserved during construction. Additional disk space, a functioning provider and time proportional to the documents are required. Do not edit TOML to bypass reindexing. Avoid source changes and provider switching during the operation. Check status and notifications; if interrupted, inspect the outcome and active index before retrying. Recovery from every real failure scenario is not guaranteed: retain a backup.

### 8.7 Link a ZIM to a KB

First download the archive in Kiwix. In the KB card, Kiwix archives section, choose Link Kiwix archive, select an existing ZIM and confirm. Alternatively, use the link-to-KB action from the Kiwix archive card and select the collection. PHAROS uses Kiwix search over linked sources; it does not automatically embed every Wikipedia article. Linking does not duplicate the archive. Select the KB in Chat to use its sources. Unlink removes only the association, not the ZIM file. Physical deletion is managed by Kiwix. If a source is unavailable, verify the service, file and link.

### 8.8 Import this manual into a Knowledge Base

Create a KB named “PHAROS manual EN” and import this English Markdown file. Wait for indexing, then select the KB in Chat. Use a separate collection for the Italian manual if you want to restrict answers to the selected language. Both languages need not be imported into the same collection. To update the manual, keep an external copy and replace the obsolete revision after verifying the new one. Examples: “How do I add a GPX track?”, “How does embedding model switching work?”, “Why is PHAROS blocking an update?” Markdown avoids PDF extraction problems, but retrieval remains subject to RAG limitations.

## 9. Kiwix offline library

Kiwix reads ZIM archives: local copies of websites and content collections. Archives can be read offline without an AI model. Content dates depend on the downloaded file version; they are not live updates of the original site.

### 9.1 Download archives

Open Kiwix and the official Kiwix catalog section. Search by title or topic, select language and category, then find archives. Check size, language and edition. Mini contains more concise entries; nopic favors articles without pictures; maxi includes pictures where supplied. Actual sizes and contents depend on the collection. Choose Download ZIM and confirm if requested. Multiple archives can be queued; ZIM downloads run sequentially. Monitor Notifications and NAS space. Manual URL download is an advanced function: use only official ZIM URLs accepted by PHAROS with their associated checksums, not arbitrary files or unofficial sites.

The catalog needs Internet access; PHAROS may display a cached copy when available. Downloads need connectivity and file verification. Partial files are not readable archives. The queue is persistent and the system attempts to resume after restart; the remote server and data integrity may require reacquisition. Do not manually remove partial files during active jobs.

### 9.2 Read and search archives

After the first valid download, the local Kiwix service starts automatically. In the offline library, click the archive title to open the integrated reader; use Kiwix navigation and search. Close returns to PHAROS. Search within available archives operates on ZIM content, not the Web. External links may require Internet access. Content and search indexes depend on the downloaded edition. If the service does not respond, check that at least one valid ZIM exists, the executable is installed and the data volume is available.

### 9.3 Delete or link an archive

The link-to-KB action lets you choose an existing KB, making the archive queryable through federated RAG. The card indicates linked collections. To remove only an association, use Unlink in the KB. Delete ZIM in Kiwix removes the physical file after confirmation; first check links and the impact on answers. Active downloads may prevent deletion. Do not delete the entire data directory to free space. Archives and content remain subject to their respective licenses.

## 10. Offline maps

PHAROS downloads vector PMTiles maps from a verified Protomaps source and uses the Geofabrik geographic catalog for area selection. MapLibre renders maps in the browser without an online mapping service once local resources are prepared. The visual map and geographic search database are separate resources.

### 10.1 Download an area

Open Maps and navigate to the geographic area catalog. Explore continent, country and region, or search for an area name. Use Explore for subareas. Choose map detail (maximum zoom) from the offered values. More detail usually means more data, time and storage. To find places, addresses or points of interest, select the option to include offline search data. Choose Download area and confirm prompts for large or already covered areas.

Monitor background preparation and Notifications. The system verifies the source and archive; if the source cannot be verified, downloads are suspended even if Internet access appears to work. Start with a small region. An area may already be covered by a larger map: read coverage and zoom warnings before downloading duplicates. More detailed archives are not necessarily redundant. For areas crossing the antimeridian, explore available subareas.

### 10.2 View a map

In the available areas section, click the archive name. Pan and zoom using the mouse or touch gestures. Street and place names depend on vector data, zoom level and label collision handling; not all appear simultaneously. PHAROS can use adjacent/compatible local map data when available; it does not assume worldwide coverage. Use Close to return to management. For WebGL errors, try a compatible browser/device. If local libraries are missing, rerun the Ubuntu installer with Internet access.

### 10.3 Delete and maintain maps

Delete in an archive card removes the selected map and its associated metadata after confirmation. This does not automatically mean deleting GPX tracks, waypoints or every search index; check resource availability separately. If a job for the area is active, wait or cancel it in a controlled manner. Before deleting a map, verify alternative coverage and zoom. Old archives without metadata may not be automatically comparable. Retain OpenStreetMap, Protomaps, MapLibre and PMTiles attribution.

## 11. Offline geographic search

### 11.1 Prepare geographic indexes

A PMTiles map alone does not provide name search. PHAROS uses verified Geofabrik OSM extracts imported into local SQLite, with FTS5 for text, RTree for location and OSM tags for categories. During download, select the option to include offline search data. If a map is already present, navigate to its catalog area and choose the search-data-only download action. Wait for download, import, index construction and verification. Temporary space and the local database can be very large. Do not put SQLite indexes on a NAS. V3 indexes are required for generic tag-based search; for an older index, use Rebuild index in the relevant area. An existing index remains available until verified publication of the new one. Rebuilding the index alone does not require downloading PMTiles again.

### 11.2 Manual search without AI

Open Maps → Offline geographic search, enter at least two characters (place, street, address or point of interest) and choose Search. Specify a locality to disambiguate identical names, e.g. “Via Cavour Ravenna” or “Ferrara station”. Results come only from installed indexes. Choose Show on map to open the map covering the result coordinates. If a house number is absent, an approximate street location may be shown with a warning; it is not verification of that house number. If no covering map is available, download the appropriate area. No results does not mean the place does not exist.

### 11.3 Search through AI chat

Open AI chat, choose a model compatible with native tool calling, deselect KBs and enable AI-assisted offline geographic search. Ask a specific geographic question including locality and category. Examples: “Where are the libraries in Ferrara?”, “Find pharmacies in Ravenna”, “Where is Via Cavour in Ravenna?”, “Which restaurants in Bologna are classified as pizzerias?” The model interprets the question and may invoke tools to find places, search POIs or discover tags available in the index. Result names and coordinates come from deterministic local queries, not coordinates invented by the model. The interpreted query helps you check how the question was understood. Show on map opens the result if map coverage is available.

Tool calling compatibility depends on the model and provider. Gemma 4 26B A4B instruct was successfully used in reference testing; it is not a universal requirement and other models must be verified. The system limits attempts and may return a controlled error if the model cannot interpret a request. Try simpler wording, a compatible model or manual search.

### 11.4 Geographic limitations

Category/locality search uses an approximate radius of 8 km from a locality point, not municipal boundaries. It may include neighboring places. Results may be incomplete or duplicate depending on OSM data and address granularity. Tag availability depends on the actual indexes; not every business or facility has a category or name. Live opening hours, service availability, commercial ratings, route calculation and road-network distances are not provided. Results do not replace official verification, especially for essential services. Deterministic search is offline; if the selected AI model is remote, its interpretation step is not.

## 12. GPX tracks and waypoints

### 12.1 Import and display GPX

Open Maps → GPX tracks and select a `.gpx` file containing a track or route. Import starts automatically. The limit is 5 MB. The file must contain valid geometry; a collection containing only waypoints is not a track. Wait for confirmation. Click the track title to view it on a covering map, if available. In the map, open the GPX tracks panel and select tracks to show or hide. The library displays information such as length, elevation gain when available and point count. These are derived from GPX, not a road routing calculation.

Rename changes the track name (maximum 120 characters). Delete removes the track after confirmation, not maps or waypoints. If no covering map exists, download the relevant region. PHAROS does not recalculate the track or generate navigation directions.

### 12.2 Create and manage waypoints

Open a map, press the Waypoint icon, then Add (plus). Tap the desired map location. Enter name (maximum 120 characters), description (maximum 2000), latitude and longitude; coordinates may be corrected in the form. Save. To edit a waypoint, use Manage (pencil) and select its marker. Change text or coordinates, or choose the action to select a new position on the map and tap the new point; save or cancel the move. Delete requires confirmation and removes only the selected waypoint. Done exits management mode. Coordinates must be valid: latitude -90 to 90, longitude -180 to 180.

To export waypoints, use Export waypoints as GPX in Maps → GPX tracks. Keep the file as a portable copy of the points. Waypoint export is not a full PHAROS backup.

## 13. Notifications and background jobs

The top bar indicates notifications; open it or select the Notifications tile. ZIM downloads, maps, geographic indexes, ingestion, text indexes and reindexing are server-managed jobs. Activities may be pending, running, completed, failed, cancelled or interrupted. Active jobs also appear in their relevant sections; the final notification reports the outcome. Select a notification to open its section and archive it where applicable. Archiving a notification does not remove downloaded content.

Cancel, when offered, requests job cancellation. Wait for the actual terminal state; do not power off the server assuming cancellation is immediate. Queue persistence enables recovery of several job types after restart; some activities restart from an earlier phase (text indexing may begin again from the first chunk), while others may be interrupted. Check status before starting a duplicate job. Closing the browser normally does not interrupt a server job already accepted. NAS, provider or network failures can prevent completion.

## 14. Diagnostics and updates

### 14.1 System status

Open Diagnostics and check PHAROS Core, local database, data volume and AI engine. Choose Refresh to repeat service checks. A reachable AI status does not guarantee that the selected model supports every feature. If the data volume is unavailable, resolve the mount before writing or updating. If the database does not respond, check the service and local filesystem. Use Ubuntu logs for more detailed errors; do not disclose credentials or private content.

### 14.2 Update from the Ubuntu web interface

In PHAROS updates, check Installed version, Available version and the last check time. Choose Check for updates to query the official repository. When an update exists, the GitHub changes link allows inspection of the diff; Install update starts the process directly without the old confirmation popup. Availability is checked against branch `main`, not a certified stable release catalog. A commit can change even when its version number remains the same. The remote version is read from verified commit metadata; if unavailable, an unknown value may appear. Git SHAs remain in internal use.

Before choosing Install update, ensure you have an independent backup and wait for jobs to finish. The server blocks updates when downloads, ingestion, reindexing, geographic/text indexing or incompatible requests are active or queued, showing the reason. Do not bypass the block. Finish jobs, check again if necessary and retry. During installation, Update in progress is displayed. The service may restart, causing brief unavailability; do not manually reboot the VM to accelerate the process. On completion, the page reloads and displays the actual installed version. If the update fails, PHAROS attempts its supported rollback; read error messages and verify services.

The web worker is designed for Ubuntu x86-64 installed with the expected services. An update requiring migrations, new third-party binaries or manual intervention may be refused. Automatic updating backs up configuration and SQLite, not a full copy of originals, Qdrant, ZIMs and PMTiles.

### 14.3 Manual Ubuntu update

With a verified backup, completed jobs, GitHub connectivity and a clean official checkout, run over SSH:

```bash
cd ~/pharos
bash scripts/update-ubuntu.sh
```

For a non-standard TOML, supply the correct `--config` parameter. The script uses a conservative fast-forward update. Do not force a merge or reset in the presence of local changes; preserve and assess customizations. If an update requires specific steps, follow its revision notes. On Windows, follow the experimental local installer procedure, not the Ubuntu worker.

## 15. Languages and translations

Select the language in Configuration → General → Language, then save configuration. Italian and English are built in and read-only. The Translations tile on Home opens catalog management. Select a language to inspect it, or New language to create one. Enter a code such as `fr`, `de` or `pt-BR` (two lowercase letters, optional uppercase region) and a readable name. Prepare the translation, search keys or English reference text, fill the desired values and save. The missing-entries filter supports partial translation. Blank or untranslated strings fall back to English; select the new language in Configuration to apply it.

Preserve placeholders such as `{name}`, `{count}`, `{done}`, `{total}` exactly, while reordering them if grammar requires. Dynamic values such as versions, counts and names should not be translated or duplicated in the catalog. Catalogs are JSON data, not HTML or JavaScript. Custom languages live in `storage.data_dir/locales/<code>.json`; Core updates do not overwrite them. If an old custom pack includes keys removed from the English reference, those keys may need to be removed before saving. Back up the file before editing JSON manually. Do not delete the active language: select another first. Built-in languages cannot be deleted.

Minimal custom catalog example:

```json
{
  "code": "fr",
  "name": "Français",
  "messages": {"homeWelcome": "Bienvenue dans PHAROS"}
}
```

## 16. Customization

On Home, choose Edit. Reorder tiles by dragging or using up/down arrows; use arrows on touch devices when dragging is inconvenient. Show/Hide controls visibility. Choose Save to persist preferences, Cancel to discard them, or Reset followed by Save to restore the default arrangement. Hiding a function does not uninstall it, stop services or delete data. Preferences are shared by the installation, not separate user accounts.

For advanced customization, PHAROS loads `custom.css` from the TOML directory, normally `~/.config/pharos/custom.css`. Create the file with CSS rules only, keep a copy and reload the browser; avoid hiding warnings, confirmation buttons or accessibility elements. Do not edit packaged stylesheets: updates may overwrite them. If customization makes the website unusable, rename your own `custom.css` and reload.

## 17. Backup and restore

### 17.1 Full Ubuntu backup

The update worker backup is not a full backup. The `scripts/pharos-backup.py` script copies configuration, secrets, databases, Qdrant, original data, ZIMs, PMTiles, cache, GPX, waypoints and operational files. It is a full, not incremental, copy. It requires an independent, writable destination with sufficient space, an existing parent directory and an unused final name. Finish jobs, stop services and substitute real paths for the example:

```bash
cd ~/pharos
systemctl --user stop pharos.service pharos-qdrant.service
python3 scripts/pharos-backup.py backup /backup/pharos-copy
python3 scripts/pharos-backup.py verify /backup/pharos-copy
systemctl --user start pharos-qdrant.service pharos.service
```

Verification checks the manifest with sizes and SHA-256 digests. If backup or verification fails, do not delete original data; diagnose the error and restore operations in a controlled manner. Protect backups because they include secrets and documents. If a NAS is not mounted, do not back up an empty mountpoint. Periodically verify restore capability on a separate machine as well.

### 17.2 Restore to an empty environment

The `restore-empty` command is intended for a separate recovery machine with the original absolute paths available and empty, and services stopped. Do not restore over a working production system. Prepare correct mounts and verify the backup:

```bash
cd ~/pharos
python3 scripts/pharos-backup.py verify /backup/pharos-copy
python3 scripts/pharos-backup.py restore-empty /backup/pharos-copy
```

The script refuses non-empty destinations. After restoration, reinstall/prepare runtimes and services using the documented procedure, first checking TOML and paths. Saved systemd files are references, not automatically reactivated services. If interrupted, the recovery environment may be partial; start again on empty destinations. Real restoration of large production indexes has not been qualified for every scenario. On Windows, arrange independent copies with processes stopped; do not assume Ubuntu systemd scripts work on Windows.

## 18. Security and data protection

PHAROS is designed for trusted LANs/VPNs, without individual user accounts or granular permissions. Do not directly expose the webapp, Qdrant, Kiwix or the AI provider to the Internet. Use firewalls, VPN, segmentation and server access controls. Protect secrets and backups with appropriate permissions. Do not place API keys in indexed documents. Remote providers receive content necessary for requests, including RAG fragments; verify the destination before handling private material. Confirmations and volume checks protect against some accidental errors, not as substitutes for authorization and backups.

Download software and archives from official sources. Do not modify SHAs, markers or integrity checks to bypass errors. Do not rely on AI answers, offline geographic data or encyclopedic information as the sole source for medical, legal, emergency or safety decisions. Respect software, document, ZIM and OpenStreetMap licenses when redistributing content. Local access does not automatically grant redistribution rights.

## 19. Troubleshooting

| Symptom | Checks and action |
|---|---|
| PHAROS unreachable | Check server address, VPN/LAN, port, proxy, `systemctl --user status pharos.service` and logs. Do not use localhost from another device. |
| Data volume unavailable | Check NAS mount, permissions and volume identity; restore before writing. |
| AI unreachable | Verify URL from the server, selected provider, key, network and service. An unresolved `.local` name may require DNS/mDNS correction or a stable IP. |
| No chat models | Refresh catalog, check AI enablement and installed models; verify credentials and provider compatibility. |
| Model not in NPU | Load using available controls or Tiiny manager; check NPU capacity and policy. |
| Slow answer or timeout | Try a smaller model, automatic/off thinking when supported, and check queue and timeout; avoid multiplying retries. |
| KB activation failed | Verify valid embedding model, Qdrant, volume and key. |
| PDF not indexed | Check selectable text, format and size limit; perform external OCR if required. |
| RAG cannot find an answer | Check selected KB, indexed documents, embedding model and wording; read sources. |
| Text index stale | Rebuild from the KB after finishing document changes. |
| Kiwix empty or stopped | Check valid ZIM and volume; the first complete archive enables service startup. |
| ZIM download failed | Check space, official URL, checksum, network and queue state; do not publish partial files. |
| Map cannot be displayed | Check local libraries, WebGL, PMTiles integrity and space; repeat setup if resources are missing. |
| Geographic search returns nothing | Check indexed area, locality, house number and OSM tags; try manual search; rebuild v3 index if needed. |
| Geographic chat failed | Deselect KBs, choose compatible tool-calling model, simplify question and inspect interpreted query. |
| GPX not visible | Check format, size, track geometry and covering map. |
| Update blocked | Read reason, wait for jobs, check volume, checkout and GitHub. |
| Update failed | Check rollback, actual version and logs; do not force a repository reset. |
| Custom language cannot be saved | Check code, current keys, placeholders and revision; reload editor after concurrent changes. |

## 20. Frequently asked questions

### Does PHAROS really work without Internet access?
Yes, for prepared and reachable local resources. Installation, new downloads and updates normally need Internet access; a remote AI provider remains network-dependent. Losing Internet access does not mean losing the LAN.

### Do I need to install Docker, Qdrant or Kiwix separately?
Not in the ordinary integrated Ubuntu procedure: setup prepares the expected dependencies. Check installer outcomes. Do not confuse integrated features with the old dynamically installable module architecture.

### Can I use PHAROS without Tiiny?
Yes. You can use non-AI features and, for chat/RAG, an alternative compatible provider with required models. NPU-specific features and tool calling may differ.

### Can I put everything on the NAS?
No, this is not recommended. Keep bulky data on NAS and I/O- and locking-sensitive databases and indexes on a local filesystem. Use mount and marker checks.

### Can I close the browser during a download?
Yes, once the server has accepted the job. Check Notifications when you return. Stopping the server is a different event, and resumption depends on job type.

### Does linking Wikipedia to a KB duplicate tens of gigabytes?
No. Linking uses the existing Kiwix archive and federated search; it does not compute embeddings for every article. Unlinking does not delete the file.

### How can I improve document answers?
Use clean text documents, clear names, relevant collections and an embedding model suited to the language. Verify citations and test questions. Changing the embedding model requires reindexing; FTS5 is separate and does not automatically activate hybrid retrieval in standard Chat.

### Why does a map display a street that search cannot find?
PMTiles display and the OSM search database are separate. Download and index search data for the area. Even an existing index may omit a specific name or house number.

### Can I find a pharmacy open right now or calculate a route?
No. Search finds POIs in local data, does not verify current opening hours and does not calculate road routes. Verify information with appropriate sources.

### Why is the Install update button missing?
Check GitHub availability, platform and installation type. Windows does not use the Ubuntu worker. Incompatible updates require a specific procedure.

### Is the automatic update backup sufficient?
No. Automatic rollback is limited; use an independent full backup to protect archives and indexes as well.

## 21. Glossary

- **Core:** main PHAROS application.
- **AI provider:** service performing inference and/or embedding.
- **LLM:** generative large language model.
- **NPU:** neural processing accelerator.
- **Embedding:** numeric text representation used for semantic search.
- **RAG:** source retrieval followed by contextual generation.
- **Chunk:** indexed text fragment.
- **Qdrant:** vector database.
- **SQLite:** local file-based database.
- **FTS5:** SQLite full-text index.
- **RTree:** spatial index.
- **ZIM:** offline archive readable with Kiwix.
- **PMTiles:** map tile archive format.
- **OSM:** OpenStreetMap, open geographic data.
- **PBF:** compact OSM extract format.
- **POI:** point of interest.
- **GPX:** exchange format for tracks, routes and waypoints.
- **Waypoint:** stored geographic point.
- **NAS/NFS:** network storage and access protocol.
- **TOML:** configuration file format.
- **FIFO:** first-in, first-out queue, with application priorities where implemented.
- **SHA-256:** digest used for integrity verification.
- **Rollback:** attempted restoration of previous state after failure.
- **Tool calling:** structured model request to an application function; geographic results come from the local database.

## Appendix A. Initial acceptance checklist

1. Verify mount and Diagnostics.
2. Test normal chat with a local model.
3. Create a test KB and import this manual; verify an answer and its source.
4. Download and open a small ZIM, then link it to a test KB.
5. Download a small PMTiles area with OSM index; try manual search and open the map.
6. Try a small GPX, create a test waypoint and export it.
7. Check Notifications and English/Italian language switching.
8. Make a full backup and verify its checksums.
9. Check updates only after jobs finish.
10. To test autonomy, disconnect Internet while retaining LAN, server and local provider; test ZIM, map and KB access. Restore connectivity before downloading new content.

## Appendix B. Technical references

Official repository: https://github.com/mascalx/pharos . See [README](../README.md), [architecture](ARCHITECTURE.md), [backup and restore](BACKUP_RESTORE_060.md), [updates](UPDATES.md), [security](SECURITY.md) and [uninstall](UNINSTALL.md). This manual describes the 0.8 feature set qualified on Ubuntu; Windows remains experimental. Full disaster recovery and every possible data/provider combination have not been acceptance tested.


## Uninstall

Review README prerequisites, stop PHAROS on Windows and finish jobs. These commands preserve configuration and data.

Ubuntu:

```bash
setup=$(mktemp) && curl -fsSL https://raw.githubusercontent.com/mascalx/pharos/main/uninstall-pharos.sh -o "$setup" && bash "$setup"
```

Windows:

```powershell
$setup = Join-Path $env:TEMP ('pharos-' + [guid]::NewGuid().ToString('N') + '.ps1'); Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/mascalx/pharos/main/uninstall-pharos.ps1' -OutFile $setup -ErrorAction Stop; & $setup
```

Type `REMOVE PHAROS`. See [Uninstall](UNINSTALL.md) for custom paths, Nginx and complete removal.


## Appendix — External AI gateway HTTP contract

Actual PHAROS 0.8.0 client contract, derived from `ai.py`, `tiiny_core.py`, `gateway_inventory.py`, `config.py` and `tools/orchestrator.py`. Independent gateways can implement it without the original Tiiny Gateway. Configure a bare HTTP(S) base URL without path, credentials or /v1 suffix. Configure the API key in the UI; it is stored separately in secrets.toml.

```toml
[ai]
enabled = true
provider = "tiiny_gateway"
base_url = "https://gateway.example.org:9876"
request_timeout_seconds = 120
max_output_tokens = 1200
```

All requests use `Accept: application/json`; when configured, `Authorization: Bearer <API_KEY>`; JSON POSTs use Content-Type application/json. In tiiny_gateway mode only inference requests (chat, tools, embeddings) include `X-Tiiny-Priority: 10` for interactive or `90` for background inference (0 = highest priority). The gateway owns scheduling and model loading. No direct TiinyOS or load/unload calls in this mode. PHAROS follows neither redirects nor environment proxies. Configurable timeout 5–900 seconds.

### GET /v1/models

Standard model list. No query parameters or body. PHAROS reads up to the first 100 entries of `data`, keeping nonempty string `id` values. Required for ordinary model discovery even if extended inventory is available.

**Example response (HTTP 200)**

```json
{
  "data": [
    {
      "id": "local/chat-model"
    },
    {
      "id": "local/embedding-model"
    }
  ]
}
```

### GET /gateway/v1/models

Extended Tiiny Gateway inventory, no parameters or body. Optional for basic inference, required for full installed-model discovery. `models` must be an array of objects (maximum 10,000); identifiers can be `id`, `model_id`, `model`, `fullname` or `name`, nonempty and at most 256 characters. `running` is optional and can contain IDs or objects using those identifier fields; a valid list is authoritative for residency, including an empty list. Otherwise PHAROS uses explicit boolean `loaded` or `in_npu`, or recognized status/state values. Installed states: downloaded, installed, stopped, unloaded, idle; running states: running, loaded, active, serving. Incomplete downloads and explicit `downloaded:false` are excluded. Unknown residency is not assumed loaded. Optional fields: `type`/`kind`, `capabilities` string array, numeric `npu_usage` 0–100, `desc`/`description`. On unavailable, rejected or invalid inventory, PHAROS falls back to `/v1/models` and marks catalog partial.

**Example response (HTTP 200)**

```json
{
  "models": [
    {
      "id": "local/chat-model",
      "status": "running",
      "type": "Text Generation",
      "npu_usage": 35
    },
    {
      "id": "local/embedding-model",
      "status": "downloaded",
      "type": "Text Embedding"
    }
  ],
  "running": [
    "local/chat-model"
  ]
}
```

### POST /v1/chat/completions

Normal chat and RAG answer generation. `model` is the exact ID, `messages` is the conversation array, `stream` is false, `max_tokens` is configured. `choices[0].message.content` must be a nonblank string. No temperature parameter is sent. When explicitly selected for eligible chat models, PHAROS adds `chat_template_kwargs: {"enable_thinking": true}` (or false); a gateway without support should reject the option rather than claim it applied.

**Request body**

```json
{
  "model": "local/chat-model",
  "messages": [
    {
      "role": "user",
      "content": "Hello"
    }
  ],
  "stream": false,
  "max_tokens": 1200
}
```

**Example response (HTTP 200)**

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "Hello!"
      },
      "finish_reason": "stop"
    }
  ]
}
```

### POST /v1/embeddings

Embedding requests send a nonempty array of nonempty strings. Return one record per input; integer `index` must cover 0 through N−1 exactly once (ordering of records may vary). Each vector must be nonempty, finite numeric values, identical dimension within the response, at most 8192 dimensions. Example vectors are deliberately short; real dimensions must remain consistent for the indexed model. PHAROS probes embeddings before accepting a new model.

**Request body**

```json
{
  "model": "local/embedding-model",
  "input": [
    "First text",
    "Second text"
  ]
}
```

**Example response (HTTP 200)**

```json
{
  "data": [
    {
      "index": 0,
      "embedding": [
        0.1,
        0.2,
        0.3
      ]
    },
    {
      "index": 1,
      "embedding": [
        0.4,
        0.5,
        0.6
      ]
    }
  ]
}
```

### POST /v1/chat/completions — tool calling

Native function calling for geographic search. The example function is illustrative, not an actual registered PHAROS function. The actual `tools` array contains schemas dynamically supplied by PHAROS; accept `tool_choice` auto or required. Return at most one call per response, `type:function`, unique nonempty `id` of at most 256 characters, registered function name (max 64), and `arguments` as a JSON-encoded object string, not an object (max 4096 UTF-8 bytes). PHAROS validates and executes tools locally; the gateway must not execute them. A subsequent request may contain an assistant message with `content:null` and `tool_calls`, followed by `role:tool`, `tool_call_id` matching the call ID, and JSON-string `content`. Accept those messages. A no-call response must not claim finish_reason tool_calls or length. Response limit 262144 bytes; PHAROS bounds orchestration to four inference rounds, six calls and at most 180 seconds. Optional thinking extension is identical to ordinary chat.

**Request body**

```json
{
  "model": "local/chat-model",
  "messages": [
    {
      "role": "user",
      "content": "Find a place"
    }
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "example_lookup",
        "description": "Illustrative schema only",
        "parameters": {
          "type": "object",
          "properties": {
            "name": {
              "type": "string"
            }
          },
          "required": [
            "name"
          ],
          "additionalProperties": false
        }
      }
    }
  ],
  "tool_choice": "auto",
  "stream": false,
  "max_tokens": 1200
}
```

**Example response (HTTP 200)**

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": null,
        "tool_calls": [
          {
            "id": "call_1",
            "type": "function",
            "function": {
              "name": "example_lookup",
              "arguments": "{\"name\":\"Example\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ]
}
```

### Errors and compatibility

Return valid JSON on success and appropriate HTTP error status codes; 401/403 signal authentication failure, 404 on extended inventory means unsupported endpoint. For chat with thinking, 400/422 explicitly reject the option. For tool calling, 400/404/405/415/422/501 signal rejected tool API; timeouts and other failures are handled separately. Do not return false success. For a generic OpenAI-compatible service choose `openai_compatible`: the three standard endpoints are used without extended inventory or priority header. Actual chat, embedding and tool support depends on model and gateway. Geographic lookup stays local to PHAROS, but the gateway receives prompts, messages, RAG excerpts and tool results when needed. Use HTTPS for untrusted connections and do not log credentials.
