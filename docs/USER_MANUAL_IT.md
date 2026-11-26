# PHAROS 0.8 — Manuale utente

Edizione italiana · 25 novembre 2026

Guida all'installazione, alla configurazione, all'uso quotidiano e alla manutenzione. La denominazione 0.8 identifica l'edizione del manuale; la versione effettivamente installata è quella mostrata nella Home e in Diagnostica. Il manuale descrive le funzionalità della versione 0.8.0 collaudate su Ubuntu. Windows resta sperimentale.

## 1. Introduzione a PHAROS

PHAROS è una piattaforma web per conservare e consultare informazioni, documenti, enciclopedie e cartografia su infrastruttura propria. Il principio offline-first significa che le risorse già scaricate possono essere utilizzate senza Internet, non che l'installazione iniziale o tutti i servizi siano indipendenti dalla rete. Il server PHAROS esegue le operazioni e conserva i dati; il browser è il terminale di consultazione. Un dispositivo mobile può quindi usare le risorse del server anche senza avere spazio per gli archivi, purché raggiunga il server tramite la rete locale o VPN.

### 1.1 Funzionalità

| Sezione | Scopo | Requisiti per l'uso offline |
|---|---|---|
| Chat IA | Conversare con un modello linguistico | Provider locale raggiungibile e modello disponibile |
| Knowledge Base | Interrogare documenti e fonti collegate con RAG | Documenti indicizzati, Qdrant, embedding e modello chat raggiungibili |
| Kiwix | Consultare biblioteche ZIM | Archivio scaricato e servizio Kiwix funzionante |
| Mappe | Visualizzare cartografia, cercare luoghi, gestire GPX e waypoint | PMTiles locali; indici OSM locali per la ricerca |
| Notifiche | Monitorare lavori e download | Core e database locale |
| Diagnostica | Stato dei servizi e aggiornamenti | Controlli locali offline; GitHub per aggiornamenti |
| Configurazione | Impostazioni applicative | Core e volume disponibile per le modifiche che lo richiedono |
| Traduzioni | Lingue dell'interfaccia | Cataloghi locali |

PHAROS non è un motore di navigazione turn-by-turn, un servizio meteo in tempo reale o un archivio cloud. Non calcola itinerari stradali, tempi di guida o traffico. L'interfaccia non offre OCR automatico dei PDF scansionati. La qualità delle risposte IA dipende dai modelli e dalle fonti. Le funzioni non IA, come leggere un archivio Kiwix o visualizzare una mappa, non richiedono un modello linguistico.

### 1.2 Guida rapida per iniziare

Se PHAROS è già installato, aprire nel browser l'indirizzo fornito dall'amministratore. Dalla Home aprire Diagnostica e controllare il volume dati e i servizi. Per conversare, aprire Chat IA, scegliere un modello e scrivere una domanda. Per consultare documenti, aprire Knowledge Base, attivare il RAG se necessario, creare una KB e scegliere Aggiungi documento; attendere l'indicizzazione e selezionare quella KB nella Chat. Per un'enciclopedia offline, aprire Kiwix, cercare un archivio nel catalogo, scaricarlo e aprirne il titolo. Per le mappe, scaricare un'area dalla sezione Mappe; includere anche i dati per la ricerca offline se si desidera cercare indirizzi e luoghi. Usare Notifiche per seguire le attività in background. Tornare alla Home premendo il logo PHAROS in alto.

## 2. Requisiti e pianificazione

### 2.1 Piattaforme

L'installer supportato e collaudato nell'ambiente di riferimento è Ubuntu Linux x86-64 con Python 3.11 o superiore, Git e supporto agli ambienti virtuali Python. Ubuntu può essere installato su un PC o una macchina virtuale. Windows x64 dispone di un installer nativo sperimentale: non ha ancora la stessa qualificazione di Ubuntu. Non è disponibile un installer macOS qualificato. Docker non è richiesto. I servizi di sistema Ubuntu possono richiedere una sessione utente systemd; eventuali pacchetti di sistema o proxy LAN possono richiedere sudo previa autorizzazione.

### 2.2 Dimensionamento

Non esiste un requisito hardware minimo certificato per tutti i carichi. CPU, RAM e spazio dipendono dagli archivi e dagli indici scelti; un dispositivo IA separato riduce il carico di inferenza sul server. Per un primo collaudo usare pochi documenti, uno ZIM piccolo e un'area cartografica limitata. Prima di archiviare grandi regioni o Wikipedia completa, misurare lo spazio disponibile, includendo file temporanei, indici e copie di sicurezza. Gli indici geografici possono richiedere molte risorse e tempi lunghi. Il browser che visualizza le mappe deve supportare WebGL.

### 2.3 Storage e rete

Separare `data_dir` (documenti originali, ZIM, mappe e altri dati persistenti) da `database_dir` (SQLite, indici Qdrant, indici geografici e file operativi) e `cache_dir`. Le tre directory devono essere distinte e non annidate fra loro. È possibile usare un NAS per i dati voluminosi; mantenere database e indici su filesystem locale affidabile, non NFS. Preparare il mount NAS prima dell'installazione, assicurando lettura e scrittura all'utente del servizio. Usare `require_mount` e l'identificatore del volume per impedire scritture nel mountpoint vuoto quando il NAS manca. Non usare la stessa copia NAS come unica protezione dal guasto del NAS.

Consentire Internet durante setup, download dei contenuti e aggiornamenti. Per uso offline preparare tutti i dati e le librerie cartografiche in anticipo. L'IA locale deve essere raggiungibile dalla macchina PHAROS; un provider remoto richiede la propria connettività. Limitare gli accessi al server a dispositivi fidati su LAN/VPN.

## 3. Installazione

### 3.1 Prima installazione Ubuntu

Repository ufficiale pubblico: https://github.com/mascalx/pharos . Non servono account o clone manuale. Verificare il sorgente, usare un utente normale e installare curl se assente.

```bash
setup=$(mktemp) && curl -fsSL https://raw.githubusercontent.com/mascalx/pharos/main/install-pharos.sh -o "$setup" && bash "$setup" --language it
```

Per configurare Tiiny, sostituire l'indirizzo di esempio:

```bash
setup=$(mktemp) && curl -fsSL https://raw.githubusercontent.com/mascalx/pharos/main/install-pharos.sh -o "$setup" && bash "$setup" --language it --ai-url http://192.0.2.50
```

Per un NAS dedicato già montato:

```bash
setup=$(mktemp) && curl -fsSL https://raw.githubusercontent.com/mascalx/pharos/main/install-pharos.sh -o "$setup" && bash "$setup" --language it --data-dir /mnt/pharos-data --require-mount
```

Usare una directory dedicata inizialmente vuota, non condivisa con altri applicativi.

L'installer clona normalmente i sorgenti in `~/pharos`, prepara un ambiente virtuale separato, installa PHAROS e le dipendenze Python, prepara Qdrant, Kiwix e le risorse cartografiche offline. Qdrant e Kiwix sono integrati nell'installazione, non moduli da acquistare o installare manualmente dalla Home. I contenuti di grandi dimensioni non vengono scaricati automaticamente. Il setup può chiedere il consenso per il proxy LAN; leggere il riepilogo finale per l'indirizzo di accesso. Se le librerie del visualizzatore mappe non sono state scaricate a causa della rete, ripetere l'installer con Internet disponibile.

### 3.2 Opzioni dell'installer Ubuntu

| Opzione | Significato |
|---|---|
| `--language it` / `en` | Lingua iniziale (inglese se omessa nel bootstrap) |
| `--ai-url URL` | Indirizzo base del provider IA |
| `--provider tiiny_direct` / `tiiny_gateway` / `openai_compatible` | Provider iniziale |
| `--embedding-model ID` | Registra il modello embedding iniziale, non completa da solo l'attivazione RAG |
| `--data-dir PATH` | Percorso dati dedicato |
| `--require-mount` | Richiede volume di rete montato |
| `--config PATH` | Percorso TOML non standard |
| `--host 127.0.0.1` | Listener locale; verificare separatamente il proxy LAN |
| `--port 8080` | Porta del Core |
| `--skip-lan-proxy` | Non configurare automaticamente il proxy LAN |
| `--no-start` | Preparare senza avviare i servizi, per test avanzati |

