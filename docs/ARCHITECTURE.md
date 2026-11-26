# PHAROS 0.8 — Architecture

PHAROS is an integrated Python/FastAPI application, not a runtime plug-in marketplace. The browser uses local HTTP endpoints; third-party executables and map viewer assets are installed from upstream sources. Initial setup and content acquisition require connectivity. Local functionality remains usable offline when its required resources and AI provider are available.

## Application components

- app.py and web/: HTTP API, browser UI, same-origin request guard, content security policies and bilingual catalogs.
- ai.py, providers.py, tiiny_core.py: configurable Tiiny Direct, Gateway and OpenAI-compatible providers, model selection and supported queue/load policies.
- knowledge/: document retrieval, Qdrant vector search, local FTS5 lexical search, citations and non-destructive index generation switching.
- kiwix/, kiwix_runtime.py, kiwix_proxy.py: catalog, library, managed server, read-only proxy and federated retrieval over linked ZIM sources.
- maps*, waypoints.py, tools/: offline PMTiles, OSM indexes, geographic lookup, GPX, waypoints and structured AI tool calls. No road routing.
- download_jobs.py and knowledge/lexical job adapters: persistent job records, bounded workflows and notification state.
- config.py, config_edit.py, secrets.py, home.py: authoritative TOML, separate secrets and presentation preferences. No application login.
- update_check.py, web_updater.py: revision detection and guarded Ubuntu update worker.

## Deployment

Ubuntu x86-64 uses dedicated per-user systemd units for Core and Qdrant. Core owns Kiwix. The optional Nginx LAN proxy uses a dedicated vhost; system package/configuration operations may require administrator approval. Windows x64 uses a foreground launcher and Windows Job Objects for process lifetime; its installation is experimental and does not create a persistent Windows service. The Windows setup and uninstaller have been exercised on real hardware, but not all features are qualified to the same extent as Ubuntu.

## Storage

Source, virtual environment, TOML/secrets, local databases, cache and user content have separate lifecycles. Keep SQLite/Qdrant/geographic indexes on local supported storage. A verified dedicated NAS volume can hold larger content. Reinstallation and conservative uninstall preserve configuration and data. Use volume identity and mount checks, backups and the original configuration when migrating.

## Security boundary

Trusted LAN/VPN only. The request guard and sandboxed Kiwix frame are defense-in-depth, not authentication. Do not expose PHAROS or its internal services directly to the public Internet. An external AI provider may receive queries and retrieved excerpts. See [Security](SECURITY.md) and the user manuals.

