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