Le opzioni di prima installazione non sono un metodo per sovrascrivere automaticamente la configurazione esistente. Se il checkout esiste, il bootstrap richiama la procedura conservativa di aggiornamento. Non usare `git reset --hard` per forzare un aggiornamento.

### 3.3 Percorsi predefiniti Ubuntu

| Risorsa | Percorso |
|---|---|
| Sorgenti | `~/pharos` |
| Configurazione | `~/.config/pharos/config.toml` |
| Ambiente Python | `~/.local/share/pharos/venv` |
| Dati | `~/PHAROS-data` (oppure percorso scelto) |
| Database | `~/.local/share/pharos/db` |
| Cache | `~/.cache/pharos` |
| Servizi | `pharos.service`, `pharos-qdrant.service` (systemd utente) |

I segreti sono nel file protetto indicato da `security.secrets_file`, normalmente relativo alla cartella di configurazione. Non modificare direttamente i file dentro la virtualenv. Per controllare i servizi e i log:

```bash
systemctl --user status pharos.service pharos-qdrant.service
journalctl --user -u pharos.service -n 100 --no-pager
journalctl --user -u pharos-qdrant.service -n 100 --no-pager
```

Aprire `http://127.0.0.1:8080/` dalla macchina stessa se si usa la porta predefinita. Da altri dispositivi usare l'indirizzo LAN effettivamente configurato dal setup, non `127.0.0.1` del dispositivo client. Non aprire porte Internet pubbliche. Se il servizio non si avvia, esaminare i log e il mount prima di ripetere il setup.

### 3.4 Installazione Windows sperimentale

Eseguire questo unico comando in PowerShell (senza clone manuale né account GitHub):

```powershell
$setup = Join-Path $env:TEMP ('pharos-setup-' + [guid]::NewGuid().ToString('N') + '.ps1'); Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/mascalx/pharos/main/install-pharos.ps1' -OutFile $setup -ErrorAction Stop; & $setup
```

Il setup verifica Git e Python 3.11+ a 64 bit e propone di installare i prerequisiti mancanti tramite winget. Se winget non è disponibile, usare gli installer ufficiali Git/Python. Verificare il sorgente ufficiale prima di eseguirlo e rispettare la policy PowerShell.

L'installer crea normalmente `%LOCALAPPDATA%\PHAROS\source`, `venv`, `config` e `bin`, prepara Qdrant e Kiwix, mantenendo il TOML esistente. Per avviare:

```powershell
& "$env:LOCALAPPDATA\PHAROS\source\scripts\start-windows.ps1"
```

Il setup crea un collegamento PHAROS nel menu Start e sul desktop dell'utente, usando il launcher PowerShell esistente. Usare `-NoDesktopShortcut` per non creare il collegamento sul desktop (eventuali collegamenti esistenti non vengono rimossi). Avviare PHAROS dal collegamento e lasciare aperto il terminale. Aprire `http://127.0.0.1:8080/`. Il launcher non crea un servizio Windows persistente né configura automaticamente il proxy LAN. Gli aggiornamenti web Ubuntu non sono supportati su Windows. Usare l'installer locale per aggiornare dopo un backup e dopo aver arrestato l'istanza in esecuzione. La procedura Windows e la preparazione completa delle risorse cartografiche non sono qualificate come su Ubuntu: usare dati di prova prima di affidarle dati importanti. Se l'esecuzione dello script è bloccata, verificare provenienza e policy PowerShell con l'amministratore; non disattivare indiscriminatamente le protezioni.

## 4. Home e navigazione

La Home mostra «Benvenuto in PHAROS», i riquadri delle otto funzioni e la versione installata. Premere un riquadro per entrare nella sezione. Il logo superiore torna alla Home; la barra delle notifiche permette di aprire le attività. Il layout si adatta a desktop e smartphone. I visualizzatori di mappe, documenti e archivi Kiwix hanno il proprio pulsante Chiudi. Non è necessario tenere aperta la pagina per completare un lavoro gestito dal server.

## 5. Configurazione

Aprire Configurazione. Il TOML è il riferimento persistente; l'interfaccia mostra i campi modificabili. Dopo una modifica premere Salva configurazione. Le modifiche operative compatibili vengono applicate a caldo; i parametri strutturali possono richiedere un riavvio, indicato dall'interfaccia. Non cambiare manualmente i percorsi di un sistema già popolato senza una procedura di migrazione e un backup. Il salvataggio non sposta automaticamente gli archivi o gli indici. I percorsi relativi sono risolti rispetto alla directory del TOML.

### 5.1 Generale, server e sicurezza

| Campo TOML | Significato |
|---|---|
| `general.language` | Lingua UI; italiano `it`, inglese `en`, o lingua personalizzata disponibile |
| `server.host` | Indirizzo di ascolto del Core: loopback o interfaccia di rete |
| `server.port` | Porta HTTP interna, esempio 8080 |
| `security.secrets_file` | File separato delle credenziali; non è una password di accesso web |

PHAROS non offre autenticazione multiutente in questa edizione. La sezione Sicurezza non attiva un login. L'utente che raggiunge la webapp può utilizzare anche le funzioni amministrative; proteggere il server mediante rete e firewall. I cambi di porta e indirizzo devono essere coordinati con il proxy e le regole di rete.

### 5.2 Storage

| Campo | Significato |
|---|---|
| `storage.data_dir` | Dati originali e archivi persistenti; può essere su NAS |
| `storage.database_dir` | Database, indici e servizi gestiti; filesystem locale |
| `storage.cache_dir` | File di cache e supporto |
| `storage.marker_id` | Identità del volume, protetta nell'interfaccia |
| `storage.require_marker` | Verifica dell'identità del volume |
| `storage.require_mount` | Richiesta mount di rete dove configurato |

Un volume non disponibile impedisce le operazioni che richiedono scritture sicure. Ripristinare il mount corretto prima di procedere. Non disabilitare il marker per aggirare il problema.

### 5.3 Impostazioni IA

| Campo | Significato / valore di esempio |
|---|---|
| `ai.enabled` | Abilita il provider IA |
| `ai.provider` | `tiiny_direct`, `tiiny_gateway` o `openai_compatible` |
| `ai.base_url` | URL del servizio, non del browser PHAROS |
| `ai.default_model` | ID del modello chat selezionato inizialmente |
| `ai.request_timeout_seconds` | Timeout richieste; esempio 120 s |
| `ai.max_output_tokens` | Limite risposta; esempio 1200 token |
| `ai.npu_policy` | `manual`, `protected` o `managed` |
| `ai.protected_models` | ID separati da virgola, predisposti per gestione protetta |
| `ai.queue_limit` | Dimensione massima coda FIFO, esempio 16 |
| `ai.npu_load_timeout_seconds` | Timeout caricamento modello, esempio 120 s |

Il campo storico `management_url` non è usato per accedere direttamente a TiinyOS quando si seleziona il Gateway ed è nascosto nell'editor. La policy `manual` non effettua caricamento automatico; `protected` permette caricamento senza espulsione automatica di altri modelli; `managed` non implica ancora un algoritmo di espulsione automatica basato sulla memoria. La coda condivide il motore fra richieste e lavori; la chat ha priorità rispetto all'ingestione in background. Un timeout maggiore non rende più veloce un modello.

### 5.4 Impostazioni Knowledge Base

| Campo | Significato / valore di esempio |
|---|---|
| `rag.enabled` | Abilitazione RAG; usare la procedura di attivazione nella KB |
| `rag.embedding_model` | ID modello embedding; cambiare tramite Knowledge Base quando attiva |
| `rag.qdrant_url` | URL servizio vettoriale, normalmente `http://127.0.0.1:6333` |
| `rag.chunk_chars` | Dimensione frammenti in caratteri, esempio 1100 |
| `rag.overlap_chars` | Sovrapposizione frammenti, esempio 150 |
| `rag.max_document_mb` | Limite per file, predefinito 12 MB, configurabile da 1 a 40 |
| `rag.search_limit` | Limite risultati, predefinito 5, configurabile da 1 a 12 |

Cambiare dimensioni dei chunk non ricostruisce automaticamente i documenti esistenti. Non cambiare Qdrant o modello embedding per tentativi indiscriminati: gli indici dipendono dal modello e dalla configurazione. Un modello chat non sostituisce un modello embedding. L'indice testuale FTS5 è distinto dall'indice vettoriale.

