# PHAROS 0.6.0 — backup e ripristino offline (Ubuntu)

Questa procedura è **separata** dal rollback di `scripts/update-ubuntu.sh`.
Il rollback dell'updater salva soltanto configurazioni e database SQLite: **non**
è una copia completa di Qdrant, documenti KB, file ZIM o mappe.

## Cosa contiene la copia completa

Lo script `scripts/pharos-backup.py` recupera i percorsi reali dal TOML
`~/.config/pharos/config.toml` e copia integralmente:

- Directory configurazione (TOML, segreti locali, `qdrant.yaml`, CSS personalizzati).
- `storage.database_dir`: tutti i file SQLite, indici e storage Qdrant locale,
  file operativi e metadati dei servizi; comprende waypoint, GPX e job.
- `storage.data_dir`: documenti originali KB, ZIM, PMTiles, metadati,
  liste scaricamenti e file parziali; **non cancella né esclude i dati NAS**.
- `storage.cache_dir`: cache gestita e altri file locali.
- Segreti posti fuori dalla directory configurazione, se configurati e presenti.
- Copia consultabile dei file user-systemd `pharos.service` e
  `pharos-qdrant.service` (non riattivati automaticamente durante il restore).

Nessun file viene modificato o eliminato dalle cartelle sorgenti. Lo script non
usa Internet, Docker o sudo. I dati vengono copiati in una cartella temporanea
e poi pubblicati con rinomina quando l'operazione e la verifica sono terminate.
Ogni file nel backup ha dimensione e SHA-256 nel `manifest.json`. Le cartelle
incompiute sono eliminate *solo* nella destinazione temporanea del backup.

**Spazio:** questo è un backup *completo*, non incrementale. Gli archivi ZIM e
PMTiles possono occupare centinaia di GB o più. Destinare la copia a un disco
o share indipendente e capiente, non alle directory PHAROS esistenti o a una loro
sottocartella. Una copia sullo stesso NAS non protegge dal guasto del NAS.

## Backup completo a servizi arrestati

Da SSH sull'Ubuntu dell'utente, dopo aver completato gli eventuali
download/reindicizzazioni:

```bash
cd ~/pharos
systemctl --user stop pharos.service pharos-qdrant.service
python3 scripts/pharos-backup.py backup /percorso-indipendente/pharos-0.6.0-backup
python3 scripts/pharos-backup.py verify /percorso-indipendente/pharos-0.6.0-backup
systemctl --user start pharos-qdrant.service pharos.service
```

Il percorso genitore `/percorso-indipendente` deve già esistere ed essere
scrivibile. Il nome `pharos-0.6.0-backup` **non** deve esistere prima del
backup: lo script rifiuta le sovrascritture. Sostituire il percorso d'esempio
con un percorso reale *esterno agli storage PHAROS*. Non eseguire il backup
durante attività in background; se lo script fallisce, controllare l'errore,
non eliminare i dati sorgente. Il riavvio va effettuato solo dopo un backup
completato oppure dopo aver diagnosticato un eventuale fallimento.

**NFS:** quando `storage.require_mount=true`, lo script verifica che
`storage.data_dir` ricada effettivamente sotto un filesystem di rete
riconosciuto (NFS/CIFS). Se lo share è assente interrompe l'operazione:
non costruirà una falsa copia dei file presenti sotto un mountpoint vuoto.

## Ripristino: solo macchina di recupero o percorsi vuoti

Non eseguire **mai** il ripristino sopra la VM di produzione funzionante.
La modalità `restore-empty` è deliberatamente conservativa: prima verifica
integralmente manifest e checksum, richiede i due servizi user-systemd
arrestati e **rifiuta qualunque destinazione non vuota**. Nessuna cancellazione
o sovrascrittura automatica è consentita. La ricostruzione avviene usando gli
**stessi percorsi assoluti** presenti nel backup, quindi una macchina di recupero
deve prepararli (incluso il mount NAS corretto) prima di lanciare lo script.

Su una VM di recupero *separata*, con percorsi originali disponibili e vuoti:

```bash
cd ~/pharos
python3 scripts/pharos-backup.py verify /backup/pharos-0.6.0-backup
python3 scripts/pharos-backup.py restore-empty /backup/pharos-0.6.0-backup
```

Non utilizzare `restore-empty` su un mount NFS non verificato. Per reinstallare
i file di servizio systemd, Qdrant e il runtime Python, utilizzare la procedura
Ubuntu documentata dopo avere controllato gli indirizzi/percorsi nel TOML.
I file user-systemd salvati in `services/` sono soltanto un riferimento.
Non usare un installer che possa rigenerare configurazione, alterare indici
o avviare componenti prima di avere verificato i percorsi del recupero.

**Limite importante:** il restore è destinato a percorsi vuoti, non è un
rollback transazionale su directory già popolate. In caso di interruzione
durante la copia, la destinazione di recupero potrebbe essere parziale:
ripartire su un nuovo ambiente vuoto e verificare gli SHA-256. Non è stato
ancora eseguito un ripristino reale su una seconda VM Ubuntu con un indice
Qdrant di produzione; i test automatici coprono soltanto dati sintetici.

## Test di qualificazione 0.6.0

Eseguire su un ambiente di sviluppo con Python, dipendenze PHAROS/pytest e
Node.js disponibili (non è necessario GitHub Actions):

```bash
cd ~/pharos
bash scripts/test-local.sh
```

Verificare in aggiunta: `/healthz`, avvio/riavvio Qdrant e PHAROS, Chat con
modello Tiiny predefinito, risposta KB con citazioni e PDF incorporato,
lettura ZIM/Kiwix, mappa regionale adiacente, GPX e waypoint persistenti,
rilevazione mount NAS e continuità dopo riavvio.

La suite automatica non simula le condizioni elettriche del NAS o la reale
interruzione di un download multi-GB. GitHub Actions resta manuale per
evitare consumi della quota mensile.
