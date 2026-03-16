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

Struttura:

```text
reports/<server_name>/<YYYY-MM-DD>/inspection-<UTC_TIMESTAMP>.txt
reports/<server_name>/<YYYY-MM-DD>/inspection-<UTC_TIMESTAMP>.json
reports/<server_name>/<YYYY-MM-DD>/inspection-<UTC_TIMESTAMP>.csv
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

Con `--debug-json` viene aggiunta la sezione opzionale `debug` con dettagli piu verbosi e investigativi.

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
- `--json-out`
- `--csv-out`

## Limiti noti

- niente accesso ai log globali di sistema
- ranking iniziale dipendente dai log backend per-app disponibili
- parsing basato sui formati osservati finora
- `wp-cli` enrichment best effort
- nessun invio HTTP verso servizio centrale ancora implementato
- nessun trend storico aggregato lato server ancora implementato

## Roadmap essenziale

- POST del payload JSON verso servizio centrale
- validazione schema/payload lato server centrale
- visualizzazione multi-server nella web app
- trend storici e digest giornaliero centralizzato