### 5.5 Impostazioni Kiwix

`kiwix.server_url` indica il servizio Kiwix raggiungibile dal Core, normalmente `http://127.0.0.1:8081`. `kiwix.browser_url` è un parametro facoltativo storico/avanzato; il lettore integrato usa normalmente il proxy PHAROS, quindi non occorre esporre Kiwix sulla LAN o configurare l'indirizzo del browser. Mantenere i valori generati dall'installer salvo esigenze specifiche. Il servizio gestito viene avviato quando esistono archivi validi.

## 6. Configurazione dei provider IA

### 6.1 Tiiny Direct

Aprire Configurazione, sezione Chat IA / provider IA. Abilitare IA, scegliere Tiiny Direct e impostare l'indirizzo HTTP del dispositivo Tiiny, senza `/v1` come nell'esempio `http://192.0.2.50`. Inserire la chiave API richiesta dal proprio dispositivo, se necessaria; salvare. Aggiornare l'elenco modelli, scegliere un modello chat disponibile e salvare. Controllare Diagnostica e provare una domanda semplice. Il catalogo Tiiny distingue i modelli installati da quelli residenti in NPU. Un modello non scaricato va acquisito tramite gli strumenti di gestione del dispositivo; PHAROS non sostituisce il gestore completo del catalogo Tiiny.

### 6.2 Tiiny Gateway

Se si dispone di un Tiiny Gateway esterno già installato, selezionare Tiiny Gateway e indicare il suo URL base, ad esempio `http://192.0.2.60:9876`. Usare la chiave del Gateway, non presumere che sia la stessa del dispositivo Tiiny. PHAROS comunica con il Gateway per inferenza e inventario; non configurare un secondo accesso diretto al dispositivo per aggirarlo. Verificare prima che il Gateway funzioni e che il server PHAROS possa raggiungerlo.

### 6.3 Provider OpenAI-compatible

Selezionare OpenAI-compatible, inserire l'URL base previsto dal servizio compatibile e la relativa chiave, se richiesta. Il provider deve offrire un'API compatibile e i modelli necessari. Non tutti i provider implementano inventario NPU, thinking, embedding o tool calling nello stesso modo. La chat generale può funzionare anche quando la ricerca geografica tramite tool non funziona. Un provider remoto può trasmettere domande e frammenti documentali fuori dalla LAN, richiede Internet e può comportare costi; controllare condizioni e privacy prima di utilizzarlo con dati riservati.

### 6.4 Chiavi API e cambio provider

La chiave precedente non viene mostrata. Usare il campo Nuova chiave API e Imposta / sostituisci quando si mantiene la stessa destinazione. Se si cambia provider o URL, inserire la nuova chiave e salvare configurazione insieme alle modifiche: PHAROS non deve riutilizzare accidentalmente la vecchia chiave sulla nuova destinazione. Se si lascia vuota la chiave durante un cambio di destinazione può essere richiesta conferma della rimozione di quella precedente. Il cambio a caldo è consentito solo a motore inattivo; terminare le conversazioni, l'ingestione e le reindicizzazioni prima di riprovare. Dopo il cambio verificare il catalogo e i modelli chat ed embedding. Indici esistenti non diventano compatibili con un embedding diverso solo perché il provider è cambiato.

## 7. Chat IA

Aprire Chat IA, selezionare un modello dal menu, scrivere il messaggio e premere Invia. Il simbolo `*` identifica la selezione corrente secondo la legenda; `ᴺᴾᵁ` indica un modello residente in NPU quando il provider fornisce tale informazione. Aggiorna rilegge il catalogo. Se disponibili, i pulsanti di caricamento/scaricamento NPU agiscono sul modello selezionato. Un modello presente sul disco non è necessariamente pronto a generare. Non scaricare dalla NPU un modello usato da altri lavori. In caso di memoria insufficiente ridurre i modelli residenti o scegliere un modello più piccolo.

### 7.1 Thinking e conversazioni

Il selettore Thinking appare quando il modello è riconosciuto come compatibile. Automatico lascia la scelta al provider, Attivato o Disattivato invia l'opzione esplicita. Non tutti i modelli rispettano questo controllo; un errore di compatibilità richiede di cambiare modello o impostazione. Il thinking può aumentare la latenza. La chat è mantenuta nella memoria della pagina, con cronologia recente limitata, non è un archivio persistente delle conversazioni. Nuova chat svuota il contesto; ricaricare la pagina può perderlo. Copiare le risposte importanti prima di chiudere. Non esiste una garanzia di correttezza delle risposte generate.

### 7.2 Modalità della chat

- Chat normale: nessuna KB selezionata e ricerca geografica disattivata. Il modello risponde con le proprie capacità.
- Chat documentale: selezionare fino a cinque KB nella tendina Knowledge Base per questa chat. PHAROS recupera le fonti e le fornisce al modello.
- Chat geografica: attivare Ricerca geografica offline con IA, senza KB selezionate. Le due modalità non sono combinabili nella stessa richiesta.

Per domande di prova: «Spiega la differenza fra RAM e SSD» in chat normale; «Come si configura il provider IA secondo il manuale PHAROS?» con la KB del manuale; «Dove si trova la stazione ferroviaria di Ferrara?» con un indice geografico pertinente e modalità geografica attiva. Le risposte documentali e geografiche hanno requisiti differenti descritti nei capitoli successivi.

## 8. Knowledge Base e RAG

RAG (Retrieval-Augmented Generation) recupera frammenti pertinenti dai contenuti locali e li usa come contesto della risposta. Non addestra il modello, non gli fa memorizzare permanentemente tutti i documenti e non garantisce che trovi ogni passaggio. Qdrant conserva i vettori; il modello embedding trasforma testo in vettori; il modello chat formula la risposta. I documenti originali restano disponibili per la verifica.

### 8.1 Attivare il RAG

Configurare e verificare prima il provider IA e Qdrant. Aprire Knowledge Base, aggiornare l'elenco dei modelli embedding, scegliere un modello compatibile e, se necessario, caricarlo in NPU. Premere il pulsante di attivazione RAG e confermare. La compatibilità viene verificata dal sistema: la semplice presenza nel catalogo non garantisce una risposta embedding valida. Se l'attivazione fallisce, controllare credenziali, capacità del modello, stato NPU e Qdrant. Non selezionare un modello di sola generazione come embedding.

### 8.2 Creare una Knowledge Base

Nella sezione Nuova Knowledge Base inserire nome e, facoltativamente, descrizione; confermare la creazione. Usare nomi descrittivi, ad esempio «Manuali PHAROS IT», «Documentazione tecnica» o «Manuali domestici». Le KB sono raccolte separate; selezionarle in Chat restringe la ricerca alle fonti scelte. Il pulsante Modifica permette di aggiornare nome e descrizione; Elimina richiede conferma e rimuove la raccolta e i suoi documenti/indici. Non usare Elimina per nascondere una KB. Gli ZIM collegati non sono copie autonome da eliminare con la KB.

### 8.3 Aggiungere documenti

1. Nella scheda della KB premere Aggiungi documento.
2. Scegliere un solo file PDF testuale, TXT o Markdown (`.md`). Il caricamento parte automaticamente alla selezione. Annullare il selettore non avvia nulla.
3. Attendere l'acquisizione; la successiva indicizzazione è un lavoro server in background. Controllare il progresso nella KB o in Notifiche.
4. Attendere lo stato indicizzato prima di interrogare il documento. Se fallisce, controllare il messaggio, la dimensione, il testo estraibile e i servizi IA/Qdrant.

Il limite standard è 12 MB per documento e si può modificare in Configurazione entro i limiti supportati. Un PDF costituito solo da immagini deve essere sottoposto a OCR esterno prima dell'importazione. Word, Excel, immagini e video non sono formati di ingestione diretta di questa procedura. Convertire i contenuti in PDF testuale, TXT o Markdown. Il pulsante Aggiungi documento gestisce un file alla volta per KB, ma la sua elaborazione può proseguire in background.

