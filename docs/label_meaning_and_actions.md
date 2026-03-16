# Bottleneck Analyzer — Significato label e azioni operative

Questo documento descrive cosa significa ogni label e cosa controllare subito.

---

## costo PHP elevato
Significato:
- richieste PHP con latenza alta o consumo memoria rilevante

Controllare subito:
- endpoint lenti (admin-ajax, wp-json, index)
- plugin coinvolti
- eventuali loop ajax o REST

Controllare dopo:
- query lente DB
- saturazione PHP workers

---

## slow PHP reali
Significato:
- slow log con eventi ripetuti

Controllare subito:
- plugin indicati nello slow log
- combinazioni plugin cache/optimizer
- purge cache automatici

Controllare dopo:
- profiling codice
- timeout upstream

---

## cron molto attivi
Significato:
- molti eventi wp-cron o scheduler

Controllare subito:
- action_scheduler_run_queue
- broken link checker
- analytics cron

Controllare dopo:
- disabilitare wp-cron interno e usare cron di sistema

---

## cache instabile / rigenerazioni frequenti
Significato:
- purge frequenti o metriche object cache elevate

Controllare subito:
- object cache (Redis / plugin cache)
- plugin optimizer immagini
- invalidazioni cache frontend

Controllare dopo:
- configurazione TTL cache
- conflitti plugin cache

---

## lavoro interno elevato
Significato:
- lavoro non causato da traffico utenti

Controllare subito:
- job schedulati
- metriche cache
- hook periodici plugin

---

## traffico sporco / bot
Significato:
- probe, crawler aggressivi o IP dominanti

Controllare subito:
- rate limit
- firewall / Cloudflare rules
- blocco path sospetti

---

## errori backend ripetuti
Significato:
- warning/error ricorrenti nello stesso file o signature

Controllare subito:
- file indicato
- plugin coinvolto
- log PHP error

---

## traffico backend elevato
Significato:
- alto numero richieste backend nell’ora

Controllare subito:
- endpoint dominanti
- caching login/ajax
- sessioni utente persistenti
