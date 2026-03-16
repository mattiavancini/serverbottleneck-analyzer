# Server Bottleneck Analyzer — Milestone Log

## 2026-03-16 — Validazione reale su server WP E e WP X

### Stato raggiunto
- Tool eseguito con successo su server Cloudways-like reali.
- Ranking multi-app funzionante.
- suspicion_score introdotto e verificato.
- JSON contract v1 stabilizzato.
- server_snapshot reso sintetico.
- wp_related_processes spostato in debug.
- high_priority_total e additional_high_priority_count introdotti.
- Documentazione README strutturata.

### Pattern tecnici osservati
- Presenza significativa di app cron-heavy.
- Slow PHP reali collegati a plugin optimizer/cache.
- Traffico bot consistente ma non unica causa del carico.
- Gruppi di app problematiche (>5) per server.

### Problemi aperti
- Possibile inflazione priorità ALTA da validare su batch più ampio.
- CSV ranking ancora da stabilizzare definitivamente.
- Assenza di trend multi-run per server.

### Decisioni prese
- Non modificare ancora la logica di priorità ALTA.
- Non inviare log grezzi alla futura web app.
- Consolidare prima il contratto dati.

### Prossimo passo unico
- Stabilizzare CSV classifica app e validare su batch reale più ampio.

## 2026-03-16 — Formalizzazione documentale, test reali multi-server e continuità sviluppo

### Stato raggiunto
- Creato il file `docs/milestones.md` come registro append-only del progetto.
- Consolidata la pratica di tracciare stato, decisioni, problemi aperti e prossimo passo unico.
- Allineato il repository GitHub come fonte primaria di codice e documentazione.
- Eseguiti test run completi del tool su 2 server reali (WP E e WP X) con output JSON/CSV validato.

### Decisioni prese
- Un solo milestone log per il progetto, senza file paralleli di note operative.
- `README.md` come documento principale del progetto.
- `docs/milestones.md` come memoria cronologica delle decisioni e dello stato reale.
- Le milestone devono supportare ripresa del lavoro a distanza di giorni o settimane e lettura da parte di strumenti come NotebookLM.

### Problemi aperti
- La documentazione tecnica e operativa è ancora mista tra inglese e italiano.
- Possibile carico cognitivo elevato durante analisi operative del tool.
- Strategia di localizzazione del tool ancora da definire.

### Prossimo passo unico
- Definire e implementare una strategia i18n/l10n del tool con output operativi in italiano e struttura tecnica coerente.

