# Uninstall / Disinstallazione

## English

Back up PHAROS before uninstalling. Finish or cancel running jobs. The scripts require the explicit phrase `REMOVE PHAROS`. They do not delete configuration, secrets, documents, SQLite/Qdrant indexes, ZIM, maps, GPX, waypoints, cache, source checkout or downloaded managed binaries. They do not uninstall Git, Python, Nginx or system packages.

### Ubuntu x86-64

As the original installation user, not root, review the official script and run:

```bash
setup=$(mktemp) && curl -fsSL https://raw.githubusercontent.com/mascalx/pharos/main/uninstall-pharos.sh -o "$setup" && bash "$setup"
```

The script checks the default installation, locks against concurrent installer activity, stops and disables the two PHAROS user services, removes their unit files and the dedicated virtualenv under ~/.local/share/pharos/venv. Nonstandard or symlinked installations require manual review. It leaves Nginx and login lingering untouched because they may be shared.

If the installer configured Nginx, inspect /etc/nginx/sites-available/pharos and /etc/nginx/sites-enabled/pharos. Only if the former has the PHAROS MANAGED NGINX VHOST marker and the latter is a symlink to that file, remove the dedicated symlink and site file with administrator approval. Validate with sudo nginx -t before reloading with sudo systemctl reload nginx. Keep a backup of both entries; restore them if validation fails. Do not remove other sites, uninstall Nginx, or automatically enable the distribution default site. Do not disable user lingering if other services need it.

### Windows x64

Stop PHAROS using Ctrl+C in its launcher. In PowerShell, review the official script and run:

```powershell
$setup = Join-Path $env:TEMP ('pharos-' + [guid]::NewGuid().ToString('N') + '.ps1'); Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/mascalx/pharos/main/uninstall-pharos.ps1' -OutFile $setup -ErrorAction Stop; & $setup
```

For a nonstandard installation root pass -InstallRoot with the exact path used at setup. The script refuses a running launcher or detectable remaining PHAROS processes, removes recognized current-user shortcuts and the dedicated virtualenv. It leaves source, configuration, logs and binaries for recovery/reinstallation. It does not terminate foreign processes.

### Optional complete removal

After backup and successful conservative uninstall, inspect the authoritative TOML to identify data_dir, database_dir and cache_dir. Remove only PHAROS-owned content from these locations manually after verifying their actual resolved paths and ownership. Never recursively delete a NAS mount root or a directory shared with other applications. Retain or remove the configuration directory (including secrets) and source checkout separately; confirm any local edits have been backed up. On Ubuntu the default state and cache are ~/.local/share/pharos and ~/.cache/pharos; configuration is ~/.config/pharos. On Windows the default root is %LOCALAPPDATA%\PHAROS. Custom paths can be elsewhere. No automatic full purge is provided.

To reinstall with data retained, use the same installation root and original TOML; ensure the original NAS volume is mounted and use the documented installer options if paths are nonstandard.

## Italiano

Prima della disinstallazione eseguire un backup, completare o annullare i job attivi. Gli script chiedono di digitare esattamente `REMOVE PHAROS`. Non cancellano configurazione, segreti, documenti, indici SQLite/Qdrant, ZIM, mappe, GPX, waypoint, cache, sorgenti o binari gestiti. Non disinstallano Git, Python, Nginx o pacchetti di sistema.

### Ubuntu x86-64

Con lo stesso utente dell'installazione e senza sudo, verificare lo script ufficiale ed eseguire:

```bash
setup=$(mktemp) && curl -fsSL https://raw.githubusercontent.com/mascalx/pharos/main/uninstall-pharos.sh -o "$setup" && bash "$setup"
```

Lo script verifica l'installazione predefinita, acquisisce il lock dell'installer, arresta e disabilita i due servizi utente PHAROS e rimuove le unità e la virtualenv dedicata. Percorsi non standard o collegamenti simbolici richiedono verifica manuale. Nginx e lingering rimangono invariati perché potrebbero essere condivisi.

Se il setup aveva configurato Nginx, verificare /etc/nginx/sites-available/pharos e /etc/nginx/sites-enabled/pharos. Solo se il primo contiene il marcatore PHAROS MANAGED NGINX VHOST e il secondo è un collegamento simbolico al primo, rimuovere esclusivamente questi due elementi con autorizzazione amministrativa. Conservare una copia, verificare con sudo nginx -t e soltanto dopo ricaricare con sudo systemctl reload nginx; ripristinare le copie se la verifica fallisce. Non modificare altri siti, non disinstallare Nginx e non riabilitare automaticamente il sito predefinito. Non disabilitare il lingering se serve ad altri servizi.

### Windows x64

Arrestare PHAROS con Ctrl+C nella finestra del launcher. Da PowerShell, verificare lo script ufficiale ed eseguire:

```powershell
$setup = Join-Path $env:TEMP ('pharos-' + [guid]::NewGuid().ToString('N') + '.ps1'); Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/mascalx/pharos/main/uninstall-pharos.ps1' -OutFile $setup -ErrorAction Stop; & $setup
```

Per un percorso personalizzato usare -InstallRoot con la radice esatta del setup. Lo script rifiuta launcher attivi o processi PHAROS residui rilevabili, rimuove i collegamenti riconosciuti dell'utente e la virtualenv dedicata. Conserva sorgenti, configurazione, log e binari. Non termina processi estranei.

### Rimozione completa facoltativa

Dopo backup e disinstallazione conservativa, leggere i percorsi data_dir, database_dir e cache_dir nel TOML effettivo. Rimuovere manualmente soltanto i contenuti appartenenti a PHAROS, dopo verifica dei percorsi risolti e della loro destinazione. Non cancellare ricorsivamente la radice di un mount NAS o cartelle condivise con altri applicativi. Configurazione (inclusi segreti) e sorgenti possono essere rimossi separatamente dopo aver salvato eventuali modifiche locali. Su Ubuntu i percorsi predefiniti sono ~/.local/share/pharos, ~/.cache/pharos e ~/.config/pharos; su Windows %LOCALAPPDATA%\PHAROS. I percorsi personalizzati possono essere altrove. Non è prevista una cancellazione completa automatica.

Per reinstallare mantenendo i dati usare la stessa radice e il TOML originale; montare il volume NAS originale e usare le opzioni documentate dell'installer se i percorsi non sono standard.
