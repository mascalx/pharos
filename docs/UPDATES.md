# PHAROS 0.8 — Updates / Aggiornamenti

## English

PHAROS checks the official GitHub main branch. Internet access is required for update checks and installation, not for previously downloaded offline resources. A different commit does not necessarily mean a new numbered release.

On Ubuntu, open Diagnostics → Check for updates. Compatible updates can be installed from the interface. The independent updater checks the installed revision, checkout, background jobs, storage and update compatibility. A protected Core change needs an explicit migration-free compatibility manifest. Updates with incompatible history or requiring manual review are not forced. Back up configuration and storage before applying updates. Do not pull the operational checkout separately before using the web updater.

Manual alternative (default source location):

```bash
cd ~/pharos
bash scripts/update-ubuntu.sh
```

Supply the original --config path if nonstandard. Do not force a reset, merge or overwrite local changes. If an update fails, consult Diagnostics and the user service logs; rollback is best effort, not a substitute for a backup.

On Windows, stop PHAROS, back up the installation and rerun the public installer command in the [README](../README.md). The Ubuntu web updater is not supported on Windows.

## Italiano

PHAROS verifica il branch main ufficiale su GitHub. Internet è necessario per verificare e installare gli aggiornamenti, non per consultare le risorse già scaricate. Un commit diverso non implica necessariamente una nuova versione numerata.

Su Ubuntu aprire Diagnostica → Verifica aggiornamenti. Gli aggiornamenti compatibili possono essere installati dall'interfaccia. L'updater indipendente verifica revisione installata, checkout, job in corso, storage e compatibilità. Una modifica alle parti protette del Core richiede un manifest esplicito senza migrazioni. Non vengono forzati aggiornamenti con cronologia incompatibile o che richiedono verifica manuale. Eseguire il backup prima dell'aggiornamento; non fare git pull separatamente sul checkout operativo.

Alternativa manuale (percorso predefinito):

```bash
cd ~/pharos
bash scripts/update-ubuntu.sh
```

Usare il percorso --config originale se personalizzato. Non forzare reset o merge. In caso di errore consultare Diagnostica e i log systemd utente; il rollback non sostituisce un backup.

Su Windows arrestare PHAROS, effettuare il backup e ripetere il comando di installazione pubblico nel [README italiano](../README.it.md). Non usare l'updater web Ubuntu.

See also / Vedere anche: [backup and restore](BACKUP_RESTORE_060.md), [security](SECURITY.md), [user manual](USER_MANUAL_EN.md), [manuale utente](USER_MANUAL_IT.md).