Fare clic sul nome di un documento per leggerlo nel visualizzatore integrato. Il pulsante Scarica consente di ottenere l'originale; l'anteprima PDF dipende anche dalle capacità del browser. Il testo Markdown viene mostrato come contenuto testuale nel lettore. Elimina rimuove il documento dalla KB e i suoi dati derivati con conferma. La rinomina del singolo documento non è offerta nella UI: scegliere il nome appropriato prima dell'importazione. Per sostituire una revisione, acquisire la nuova copia, verificarla e rimuovere quella obsoleta quando non serve più, evitando ambiguità durante le ricerche.

### 8.4 Interrogare i documenti e verificare le citazioni

Aprire Chat IA, scegliere un modello chat, espandere Knowledge Base per questa chat e selezionare fino a cinque raccolte. Disattivare la ricerca geografica. Formulare una domanda specifica e includere il nome del prodotto o della procedura se utile. Ad esempio «Secondo il manuale PHAROS, quali dati salva il backup completo?». La risposta può includere riferimenti ai documenti e alle pagine PDF. Aprire i riferimenti per verificare la fonte; per file testuali non aspettarsi necessariamente un numero di pagina. Una fonte citata non prova da sola che ogni affermazione sia corretta. Se il sistema segnala evidenza insufficiente, non interpretarlo come prova che l'informazione non esista: riformulare, verificare le fonti selezionate e lo stato degli indici.

### 8.5 Indice testuale e ricerca ibrida

Nella scheda di una KB, sotto lo stato dell'indice testuale, premere Crea indice testuale o Ricostruisci indice testuale e confermare. FTS5 indicizza il testo dei chunk già presenti in Qdrant senza calcolare nuovi embedding. Attendere Indice testuale pronto. Un indice obsoleto dopo modifiche dei documenti va ricostruito. La costruzione non modifica i vettori o gli originali; la generazione precedente può restare disponibile. L'operazione è gestita in background. Non creare un indice FTS5 per l'intero contenuto ZIM: le fonti Kiwix usano la ricerca federata.

La Chat standard usa il recupero vettoriale predefinito. Costruire l'indice testuale non seleziona automaticamente una modalità ibrida. Le API avanzate `POST /api/rag/search` e `POST /api/rag/answer` supportano `vector`, `candidate_fusion` e `hybrid_fts` tramite il campo `retrieval_mode`; non è un selettore presente nella Chat standard. Il recupero ibrido può essere utile per sigle e nomi esatti, ma non garantisce una risposta migliore. Se FTS5 non è disponibile o valido, la modalità ibrida può ricadere sul vettoriale. L'indice FTS5 è locale, con limite operativo di 300.000 chunk per KB; la qualità su lingue non segmentate da spazi non è qualificata.

### 8.6 Cambiare modello embedding e reindicizzare

Il modello embedding definisce lo spazio vettoriale. Per cambiarlo in una KB già attiva, usare il selettore Modello di embedding nella sezione Knowledge Base, scegliere il nuovo modello e premere Cambia modello. Leggere la conferma. PHAROS prepara i nuovi indici in background e tenta il passaggio solo al termine delle verifiche; gli indici precedenti sono preservati durante la costruzione. Occorrono spazio aggiuntivo, un provider funzionante e tempo proporzionale ai documenti. Non modificare manualmente il TOML per saltare la reindicizzazione. Evitare modifiche alle fonti e cambi provider durante il lavoro. Verificare stato e notifiche; se si interrompe, controllare l'esito e l'indice attivo prima di riprovare. Il recupero dopo qualunque tipo di guasto reale non è garantito: conservare un backup.

### 8.7 Collegare uno ZIM a una KB

Scaricare prima l'archivio in Kiwix. Nella scheda KB, sezione Archivi Kiwix, premere Collega archivio Kiwix, selezionare uno ZIM già disponibile e confermare. In alternativa usare Collega alla KB dalla scheda dell'archivio Kiwix e scegliere la raccolta. PHAROS usa la ricerca Kiwix sulle fonti collegate; non crea automaticamente embedding per ogni articolo di Wikipedia. Il collegamento non duplica l'archivio. Selezionare la KB nella Chat per usarne le fonti. Scollega rimuove solo l'associazione, non il file ZIM. La cancellazione fisica è gestita da Kiwix. Se una fonte non è disponibile, verificare il servizio, il file e il collegamento.

### 8.8 Inserire questo manuale nella Knowledge Base

Creare una KB denominata «Manuale PHAROS IT» e importare il file Markdown italiano. Attendere l'indicizzazione, quindi selezionare la KB nella Chat. Usare una raccolta distinta per il manuale inglese, se si desidera limitare le risposte alla lingua scelta. Non è necessario importare entrambe le lingue nella stessa raccolta. Per aggiornare il manuale, conservare una copia esterna e sostituire la revisione obsoleta dopo aver verificato quella nuova. Esempi: «Come aggiungo una traccia GPX?», «Come funziona il cambio del modello embedding?», «Perché PHAROS blocca l'aggiornamento?». Il Markdown evita problemi di estrazione PDF, ma il retrieval resta soggetto ai limiti del RAG.

## 9. Biblioteca offline Kiwix

Kiwix legge archivi ZIM, cioè copie locali di siti e raccolte di contenuti. Gli archivi possono essere consultati offline senza modello IA. La data dei contenuti dipende dalla versione del file scaricato; non sono aggiornamenti in tempo reale del sito originale.

### 9.1 Scaricare archivi

Aprire Kiwix, sezione Catalogo Kiwix ufficiale. Cercare un titolo o argomento, selezionare lingua e categoria, premere Trova archivi. Controllare dimensione, lingua ed edizione. Mini contiene schede più sintetiche; nopic privilegia articoli senza immagini; maxi comprende immagini quando previste. Le dimensioni e i contenuti effettivi dipendono dalla specifica raccolta. Premere Scarica ZIM, confermare se richiesto. È possibile accodare più archivi; i download ZIM vengono gestiti in sequenza. Monitorare Notifiche e lo spazio del NAS. Il download manuale tramite URL è una funzione avanzata: usare solo URL ufficiali ZIM accettati da PHAROS con i relativi checksum, non file arbitrari o siti non ufficiali.

Il catalogo richiede Internet; quando disponibile, PHAROS può mostrare una copia in cache. Il download richiede connettività e verifica del file. I file parziali non sono archivi consultabili. La coda è persistente e il sistema tenta la ripresa dopo riavvio; il server remoto e l'integrità dei dati possono imporre una nuova acquisizione. Non eliminare manualmente file parziali durante attività in corso.

### 9.2 Leggere e cercare negli archivi

Dopo il primo download valido, il servizio Kiwix locale viene avviato automaticamente. Nella biblioteca offline fare clic sul titolo dell'archivio per aprire il lettore integrato; usare la navigazione e la ricerca di Kiwix. Chiudi torna a PHAROS. La ricerca negli archivi disponibili opera sui contenuti ZIM, non sul Web. Eventuali link esterni possono richiedere Internet. I contenuti e gli indici di ricerca dipendono dall'edizione scaricata. Se il servizio non risponde verificare che almeno uno ZIM valido sia presente, che il binario sia installato e che il volume dati sia disponibile.

### 9.3 Eliminare o collegare un archivio

Collega alla KB permette di scegliere una KB esistente, rendendo l'archivio interrogabile tramite RAG federato. Il riquadro indica le raccolte collegate. Per rimuovere solo una relazione usare Scollega nella KB. Elimina ZIM in Kiwix rimuove il file fisico con conferma; verificare prima eventuali collegamenti e l'impatto sulle risposte. Un download in corso può impedire la cancellazione. Non cancellare l'intera cartella dati per liberare spazio. Gli archivi e i contenuti rimangono soggetti alle rispettive licenze.

## 10. Mappe offline

PHAROS scarica cartografia vettoriale PMTiles da una sorgente Protomaps verificata e usa il catalogo geografico Geofabrik per selezionare le aree. MapLibre visualizza le mappe nel browser senza un servizio cartografico online una volta predisposte le risorse locali. La mappa visiva e il database di ricerca geografica sono due risorse distinte.

### 10.1 Scaricare un'area

