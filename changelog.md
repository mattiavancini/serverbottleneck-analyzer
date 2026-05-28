# Changelog

## 2026-05-28 - Crescita su finestra disponibile

### Contesto

Dopo la prima notte di monitoring su `WP_Q`, la dashboard mostrava correttamente i trend e la crescita dell'ultima ora, ma il menu "ultime 24 ore" restituiva `none`.

Motivo:

- non erano ancora passate 24 ore complete dalla prima baseline
- il collector calcolava `delta_24h` solo quando esisteva uno snapshot vecchio almeno 24 ore
- operativamente serviva invece vedere subito la crescita osservata tra il primo snapshot disponibile e l'ultimo

### Modifica

Aggiornati:

```text
scripts/serverbottleneck_menu.py
scripts/summarize_storage.py
```

Ora il menu:

- mostra la finestra dati disponibile, per esempio `14.3h disponibili su 24h richieste`
- calcola la crescita disco osservata sulla finestra disponibile
- calcola la crescita per app confrontando primo e ultimo snapshot della finestra
- usa questa crescita anche per le voci "ultime 24 ore" e "ultimi 7 giorni" quando non esiste ancora una baseline completa

Questo permette di rispondere subito a domande come:

- "Da ieri a oggi sono spariti 30 GB: da dove vengono?"
- "Quali app sono cresciute da quando abbiamo iniziato il monitoring?"
- "Il problema e cache, backup, log, upload o altro?"

### Nota UI

Riordinato il blocco iniziale della dashboard in modo piu naturale:

1. CPU/load
2. RAM/swap
3. disco
4. PHP-FPM/Redis

Il miglioramento della leggibilita resta un lavoro aperto, ma la priorita di questa modifica e rendere disponibile subito il delta osservato.

### Aggiornamento dashboard 7 giorni

Ulteriore revisione della schermata principale:

- default dashboard portato a `168h` / 7 giorni
- rimosso il concetto ambiguo di "Periodo ultime 24h" dalla vista iniziale
- aggiunta visualizzazione a due colonne con primo e ultimo snapshot disponibili
- aggiunta barra di riempimento della finestra dati 7 giorni
- `Disk growth` chiarito come crescita dal primo snapshot della finestra
- load/RAM/swap mostrati come media e picco della finestra selezionata
- trend testuali resi piu larghi e capaci di occupare due righe
- top storage growth in dashboard aumentato da 5 a 15 app
- top storage growth di dettaglio aumentato a 30 app

Nota operativa:

- quando la finestra 7 giorni non e ancora piena, il dashboard mostra quante ore reali sono disponibili
- il 100% della barra indica che abbiamo coperto l'intera retention target di 7 giorni

## 2026-05-27 - Storage Growth Analyzer e dashboard SSH per WP_Q

### Contesto

Avviata la trasformazione di Server Bottleneck Analyzer in uno strumento operativo locale per server Cloudways-like multi-app WordPress.

Priorita reale:

- capire perche un server cresce di storage in modo anomalo
- monitorare carico e storage direttamente via SSH
- installare il tool su un server live, senza web app centrale obbligatoria
- raccogliere almeno 24 ore di dati orari

Server live usato per il primo deploy:

- `WP_Q`
- path progetto: `~/serverbottleneck`
- repo: `~/serverbottleneck/analyzer`
- dati: `~/serverbottleneck/data/WP_Q`
- app root: `~/applications`
- app scoperte: `72`

### Stato GitHub

Repository:

```text
https://github.com/mattiavancini/serverbottleneck-analyzer.git
```

Commit creati e pushati oggi:

```text
9ba8e55 Add storage growth analyzer and SSH menu
c1d1ecd Improve terminal dashboard readability
9259a35 Fix sba symlink path resolution
520f674 Fix sba default data directory
```

### Storage Growth Analyzer

Aggiunta pipeline storage separata dal ranking performance.

Nuovi moduli:

