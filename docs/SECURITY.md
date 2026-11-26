# Security — trusted LAN/VPN only

- No application authentication. PHAROS must not be routed directly from the public Internet. All LAN users with access to its HTTP endpoint can read KB data and perform configured actions.
- Mutating API routes require a custom same-origin request header and validate Origin when supplied. No permissive cross-origin CORS is configured. This is an anti-CSRF measure, **not authentication**.
- Tiiny credentials remain exclusively in restrictive `secrets.toml` storage and must not be committed to Git. Old password hashes are inert legacy configuration.
- Kiwix user paths never specify an arbitrary upstream host. PHAROS forwards read-only requests to its configured Kiwix server and rejects off-host redirects; proxy behavior and Kiwix-generated links still require real integration tests.
- Database, caches, documents, ZIMs, third-party executables and TOML secrets must remain outside source control. Backup all local SQLite/Qdrant storage before resetting the VM.
- Existing publisher Kiwix Tools validation uses HTTPS MD5 provided by the vendor and records local SHA-256. The MD5 is not a signature; avoid treating it as cryptographic supply-chain attestation.


### Ubuntu installer trust boundary (dev2)

Qdrant v1.18.3 is fetched from the official GitHub release asset. An independently pinned SHA-256 is checked; when the GitHub API supplies an asset digest, this must match as well. HTTPS downloads and a hash check are not equivalent to a signed software release. Kiwix Tools 3.8.2 continues to use the publisher's `.md5` over HTTPS (not a cryptographic signature). No source runs as root by default; any optional `sudo` to install `python3-venv` requires explicit consent. systemd-user `ExecStart` values contain only installer-computed fixed binary/config paths. The localhost-only Qdrant API must never be exposed directly to the LAN.


### Port 80 proxy (dev4)

NGINX HTTP is accessible to every system on reachable network interfaces; the installer does not configure DNS, router port-forwarding, TLS or host firewall rules. Deploy only on a trusted LAN/VPN, never publish port 80 to the Internet. No application login exists. On older TOMLs, PHAROS may also listen on `0.0.0.0:8080` until the operator changes the configured host/firewall; the proxy does not silently rewrite the TOML. NGINX installation/site management requires system authorization once, whereas user services run unprivileged.

Kiwix ZIMs may contain untrusted HTML and JavaScript. Even though the read-only Kiwix proxy permits only a fixed upstream and the application sets restrictive CSP and no permissive CORS, Kiwix is served at the same origin as PHAROS. Security review and integration testing with real ZIM content remain mandatory before use with untrusted libraries or exposed networks.

### Offline geographic indexes (0.7.0.dev12)

PBF sources are taken only from the official Geofabrik area directory and
restricted to HTTPS on `download.geofabrik.de`. Only a validated publisher
path is accepted; same-origin/CSRF protection applies to new index jobs.
Publisher HTTPS MD5 files verify transfer consistency, **not authenticity
or a signature**. Ingested coordinates/tags are treated as untrusted data
and rendered as textContent, never HTML. The SQLite database is derived
data published atomically after validation; geographically large PBFs
require disk/memory/processing review. No public geocoder API is exposed.

### Public release privacy gate (0.7.0.dev18)

Development discussions and local acceptance tests may use private addresses
or LAN-specific names. **Never copy these into bundled prompts, default
messages, test fixtures, example TOMLs, README files or release artefacts.**
Examples in current map modules, tests and documentation have been replaced
with synthetic street and locality names. The offline search index itself
contains normal public OSM data, not application-user preferences.

**Important: replacing tracked files does NOT delete strings from Git history.**
The current private development repository must not be made public without
a separate inspection of *all historical commits and refs*, secret-scanning,
a deliberate sanitized-history/release procedure, and user authorization.
This is not an instruction to rewrite or force-push the active private
repository. Check source files, settings, updater metadata, screenshots,
issues, tags, published archives and generated manuals before release.
Never commit live TOMLs, API keys or local IPs as examples.

Geographic Chat sends the user's own question to the **configured AI
provider** (which may be local Tiiny or a nonlocal OpenAI-compatible provider);
the geographic *lookup database* stays offline and sends no query to an
external geocoder. Do not claim end-to-end privacy when the user selects an
external model endpoint.