Aprire Mappe e raggiungere Catalogo aree geografiche. Esplorare continente, Paese e regione oppure cercare il nome di un'area. Usare Esplora per le sottoaree. Scegliere il dettaglio cartografico (zoom massimo) fra i valori offerti. Più dettaglio significa normalmente più dati, tempo e spazio. Se si desidera cercare località, indirizzi o punti d'interesse, selezionare Includi anche i dati per la ricerca offline. Premere Scarica area e confermare le richieste per aree grandi o già coperte.

Seguire la preparazione in background e Notifiche. Il sistema verifica la sorgente e l'archivio; se la sorgente non è verificabile sospende il download, anche se Internet sembra funzionare. Iniziare da una regione piccola. Un'area può essere già coperta da una mappa più grande: leggere gli avvisi di copertura e zoom prima di scaricare duplicati. Gli archivi più dettagliati non sono necessariamente ridondanti. Per aree che attraversano l'antimeridiano esplorare le sottoaree disponibili.

### 10.2 Visualizzare una mappa

Nella sezione Aree disponibili fare clic sul nome dell'archivio. Usare trascinamento e zoom con mouse o gesto touch. I nomi di vie e luoghi dipendono dai dati vettoriali, dal livello di zoom e dalla gestione delle sovrapposizioni delle etichette; non tutti sono visibili contemporaneamente. PHAROS può utilizzare cartografia locale adiacente/compatibile quando disponibile; non presume di avere copertura del mondo intero. Usare Chiudi per tornare alla gestione. Se appare un errore WebGL provare un browser/dispositivo compatibile. Se mancano le librerie locali, ripetere l'installer Ubuntu con Internet.

### 10.3 Eliminazione e manutenzione delle mappe

Elimina nella scheda dell'archivio rimuove la cartografia selezionata e i relativi metadati con conferma. Non significa automaticamente eliminare GPX, waypoint o tutti gli indici di ricerca; controllare separatamente la disponibilità delle risorse. Se esiste un lavoro attivo per l'area, attendere o annullarlo in modo controllato. Prima di eliminare una mappa verificare copertura alternativa e zoom. Gli archivi vecchi senza metadati possono non essere confrontabili automaticamente. Conservare le attribuzioni OpenStreetMap, Protomaps, MapLibre e PMTiles.

## 11. Ricerca geografica offline

### 11.1 Preparare gli indici geografici

Una mappa PMTiles da sola non consente la ricerca per nome. PHAROS usa estratti OSM Geofabrik, verificati e importati in SQLite locale, con FTS5 per testo, RTree per posizione e tag OSM per le categorie. Durante il download selezionare Includi anche i dati per la ricerca offline. Se la cartografia è già presente, raggiungere l'area nel catalogo e scegliere Scarica solo dati ricerca. Attendere download, importazione, costruzione indici e verifica. Lo spazio temporaneo e il database locale possono essere molto grandi. Non collocare gli indici SQLite sul NAS. Gli indici v3 sono necessari per la ricerca generica tramite tag; per un indice precedente usare Ricostruisci indice nell'area pertinente. Il vecchio indice resta disponibile fino alla pubblicazione verificata del nuovo, se esistente. Non serve riscaricare il PMTiles solo per ricostruire l'indice.

### 11.2 Ricerca manuale senza IA