```text
src/serverbottleneck/storage.py
src/serverbottleneck/storage_reporting.py
```

Nuovi output per ogni run con `--output-dir`:

```text
storage-<UTC>.json
storage-<UTC>.txt
storage-growth-<UTC>.csv
```

Contratto JSON:

```text
serverbottleneck.storage.v1
```

Dati raccolti per app:

- dimensione totale app
- `logs`
- `public_html`
- `wp-content`
- `cache`
- `uploads`
- `wpallimport`
- `local_backups`
- `tmp`
- `debug.log`
- top directory pesanti
- top file grandi
- file grandi modificati di recente
- delta da snapshot precedente
- delta 24h quando esiste una baseline

Classificazioni iniziali:

- `log_growth`
- `cache_growth`
- `upload_growth`
- `wpallimport_growth`
- `backup_accumulation`
- `tmp_growth`
- `debug_log_large`
- `fast_growth`
- `critical_growth`

### Menu SSH

Aggiunto menu terminale:

```text
scripts/serverbottleneck_menu.py
scripts/sba
```

Uso previsto:

```bash
sba --server WP_Q
sba --server WP_Q --once
```

Schermata principale:

- periodo analizzato
- ultimo storage snapshot
- ultimo performance snapshot
- CPU cores
- disk used/free/growth
- load average normalizzato per core
- RAM used
- swap used
- PHP-FPM process count
- Redis status
- trend testuale
- top storage growth

Menu:

```text
[1] Server status
[2] App cresciute ultima ora
[3] App cresciute ultime 24 ore
[4] App cresciute ultimi 7 giorni
[5] Top directory pesanti
[6] Top file grandi/recenti
[7] Dettaglio app
[8] Cambia server
[0] Esci
```

### Migliorie leggibilita dashboard

Aggiunte barre testuali per:

- disco
- load
- RAM
- swap

Aggiunta scala esplicativa per load average:

```text
1.00/core = CPU slots busy
>1.50/core = high queue
```

Aggiunto `cpu_count` nei nuovi JSON `server_snapshot`, cosi il load puo essere letto rispetto ai core disponibili.

Esempio interpretazione:

```text
load=4.89 su 2 core -> 2.45/core, critico
load=4.89 su 8 core -> 0.61/core, sostenibile
```

### Warning performance

Aggiornato warning load:

Prima:

```text
Load average is elevated at 4.89.
```

Dopo:

```text
Load average is elevated at 4.89 (CPU cores=..., load/core=...; scale: ~1.00/core busy, >1.50/core high).
```

### Script consultazione

Aggiunto:

```text
scripts/summarize_storage.py
```

Esempi:

```bash
python3 scripts/summarize_storage.py --data-dir ../data --server WP_Q --hours 1
python3 scripts/summarize_storage.py --data-dir ../data --server WP_Q --hours 24
python3 scripts/summarize_storage.py --data-dir ../data --server WP_Q --only-suspects
python3 scripts/summarize_storage.py --data-dir ../data --server WP_Q --app <app_id>
```

Tutti gli script principali espongono `--help`.

### Comando corto `sba`

Aggiunto installer:

```text
scripts/install_sba_link.sh
```

Comando installazione sul server:

```bash
cd ~/serverbottleneck/analyzer
./scripts/install_sba_link.sh
```

Installa:

```text
~/bin/sba
```

Bug risolti:

- il symlink `~/bin/sba` cercava `serverbottleneck_menu.py` in `~/bin`
- il default data dir puntava a `~/serverbottleneck/analyzer/data` invece di `~/serverbottleneck/data`

Workaround valido prima del fix:

```bash
~/bin/sba --data-dir ~/serverbottleneck/data --server WP_Q --once
```

### Collector e cron

Aggiunto collector installabile nel repo:

```text
scripts/run_collector.sh
```

Caratteristiche:

