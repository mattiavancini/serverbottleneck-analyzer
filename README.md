# Server Bottleneck Analyzer

Strumento CLI Python per analizzare localmente, ogni ora, i log di server Cloudways-like multi-app WordPress senza esportare log grezzi.

## Scopo

Il tool gira sul server, legge i log per-app disponibili all'utente applicativo e produce output strutturati e stabili per:

- overview server
- ranking app sospette
- top suspect apps
- storicizzazione locale in `txt/json/csv`
- futura ingestione verso una web app centrale

Non invia email orarie e non invia log grezzi.

## Contesto operativo

Ambiente target:

- server Cloudways-like
- multi-app WordPress
- niente `root`
- niente `sudo`
- niente accesso affidabile a `/var/log/nginx` o `/var/log/apache2`
- log leggibili sotto `~/applications/<app_id>/logs`

Identificatore stabile principale:

- `app_id`

## Log analizzati

Per ogni app il tool legge, se presenti:

- `backend_wordpress*.access.log`
- `static_wordpress*.access.log`
- `php-app.access.log`
- `php-app.slow.log`
- `php-app.slow.log.1`
- `php-app.slow.log*.gz`
- `wp-cron.log`
- `backend_wordpress*.error.log`

## Output generati

Con `--output-dir` il tool salva sempre:

- report testuale: `inspection-<UTC>.txt`
- payload JSON standard: `inspection-<UTC>.json`
- CSV sintetico per app analizzata: `inspection-<UTC>.csv`
- snapshot storage: `storage-<UTC>.json`
- report storage umano: `storage-<UTC>.txt`
- CSV crescita storage: `storage-growth-<UTC>.csv`

Struttura:

```text
reports/<server_name>/<YYYY-MM-DD>/inspection-<UTC_TIMESTAMP>.txt
reports/<server_name>/<YYYY-MM-DD>/inspection-<UTC_TIMESTAMP>.json
reports/<server_name>/<YYYY-MM-DD>/inspection-<UTC_TIMESTAMP>.csv
reports/<server_name>/<YYYY-MM-DD>/storage-<UTC_TIMESTAMP>.json
reports/<server_name>/<YYYY-MM-DD>/storage-<UTC_TIMESTAMP>.txt
reports/<server_name>/<YYYY-MM-DD>/storage-growth-<UTC_TIMESTAMP>.csv
```

## JSON standard

Il JSON standard e volutamente sintetico e pronto per futura ingestione.

Campi top-level principali:

- `contract_version`
- `generated_at_utc`
- `server_name`
- `fixture_mode`
- `analysis_window`
- `server_snapshot`
- `ranked_apps`
- `top_suspect_apps`
- `high_priority_total`
- `additional_high_priority_count`
- `app_details`
- `final_warnings`

Estensioni additive del payload:

- `server_snapshot.redis_*`
- `server_snapshot.redis_status`
- `app_details[].cron_top_hooks`
- `app_details[].cron_signal_strength`
- `app_details[].cron_suspected_sources`
- `app_details[].action_scheduler_detected`
- `app_details[].action_scheduler_pending`
- `app_details[].action_scheduler_failed`
- `app_details[].action_scheduler_old_pending`
- `app_details[].action_scheduler_top_hooks`
- `app_details[].slowlog_top_paths`
- `app_details[].slowlog_suspected_plugins`
- `app_details[].slowlog_entrypoint_signals`

Con `--debug-json` viene aggiunta la sezione opzionale `debug` con dettagli piu verbosi e investigativi.

Le nuove sezioni diagnostiche sono best effort:

- vengono raccolte solo per le top suspect apps gia selezionate
- non influenzano `suspicion_score`
- se `wp-cli`, DB o Redis non sono accessibili, i campi restano vuoti/default senza far fallire il collector

## ranked_apps vs top_suspect_apps

- `ranked_apps`
  - contiene il ranking completo di tutte le app classificate
  - ordinato in modo deterministico per `suspicion_score`, poi per priorita, poi per traffico backend, poi per `app_id`
- `top_suspect_apps`
  - contiene solo le prime 5 app del ranking finale
  - serve come shortlist operativa per la web app centrale
- `app_details`
  - contiene il dettaglio sintetico solo delle top 5

## priority e suspicion_score

### priority

Valori:

- `ALTA`
- `MEDIA`
- `BASSA`

Uso:

- `ALTA`: evidenza forte di costo PHP reale e score alto
- `MEDIA`: pressione secondaria reale, ma non ancora evidenza PHP critica
- `BASSA`: segnale debole o rumoroso

### suspicion_score

`suspicion_score` e un punteggio numerico stabile e additivo usato per spiegare l'ordinamento finale.

Contribuisce da segnali gia presenti nel tool:

- volume backend
- p95 PHP
- costly PHP requests
- slow PHP events
- cron runs
- backend errors
- dirty/bot traffic
- cache churn