Aprire Mappe → Ricerca geografica offline, digitare almeno due caratteri (località, strada, indirizzo o punto d'interesse) e premere Cerca. Usare una località esplicita per disambiguare nomi uguali: per esempio «Via Cavour Ravenna» o «Stazione Ferrara». I risultati provengono soltanto dagli indici installati. Premere Mostra sulla mappa per aprire la cartografia che copre le coordinate del risultato. Se il civico non è presente nei dati, può essere mostrata la posizione approssimativa della strada con un avviso; non è una verifica del numero civico. Se manca la mappa di copertura, scaricare l'area appropriata. Nessun risultato non significa che il luogo non esista.

### 11.3 Ricerca tramite Chat IA

Aprire Chat IA, selezionare un modello compatibile con tool calling nativo, deselezionare le KB e attivare Ricerca geografica offline con IA. Formulare una domanda geografica precisa, indicando località e categoria. Esempi: «Dove sono le biblioteche di Ferrara?», «Trova farmacie a Ravenna», «Dove si trova Via Cavour a Ravenna?», «Quali ristoranti di Bologna sono classificati come pizzerie?». Il modello interpreta la domanda e può invocare strumenti per trovare luoghi, cercare POI o scoprire i tag presenti nell'indice. I nomi e le coordinate dei risultati provengono da ricerche deterministiche locali, non da coordinate inventate dal modello. La query interpretata consente di controllare come è stata compresa la domanda. Mostra sulla mappa apre il risultato se la cartografia è disponibile.

La compatibilità tool calling dipende dal modello e dal provider. Gemma 4 26B A4B instruct è stato usato con successo nel collaudo di riferimento; non è un requisito universale e altri modelli devono essere verificati. Il sistema limita i tentativi e può restituire un errore controllato se il modello non interpreta la richiesta. In tal caso provare una formulazione più semplice, un modello compatibile o la ricerca manuale.

### 11.4 Limiti geografici

La ricerca per categoria/località usa una prossimità indicativa di circa 8 km da un punto di località, non i confini comunali. Può includere luoghi limitrofi. I risultati possono essere incompleti o duplicati in base ai dati OSM e alla granularità degli indirizzi. La disponibilità di tag dipende dagli indici effettivi; non tutte le attività hanno classificazione o nome. Non sono disponibili orari in tempo reale, disponibilità dei servizi, valutazioni commerciali, calcolo di itinerari o distanza lungo la rete stradale. Le risposte non sostituiscono verifiche ufficiali, soprattutto per servizi essenziali. La ricerca deterministica è offline; se il modello scelto è remoto, la parte di interpretazione IA non lo è.

## 12. Tracce GPX e waypoint

### 12.1 Importare e visualizzare GPX

Aprire Mappe → Tracce GPX e selezionare un file `.gpx` contenente una traccia o un itinerario. L'importazione parte automaticamente. Il limite è 5 MB. Il file deve contenere geometria valida; una raccolta composta solo da waypoint non equivale a una traccia. Attendere la conferma. Fare clic sul titolo della traccia per visualizzarla sulla mappa che la copre, se disponibile. Nella mappa aprire il pannello Tracce GPX e selezionare le tracce da mostrare o nascondere. La libreria mostra informazioni come lunghezza, dislivello quando disponibile e numero di punti. Sono valori derivati dal GPX, non una pianificazione stradale.

Rinomina cambia il nome della traccia (massimo 120 caratteri). Elimina rimuove la traccia con conferma, non le mappe o i waypoint. Se non esiste una mappa di copertura scaricare la regione pertinente. PHAROS non ricalcola la traccia né genera indicazioni di navigazione.

### 12.2 Creare e gestire waypoint

Aprire una mappa, premere l'icona Waypoint, quindi Aggiungi (più). Toccare la posizione desiderata sulla mappa. Compilare nome (massimo 120 caratteri), descrizione (massimo 2000), latitudine e longitudine; le coordinate possono essere corrette nel modulo. Salvare. Per modificare un waypoint usare Gestisci (matita) e selezionare il marker. Cambiare testo o coordinate, oppure scegliere Seleziona nuova posizione sulla mappa e toccare il nuovo punto; salvare oppure annullare lo spostamento. Elimina richiede conferma e rimuove soltanto il waypoint selezionato. Termina esce dalla modalità di gestione. Le coordinate devono essere valide: latitudine da -90 a 90, longitudine da -180 a 180.

Per esportare i waypoint usare Esporta waypoint in GPX nella sezione Mappe → Tracce GPX. Conservare il file come copia portabile dei punti. L'esportazione dei waypoint non costituisce un backup completo di PHAROS.

## 13. Notifiche e lavori in background

La barra superiore indica la presenza di notifiche; aprirla o usare il riquadro Notifiche. I lavori di download ZIM, mappe, indici geografici, ingestione, indice testuale e reindicizzazione sono gestiti dal server. Le attività possono essere in attesa, in corso, completate, non riuscite, annullate o interrotte. Le attività attive sono visibili anche nelle sezioni interessate; la notifica finale segnala il risultato. Selezionare una notifica per aprire la sezione e archiviarla quando previsto. Archiviare una notifica non elimina il contenuto scaricato.

Il pulsante Annulla, quando offerto, richiede la cancellazione del lavoro. Attendere l'effettivo stato finale; non spegnere il server presumendo che l'annullamento sia immediato. La persistenza della coda permette il recupero di diversi lavori dopo riavvio; alcune attività ripartono da una fase precedente (l'indice testuale può ricominciare dal primo chunk), altre possono risultare interrotte. Controllare lo stato prima di avviare un duplicato. La chiusura del browser non interrompe di norma un lavoro server già accettato. Un guasto del NAS, del provider o della rete può impedirne il completamento.

## 14. Diagnostica e aggiornamenti

### 14.1 Stato del sistema

Aprire Diagnostica e controllare PHAROS Core, database locale, volume dati e motore IA. Premere Aggiorna per ripetere il controllo dei servizi. Uno stato IA raggiungibile non garantisce che il modello scelto supporti ogni funzione. Se il volume dati è indisponibile, risolvere il mount prima di scrivere o aggiornare. Se il database non risponde controllare servizio e filesystem locale. Usare i log Ubuntu per errori più dettagliati; non divulgare credenziali o contenuti privati.

### 14.2 Aggiornare dall'interfaccia Ubuntu

Nel pannello Aggiornamenti PHAROS controllare Versione installata, Versione disponibile e Ultima verifica. Premere Verifica aggiornamenti per consultare il repository ufficiale. Quando esiste un aggiornamento, Visualizza le modifiche su GitHub permette di controllare il diff; Installa aggiornamento avvia direttamente la procedura senza il vecchio popup di conferma. La disponibilità viene verificata rispetto al branch `main`, non a un catalogo di release stabili certificato. Un commit può cambiare pur mantenendo lo stesso numero di versione. La versione remota è letta dal metadato del commit verificato; se non ottenibile può comparire un valore non disponibile. Gli SHA rimangono usati internamente.

Prima di premere Installa aggiornamento, verificare di avere un backup indipendente e attendere la fine dei lavori. Il server blocca l'aggiornamento se sono attivi o accodati download, ingestione, reindicizzazioni, indici geografici/testuali o richieste incompatibili, mostrando il motivo. Non aggirare il blocco. Terminare i lavori, ripetere la verifica se necessario e riprovare. Durante l'installazione appare Aggiornamento in corso. Il servizio può riavviarsi, con breve indisponibilità; non chiudere o riavviare manualmente la VM per accelerare. Al termine la pagina si ricarica e mostra la versione effettiva. Se l'aggiornamento fallisce, PHAROS tenta il rollback previsto; leggere eventuali errori e verificare i servizi.

Il worker web è progettato per Ubuntu x86-64 installato con i servizi previsti. Un aggiornamento che richiede migrazioni, binari terzi nuovi o interventi manuali può essere rifiutato. La procedura automatica salva configurazioni e SQLite, non sostituisce un backup completo di originali, Qdrant, ZIM e PMTiles.

### 14.3 Aggiornamento manuale Ubuntu

Con backup verificato, lavori terminati, connessione GitHub e checkout ufficiale pulito, da SSH:

```bash
cd ~/pharos
bash scripts/update-ubuntu.sh
```

Se si usa un TOML non standard, passare il parametro `--config` corretto. Lo script usa un aggiornamento fast-forward conservativo. Non forzare merge o reset in presenza di modifiche locali; conservare e valutare le personalizzazioni. Se l'aggiornamento richiede operazioni specifiche seguire le note della revisione. Per Windows seguire l'installer sperimentale locale, non il worker Ubuntu.

## 15. Lingue e traduzioni

La lingua si seleziona in Configurazione → Generale → Lingua, quindi Salva configurazione. Italiano e inglese sono integrati e di sola lettura. Il riquadro Traduzioni della Home apre la gestione dei cataloghi. Selezionare una lingua per consultarla, oppure Nuova lingua per crearne una. Inserire un codice come `fr`, `de` o `pt-BR` (due lettere minuscole, regione opzionale maiuscola) e un nome leggibile. Preparare la traduzione, cercare le chiavi o i testi inglesi, compilare i valori desiderati e salvare. Il filtro delle voci mancanti facilita la traduzione parziale. Le stringhe vuote o non tradotte ricadono sull'inglese; selezionare la nuova lingua in Configurazione per applicarla.

Preservare i segnaposto come `{name}`, `{count}`, `{done}`, `{total}` esattamente, potendo riordinarli secondo la grammatica. I valori dinamici, come numero di versione, quantità e nomi, non vanno tradotti né duplicati nel catalogo. I cataloghi sono JSON di dati, non HTML o JavaScript. Le lingue personalizzate risiedono in `storage.data_dir/locales/<code>.json`; non sono sovrascritte dagli aggiornamenti del Core. Se una vecchia lingua contiene chiavi rimosse dal riferimento inglese, può essere necessario eliminare queste chiavi dal file prima del salvataggio. Eseguire una copia prima di modificare manualmente il JSON. Non eliminare la lingua attiva: selezionarne prima un'altra. L'eliminazione delle lingue integrate è bloccata.

Esempio minimale di catalogo personalizzato:

```json
{
  "code": "fr",
  "name": "Français",
  "messages": {"homeWelcome": "Bienvenue dans PHAROS"}
}
```

## 16. Personalizzazione

Nella Home premere Modifica. Riordinare i riquadri trascinandoli o con le frecce su/giù; usare le frecce sui dispositivi touch quando il trascinamento non è comodo. Mostra/Nascondi regola la visibilità. Premere Salva per rendere persistenti le preferenze, Annulla per scartarle, Ripristina e Salva per tornare alla disposizione predefinita. Nascondere una funzione non la disinstalla, non arresta i servizi e non cancella dati. Le preferenze sono condivise dall'installazione, non da account separati.

Per personalizzazioni avanzate, PHAROS carica `custom.css` dalla directory del TOML, normalmente `~/.config/pharos/custom.css`. Creare il file con sole regole CSS, conservare una copia e ricaricare il browser; evitare di nascondere avvisi, pulsanti di conferma o elementi di accessibilità. Non modificare i CSS del pacchetto installato: potrebbero essere sovrascritti durante l'aggiornamento. Se una personalizzazione rende il sito inutilizzabile, rinominare il proprio `custom.css` e ricaricare.

## 17. Backup e ripristino

### 17.1 Backup completo Ubuntu

Il backup del worker aggiornamenti non è un backup completo. Lo script `scripts/pharos-backup.py` copia configurazione, segreti, database, Qdrant, dati originali, ZIM, PMTiles, cache, GPX, waypoint e file operativi. È una copia completa, non incrementale. Richiede una destinazione indipendente, scrivibile e capiente, con directory genitore esistente e nome finale non ancora utilizzato. Terminare i lavori, arrestare i servizi e utilizzare percorsi reali sostituendo l'esempio:

```bash
cd ~/pharos
systemctl --user stop pharos.service pharos-qdrant.service
python3 scripts/pharos-backup.py backup /backup/pharos-copy
python3 scripts/pharos-backup.py verify /backup/pharos-copy
systemctl --user start pharos-qdrant.service pharos.service
```

La verifica controlla il manifest con dimensioni e SHA-256. Se il backup o la verifica fallisce, non eliminare i dati originali; diagnosticare l'errore e ripristinare l'operatività in modo controllato. Proteggere il backup perché include segreti e documenti. Se il NAS non è montato, non creare una copia del mountpoint vuoto. Verificare periodicamente anche la possibilità di ripristino su una macchina separata.

### 17.2 Ripristino su ambiente vuoto

Il comando `restore-empty` è destinato a una macchina di recupero separata con percorsi assoluti originali disponibili, vuoti e servizi arrestati. Non ripristinare sopra la produzione funzionante. Preparare i mount corretti e verificare la copia:

```bash
cd ~/pharos
python3 scripts/pharos-backup.py verify /backup/pharos-copy
python3 scripts/pharos-backup.py restore-empty /backup/pharos-copy
```

Lo script rifiuta destinazioni non vuote. Dopo il ripristino reinstallare/preparare i runtime e i servizi con la procedura documentata, verificando prima TOML e percorsi. I file systemd salvati nel backup sono riferimenti, non servizi riattivati automaticamente. Se l'operazione è interrotta, l'ambiente di recupero può essere parziale; ripartire su destinazioni vuote. Il ripristino reale di grandi indici di produzione non è qualificato per ogni scenario. Per Windows predisporre copie indipendenti a processi arrestati; non assumere che gli script systemd Ubuntu funzionino su Windows.

## 18. Sicurezza e protezione dei dati

PHAROS è progettato per LAN/VPN fidate, senza account utente o autorizzazioni granulari. Non esporre direttamente la webapp, Qdrant, Kiwix o il provider IA su Internet. Usare firewall, VPN, segmentazione e controllo degli accessi al server. Proteggere i file dei segreti e le copie di sicurezza con permessi appropriati. Non inserire chiavi API nei documenti da indicizzare. Il provider remoto riceve i contenuti necessari alla richiesta, compresi i frammenti RAG; verificare la destinazione prima di trattare materiale personale. Le conferme e i controlli del volume proteggono da alcuni errori accidentali, non sostituiscono autorizzazioni e backup.

Scaricare software e archivi da fonti ufficiali. Non modificare gli SHA, i marker o le verifiche d'integrità per superare un errore. Non usare risposte IA, dati geografici o enciclopedici offline come unica fonte per decisioni mediche, legali, di emergenza o sicurezza. Rispettare le licenze di software, documenti, ZIM e OpenStreetMap quando si redistribuiscono contenuti. La consultazione locale non implica automaticamente diritto di redistribuzione.

## 19. Risoluzione dei problemi

| Sintomo | Verifiche e azione |
|---|---|
| PHAROS non raggiungibile | Controllare indirizzo del server, VPN/LAN, porta, proxy, `systemctl --user status pharos.service` e log. Non usare localhost da un altro dispositivo. |
| Volume dati indisponibile | Controllare mount NAS, permessi, identità del volume; ripristinare prima di scrivere. |
| IA non raggiungibile | Verificare URL dal server, provider selezionato, chiave, rete e servizio. Un nome `.local` non risolto può richiedere correzione DNS/mDNS o IP stabile. |
| Nessun modello chat | Aggiornare catalogo, verificare IA abilitata e modello installato; controllare credenziali e compatibilità provider. |
| Modello non in NPU | Caricare tramite i controlli disponibili o il gestore Tiiny; controllare spazio NPU e policy. |
| Risposta lenta o timeout | Provare modello più piccolo, thinking automatico/disattivato quando supportato, controllare coda e timeout; non moltiplicare i tentativi. |
| Attivazione KB fallita | Verificare embedding valido, Qdrant, volume e chiave. |
| PDF non indicizzato | Verificare testo selezionabile, formato e limite dimensione; eseguire OCR esterno se necessario. |
| RAG non trova risposta | Controllare KB selezionata, documenti indicizzati, modello embedding e formulazione; leggere fonti. |
| Indice testuale obsoleto | Ricostruire dalla KB dopo aver terminato le modifiche ai documenti. |
| Kiwix vuoto o fermo | Verificare ZIM valido e volume; il primo archivio completo abilita l'avvio del servizio. |
| Download ZIM fallito | Verificare spazio, URL ufficiale, checksum, rete e stato coda; non pubblicare file parziali. |
| Mappa non visualizzabile | Verificare librerie locali, WebGL, integrità PMTiles e spazio; ripetere il setup se mancano le risorse. |
| Ricerca geografica senza risultati | Verificare area indicizzata, località, civico e tag OSM; provare ricerca manuale; aggiornare l'indice v3 se necessario. |
| Chat geografica fallita | Deselezionare KB, scegliere modello tool calling compatibile, semplificare la domanda e controllare query interpretata. |
| GPX non visibile | Verificare formato, dimensione, presenza di traccia e mappa di copertura. |
| Aggiornamento bloccato | Leggere il motivo, attendere lavori in corso, controllare volume, checkout e GitHub. |
| Aggiornamento fallito | Verificare eventuale rollback, versione reale e log; non forzare il reset del repository. |
| Lingua personalizzata non salvabile | Verificare codice, chiavi correnti, segnaposto e revisione; ricaricare l'editor dopo modifiche concorrenti. |

## 20. Domande frequenti

### PHAROS funziona davvero senza Internet?
Sì, per le risorse locali già predisposte e raggiungibili. Installazione, nuovi download e aggiornamenti richiedono normalmente Internet; un provider IA remoto resta dipendente dalla rete. La perdita di Internet non equivale alla perdita della LAN.

### Devo installare Docker, Qdrant o Kiwix separatamente?
Non nella procedura Ubuntu integrata ordinaria: il setup prepara le dipendenze previste. Verificare gli esiti dell'installer. Non confondere funzioni integrate con i vecchi moduli installabili dinamicamente.

### Posso usare PHAROS senza Tiiny?
Sì. Si possono usare le funzioni non IA e, per la chat/RAG, un provider compatibile alternativo con i modelli necessari. Le funzionalità specifiche NPU e tool calling possono differire.

### Posso mettere tutto sul NAS?
No, non è consigliato. Conservare i dati voluminosi sul NAS, database e indici sensibili a I/O e locking su filesystem locale. Usare i controlli mount e marker.

### Posso chiudere il browser durante un download?
Sì, dopo che il server ha accettato il lavoro. Controllare Notifiche al ritorno. Arrestare il server è un evento diverso e la ripresa dipende dal tipo di lavoro.

### Collegare Wikipedia alla KB duplica decine di GB?
No. Il collegamento usa l'archivio Kiwix esistente e la ricerca federata; non crea embedding di tutti gli articoli. Scollegare non cancella il file.

### Come posso migliorare la qualità delle risposte documentali?
Usare documenti testuali puliti, nomi chiari, raccolte pertinenti e un embedding adatto alla lingua. Verificare citazioni e domande di prova. Cambiare modello embedding richiede reindicizzazione; FTS5 è una funzione separata e non attiva automaticamente il recupero ibrido nella Chat standard.

### Perché la mappa mostra una strada ma non la trova nella ricerca?
La visualizzazione PMTiles e il database OSM di ricerca sono distinti. Scaricare e indicizzare i dati di ricerca dell'area. Anche un indice presente può non includere un nome o civico specifico.

### Posso trovare una farmacia aperta adesso o calcolare un percorso?
No. La ricerca trova POI presenti nei dati locali, non verifica orari correnti e non calcola itinerari stradali. Controllare le informazioni con fonti appropriate.

### Perché non compare il pulsante Installa aggiornamento?
Verificare la disponibilità rispetto a GitHub, la piattaforma e il tipo di installazione. Windows non usa il worker Ubuntu. Gli aggiornamenti incompatibili richiedono una procedura specifica.

### È sufficiente il backup automatico dell'aggiornamento?
No. Il rollback automatico è limitato; utilizzare il backup completo indipendente per proteggere anche archivi e indici.

## 21. Glossario

- **Core:** applicazione principale PHAROS.
- **Provider IA:** servizio che esegue inferenza e/o embedding.
- **LLM:** modello linguistico generativo.
- **NPU:** acceleratore per elaborazioni neurali.
- **Embedding:** rappresentazione numerica del testo usata per ricerca semantica.
- **RAG:** recupero di fonti seguito da generazione contestualizzata.
- **Chunk:** frammento testuale indicizzato.
- **Qdrant:** database vettoriale.
- **SQLite:** database locale su file.
- **FTS5:** indice full-text SQLite.
- **RTree:** indice spaziale.
- **ZIM:** archivio offline consultabile con Kiwix.
- **PMTiles:** formato archivio di tessere cartografiche.
- **OSM:** OpenStreetMap, dati geografici aperti.
- **PBF:** formato compatto degli estratti OSM.
- **POI:** punto di interesse.
- **GPX:** formato di scambio per tracce, itinerari e waypoint.
- **Waypoint:** punto geografico memorizzato.
- **NAS/NFS:** storage di rete e protocollo di accesso.
- **TOML:** formato del file di configurazione.
- **FIFO:** coda first-in, first-out, con priorità applicative dove previste.
- **SHA-256:** digest usato per verificare integrità.
- **Rollback:** tentativo di ripristino dello stato precedente dopo errore.
- **Tool calling:** richiesta strutturata del modello a una funzione applicativa; i risultati geografici provengono dal database locale.

## Appendice A. Checklist per un primo collaudo

1. Verificare il mount e Diagnostica.
2. Provare chat normale con un modello locale.
3. Creare una KB di prova e importare questo manuale; verificare una risposta e la fonte.
4. Scaricare uno ZIM piccolo e aprirlo, quindi collegarlo a una KB di prova.
5. Scaricare una piccola area PMTiles con indice OSM; provare ricerca manuale e apertura mappa.
6. Provare un GPX piccolo, creare un waypoint di prova ed esportarlo.
7. Verificare la coda Notifiche e la lingua inglese/italiana.
8. Effettuare un backup completo e verificarne i checksum.
9. Solo dopo la fine dei lavori, verificare gli aggiornamenti.
10. Per provare l'autonomia, interrompere Internet mantenendo LAN, server e provider locale; verificare consultazione di ZIM, mappe e KB. Ripristinare la connettività prima di scaricare nuovi contenuti.

## Appendice B. Riferimenti tecnici

Repository ufficiale: https://github.com/mascalx/pharos . Consultare il [README](../README.it.md), [architettura](ARCHITECTURE.md), [backup e ripristino](BACKUP_RESTORE_060.md), [aggiornamenti](UPDATES.md), [sicurezza](SECURITY.md) e [disinstallazione](UNINSTALL.md). Il manuale descrive le funzionalità 0.8 collaudate su Ubuntu; Windows resta sperimentale. Non sono stati collaudati integralmente tutti gli scenari di disaster recovery e tutte le combinazioni di dati e provider.


## Disinstallazione

Consultare i requisiti nel README, arrestare PHAROS su Windows e completare i job. I comandi conservano configurazione e dati.

Ubuntu:

```bash
setup=$(mktemp) && curl -fsSL https://raw.githubusercontent.com/mascalx/pharos/main/uninstall-pharos.sh -o "$setup" && bash "$setup"
```

Windows:

```powershell
$setup = Join-Path $env:TEMP ('pharos-' + [guid]::NewGuid().ToString('N') + '.ps1'); Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/mascalx/pharos/main/uninstall-pharos.ps1' -OutFile $setup -ErrorAction Stop; & $setup
```

Digitare `REMOVE PHAROS`. Vedere [Disinstallazione](UNINSTALL.md) per percorsi personalizzati, Nginx e rimozione completa.


## Appendice — Contratto HTTP per gateway IA esterni

Contratto client effettivo di PHAROS 0.8.0, ricavato da `ai.py`, `tiiny_core.py`, `gateway_inventory.py`, `config.py` e `tools/orchestrator.py`. Un gateway indipendente può implementarlo senza utilizzare Tiiny Gateway originale. Impostare un URL base HTTP(S) senza percorso, credenziali o suffisso /v1. La chiave API si configura nell'interfaccia e viene conservata separatamente nel file secrets.toml.

```toml
[ai]
enabled = true
provider = "tiiny_gateway"
base_url = "https://gateway.example.org:9876"
request_timeout_seconds = 120
max_output_tokens = 1200
```

Tutte le richieste usano `Accept: application/json`; se configurata, `Authorization: Bearer <API_KEY>`; i POST JSON usano Content-Type application/json. In modalità tiiny_gateway, solo le richieste di inferenza (chat, tool, embedding) includono `X-Tiiny-Priority: 10` per interattive o `90` per elaborazioni in background (0 = priorità massima). Il gateway gestisce autonomamente scheduling e caricamento dei modelli. Nessuna chiamata diretta alle API TiinyOS né alle operazioni load/unload in questa modalità. PHAROS non segue redirect né proxy da ambiente. Timeout configurabile 5–900 secondi.

### GET /v1/models

Elenco standard dei modelli, senza parametri query né corpo. PHAROS legge al massimo le prime 100 voci di `data`, mantenendo gli `id` stringa non vuoti. Necessario per la normale scoperta dei modelli anche in presenza dell'inventario esteso.

**Risposta di esempio (HTTP 200)**

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

Inventario esteso Tiiny Gateway, senza parametri né corpo. Facoltativo per l'inferenza di base, necessario per la scoperta completa dei modelli installati. `models` deve essere un array di oggetti (massimo 10.000); identificatori ammessi: `id`, `model_id`, `model`, `fullname`, `name`, non vuoti e di massimo 256 caratteri. `running` è facoltativo e contiene ID oppure oggetti con gli stessi campi identificativi; un elenco valido è autoritativo per la residenza NPU, anche se vuoto. In alternativa PHAROS usa i booleani `loaded` o `in_npu`, oppure stati riconosciuti in `status`/`state`. Stati installati: downloaded, installed, stopped, unloaded, idle; stati attivi: running, loaded, active, serving. Download incompleti e `downloaded:false` vengono esclusi. Una residenza ignota non viene considerata attiva. Campi opzionali: `type`/`kind`, array di stringhe `capabilities`, `npu_usage` numerico 0–100, `desc`/`description`. Se l'inventario non è disponibile, è rifiutato o non valido, PHAROS ripiega su `/v1/models` e segnala il catalogo parziale.

**Risposta di esempio (HTTP 200)**

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

Chat ordinaria e generazione risposte RAG. `model` è l'ID esatto, `messages` è l'array della conversazione, `stream` vale false, `max_tokens` deriva dalla configurazione. `choices[0].message.content` deve essere una stringa non vuota. Non viene inviato `temperature`. Su richiesta esplicita per modelli chat compatibili PHAROS aggiunge `chat_template_kwargs: {"enable_thinking": true}` (oppure false); un gateway che non supporta l'opzione dovrebbe rifiutarla anziché dichiararla applicata.

**Corpo richiesta**

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

**Risposta di esempio (HTTP 200)**

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

Richieste embedding: array non vuoto di stringhe non vuote. Restituire una voce per input; gli `index` interi devono coprire da 0 a N−1 una sola volta ciascuno (ordine delle voci libero). Vettori non vuoti, numeri finiti, dimensione uguale nella risposta e massimo 8192 dimensioni. I vettori di esempio sono volutamente corti; la dimensione reale deve restare coerente per il modello indicizzato. PHAROS prova l'endpoint embeddings prima di accettare un nuovo modello.

**Corpo richiesta**

```json
{
  "model": "local/embedding-model",
  "input": [
    "First text",
    "Second text"
  ]
}
```

**Risposta di esempio (HTTP 200)**

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

Function calling nativo per la ricerca geografica. La funzione di esempio è illustrativa, non una funzione realmente registrata in PHAROS. L'array `tools` effettivo contiene gli schemi forniti dinamicamente da PHAROS; accettare `tool_choice` auto o required. Restituire al massimo una chiamata per risposta, `type:function`, `id` univoco non vuoto di massimo 256 caratteri, nome di funzione registrato (massimo 64), `arguments` come stringa contenente un oggetto JSON, non come oggetto (massimo 4096 byte UTF-8). PHAROS valida ed esegue localmente i tool; il gateway non deve eseguirli. Una richiesta successiva può contenere un messaggio assistant con `content:null` e `tool_calls`, seguito da `role:tool`, `tool_call_id` corrispondente e `content` come stringa JSON. Accettare tali messaggi. Una risposta senza chiamate non deve dichiarare finish_reason tool_calls o length. Limite risposta 262144 byte; orchestrazione limitata a quattro inferenze, sei chiamate e massimo 180 secondi. Estensione thinking identica alla chat ordinaria.

**Corpo richiesta**

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

**Risposta di esempio (HTTP 200)**

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

### Errori e compatibilità

Restituire risposte JSON valide per i successi e codici HTTP appropriati per gli errori; 401/403 indicano autenticazione fallita, 404 sull'inventario esteso indica endpoint non supportato. Per chat con thinking, 400/422 causano rifiuto esplicito dell'opzione. Per tool calling, 400/404/405/415/422/501 indicano API tool rifiutata; timeout e altri errori vengono gestiti separatamente. Non restituire un falso successo. Per un servizio OpenAI-compatible generico scegliere `openai_compatible`: usa i tre endpoint standard, senza inventario esteso e senza header di priorità. Il supporto effettivo di chat, embedding e tool dipende dal modello e dal gateway. La ricerca geografica resta locale a PHAROS, ma il gateway riceve prompt, messaggi, frammenti RAG e risultati tool quando necessari. Usare HTTPS per collegamenti non fidati e non registrare credenziali.