- usa `APP_ROOT`, `BASE_DIR`, `ANALYZER_DIR`, `OUT_BASE`, `LOG_FILE`, `SERVER_NAME`
- default `SERVER_NAME=$(hostname)`
- default retention: `7` giorni
- produce inspection performance e storage snapshot

Cron installato su `WP_Q`:

```cron
7 * * * * cd $HOME/serverbottleneck/analyzer && SERVER_NAME=WP_Q APP_ROOT=$HOME/applications BASE_DIR=$HOME/serverbottleneck nice -n 10 ionice -c2 -n7 ./scripts/run_collector.sh
```

Cron gia presente sul server, lasciato intatto:

```cron
0 6 * * * cd /mnt/BLOCKSTORAGE/home/233888.cloudwaysapps.com/applications/fxdnvsbsuk/public_html && /usr/bin/php status_tls_daily.php >> /mnt/BLOCKSTORAGE/home/233888.cloudwaysapps.com/applications/fxdnvsbsuk/public_html/logs/status_tls_daily_cron.log 2>&1
```

Snapshot gia generati su `WP_Q` durante il primo deploy:

```text
storage-2026-05-27T13-44-54Z.json
storage-2026-05-27T14-01-23Z.json
```

### Deploy iniziale su WP_Q

Comandi eseguiti:

```bash
mkdir -p ~/serverbottleneck/data ~/serverbottleneck/logs
cd ~/serverbottleneck
git clone https://github.com/mattiavancini/serverbottleneck-analyzer.git analyzer
cd analyzer
python3 --version
PYTHONPATH=src python3 -m serverbottleneck.cli --help
python3 scripts/summarize_storage.py --help
python3 scripts/serverbottleneck_menu.py --help
```

Risultato:

```text
Python 3.9.2
```

Verifica app:

```bash
find -L ~/applications -maxdepth 1 -mindepth 1 -type d | wc -l
find -L ~/applications -maxdepth 2 -type d -name logs | wc -l
```

Risultato:

```text
72
72
```

Discovery Python:

```text
discovered_apps 72
```

Prima run manuale:

```bash
time nice -n 10 ionice -c2 -n7 env PYTHONPATH=src python3 -m serverbottleneck.cli \
  --applications-root ~/applications \
  --server-name WP_Q \
  --output-dir ../data \
  --top 5 \
  --debug-json
```

### Osservazioni operative emerse

Dal primo report su `WP_Q`:

- load elevato osservato
- swap non trascurabile, circa 3 GB
- diverse app con costo PHP/slow log/cron visibili
- Redis rilevato e `OK`
- alcune app mostrano slow log ricorrenti legati a optimizer/cache

Esempi app segnalate nei warning:

- `ddybmpgejx`
- `memshzjhde`
- `zafqzundrh`
- `zeytcxaxzn`
- `fxmtptsyev`

### Note performance/sicurezza

Il tool e pensato per essere read-only sulle app:

- legge log
- legge metadati file/directory
- misura dimensioni
- scrive solo in `~/serverbottleneck/data` e `~/serverbottleneck/logs`

Precauzioni adottate:

- esecuzione cron con `nice`
- esecuzione cron con `ionice`
- retention 7 giorni
- niente invio remoto
- niente modifica file WordPress
- niente cancellazione log/app

### Prossimi passi

1. Lasciare girare il cron per almeno 24 ore.
2. Controllare:

```bash
sba --server WP_Q --once
python3 ~/serverbottleneck/analyzer/scripts/summarize_storage.py --data-dir ~/serverbottleneck/data --server WP_Q --hours 24
```

3. Migliorare dashboard dopo aver visto dati reali su 24 ore:

- trend piu leggibili
- orario ultimo/next cron
- dettaglio soglie load/RAM/swap
- top app growth con motivazione piu chiara

4. Valutare retention:

- default attuale: 7 giorni
- desiderabile futuro: 30 giorni con eventuale compattazione

5. Dopo validazione su `WP_Q`, replicare sugli altri server.