Non e una misura assoluta di gravita: serve a ordinare in modo coerente le app sospette.

## App ALTA oltre top 5

Il payload espone:

- `high_priority_total`
- `additional_high_priority_count`

Questo permette alla futura web app di sapere se esistono altre app `ALTA` fuori dalle top 5 mostrate in `top_suspect_apps`.

## Uso CLI

Esempio base:

```bash
PYTHONPATH=src python3 -m serverbottleneck.cli --server-name cloudways-359695 --output-dir reports
```

Fixture mode:

```bash
PYTHONPATH=src python3 -m serverbottleneck.cli \
  --applications-root /path/to/fixture-root \
  --fixture-mode \
  --server-name fixture-server \
  --output-dir reports
```

JSON con sezione debug:

```bash
PYTHONPATH=src python3 -m serverbottleneck.cli \
  --server-name cloudways-359695 \
  --output-dir reports \
  --debug-json
```

Flag principali:

- `--applications-root`
- `--top`
- `--fixture-mode`
- `--output-dir`
- `--server-name`
- `--debug-json`
- `--skip-storage`
- `--json-out`
- `--csv-out`

## Storage / disk growth

La pipeline storage e separata dal ranking performance. Ogni run con `--output-dir` produce uno snapshot storage stabile con contratto:

```text
serverbottleneck.storage.v1
```

Obiettivo:

- capire quale app cresce
- distinguere bucket principali: `logs`, `cache`, `uploads`, `wpallimport`, `local_backups`, `tmp`, `debug_log`
- confrontare snapshot precedente e baseline 24h quando disponibili
- produrre top sospetti leggibili senza salvare log grezzi

Consultazione rapida:

```bash
python3 scripts/summarize_storage.py --data-dir ../data --server wp-x --hours 24
python3 scripts/serverbottleneck_menu.py --data-dir ../data --server wp-x
```

Il menu SSH mostra status server, trend testuali e crescita storage locale. Il collector resta non interattivo.

Per installare il comando corto locale:

```bash
./scripts/install_sba_link.sh
sba --server WP_Q
```

Il dashboard usa barre testuali per disco/RAM/swap e normalizza il load average sui core CPU:

- `~1.00/core`: i core sono occupati
- `>1.50/core`: coda alta, server sotto pressione
- `>2.00/core`: overload forte

### Ciclo vita app

Lo storage JSON distingue due casi diversi:

- app scoperta nella run, ma con scan parziale: `lifecycle.status=active`, `scan_state=partial`
- app non scoperta nella run corrente: `lifecycle.status=missing_current_discovery`, dimensioni mantenute dal precedente snapshot

Dopo `missing_app_grace_snapshots` snapshot consecutivi senza discovery, l'app diventa:

```text
lifecycle.status=deleted_or_moved_candidate
```

Dopo `missing_app_retire_snapshots` snapshot consecutivi senza discovery, l'app non viene piu riportata nello snapshot corrente. Gli snapshot storici restano nella retention dati.

Questo evita di confondere timeout/permessi negati con app spostate o cancellate.

## Notifiche SMTP

Le notifiche sono separate dal collector. Il collector scrive JSON; lo script notifiche legge gli snapshot locali e invia:

- alert spazio disco se supera le soglie
- report giornaliero con CPU load medio, RAM media, swap media e utilizzo disco

Configurazione locale:

```bash
cd ~/serverbottleneck/analyzer
cp config/notifications.example.json config/notifications.json
nano config/notifications.json
```

`config/notifications.json` non viene committato. Inserire host SMTP, porta, username, password, mittente e destinatari. Per `siti@automa.biz` servono almeno:

- `smtp.host`
- `smtp.port`
- `smtp.username`
- `smtp.password`
- `smtp.from`
- `smtp.to`

Test senza invio:

```bash
SERVER_NAME=WP_Q ./scripts/send_notifications.sh alert --dry-run
SERVER_NAME=WP_Q ./scripts/send_notifications.sh daily --dry-run
```

Cron consigliati:

```cron
12 * * * * cd $HOME/serverbottleneck/analyzer && SERVER_NAME=WP_Q ./scripts/send_notifications.sh alert >> $HOME/serverbottleneck/logs/notifications.log 2>&1
30 7 * * * cd $HOME/serverbottleneck/analyzer && SERVER_NAME=WP_Q ./scripts/send_notifications.sh daily >> $HOME/serverbottleneck/logs/notifications.log 2>&1
```

## Limiti noti

- niente accesso ai log globali di sistema
- ranking iniziale dipendente dai log backend per-app disponibili
- parsing basato sui formati osservati finora
- `wp-cli` enrichment best effort
- nessun invio HTTP verso servizio centrale ancora implementato
- nessun trend storico aggregato lato server centrale ancora implementato

## Roadmap essenziale

- POST del payload JSON verso servizio centrale
- validazione schema/payload lato server centrale
- visualizzazione multi-server nella web app
- trend storici e digest giornaliero centralizzato
