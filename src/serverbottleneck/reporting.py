from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .models import AnalysisReport, AppAnalysis

ITALIAN_LABELS = {
    "high PHP cost": "costo PHP elevato",
    "slow-cost heavy": "slow PHP reali",
    "cron-heavy": "cron molto attivi",
    "cache churn suspected": "cache instabile / rigenerazioni frequenti",
    "high internal workload": "lavoro interno elevato",
    "bot/probe heavy": "traffico sporco / bot",
    "error-heavy": "errori backend ripetuti",
    "high traffic": "traffico backend elevato",
    "needs manual review": "da verificare manualmente",
    "internal-traffic dominated": "traffico interno dominante",
}

ACTION_RULES = {
    "costo PHP elevato": [
        "Controlla le richieste PHP piu lente e i target finali piu frequenti.",
        "Verifica se ci sono endpoint dinamici non cacheati che stanno concentrando il carico.",
    ],
    "slow PHP reali": [
        "Apri subito php-app.slow.log e verifica i plugin ricorrenti nelle stack trace.",
        "Controlla se gli slow event arrivano da wp-cron.php, admin-ajax.php o da plugin specifici.",
    ],
    "cron molto attivi": [
        "Controlla wp-cron.log e identifica gli hook ripetuti o troppo frequenti.",
        "Verifica se alcuni job possono essere spostati, ridotti o disabilitati temporaneamente.",
    ],
    "cache instabile / rigenerazioni frequenti": [
        "Controlla plugin/object cache e cerca rigenerazioni, purge o warmup troppo frequenti.",
        "Verifica se object cache, preload o page cache stanno generando lavoro interno continuo.",
    ],
    "lavoro interno elevato": [
        "Controlla wp-cron.php, admin-ajax.php e gli altri endpoint WordPress sensibili.",
        "Verifica se il carico arriva da attivita interne invece che da traffico esterno reale.",
    ],
    "traffico sporco / bot": [
        "Controlla IP dominanti, user agent sospetti e path di probing nei log static/backend.",
        "Valuta rate limiting, blocchi mirati o regole WAF sui path piu colpiti.",
    ],
    "errori backend ripetuti": [
        "Controlla subito i warning/errori ripetuti e i file/plugin piu coinvolti.",
        "Verifica se gli errori coincidono con rallentamenti o endpoint specifici.",
    ],
    "traffico backend elevato": [
        "Controlla i top path e verifica se il traffico e legittimo o concentrato su poche risorse.",
        "Verifica quota traffico per IP e presenza di endpoint dinamici ad alto volume.",
    ],
    "traffico interno dominante": [
        "Verifica se richieste locali, callback o job interni stanno dominando l'ora analizzata.",
    ],
    "da verificare manualmente": [
        "Rivedi i log principali dell'app per capire se il segnale e reale o solo rumore residuale.",
    ],
}


def render_text(report: AnalysisReport) -> str:
    lines: list[str] = []
    snapshot = report.snapshot
    lines.append(f"Server: {report.server_name}")
    lines.append(f"Inspection timestamp (UTC): {report.inspection_timestamp.isoformat()}")
    lines.append(f"Mode: {'fixture' if report.fixture_mode else 'live'}")
    lines.append("")
    lines.append("Server Health Snapshot")
    lines.append(f"Timestamp: {snapshot.timestamp.isoformat()}")
    lines.append(f"Snapshot source: {snapshot.source}")
    if report.fixture_mode:
        lines.append("Snapshot note: fixture mode enabled; local/non-server snapshot data shown as unavailable.")
        lines.append("Load average: fixture-mode skipped")
        lines.append("RAM MB total/used/available: fixture-mode skipped")
        lines.append("Swap MB total/used: fixture-mode skipped")
        lines.append("php-fpm process count: fixture-mode skipped")
        lines.append("Top CPU processes:")
        lines.append("  fixture-mode skipped")
        lines.append("Top memory processes:")
        lines.append("  fixture-mode skipped")
        lines.append("WP/CLI process presence:")
        lines.append("  fixture-mode skipped")
    else:
        lines.append(f"Load average: {snapshot.load_averages[0]:.2f} {snapshot.load_averages[1]:.2f} {snapshot.load_averages[2]:.2f}")
        lines.append(
            f"RAM MB total/used/available: {fmt(snapshot.ram_total_mb)} / {fmt(snapshot.ram_used_mb)} / {fmt(snapshot.ram_available_mb)}"
        )
        lines.append(f"Swap MB total/used: {fmt(snapshot.swap_total_mb)} / {fmt(snapshot.swap_used_mb)}")
        lines.append(f"php-fpm process count: {snapshot.php_fpm_process_count}")
        lines.append("Top CPU processes:")
        lines.extend(f"  {line}" for line in snapshot.top_cpu_processes or ["  none seen"])
        lines.append("Top memory processes:")
        lines.extend(f"  {line}" for line in snapshot.top_memory_processes or ["  none seen"])
        lines.append("WP/CLI process presence:")
        lines.extend(f"  {line}" for line in snapshot.wp_related_processes or ["  none seen"])
        lines.append(
            f"Redis: status={snapshot.redis_status} detected={snapshot.redis_detected} reachable={snapshot.redis_reachable} "
            f"used={snapshot.redis_used_memory_human or 'n/a'} peak={snapshot.redis_used_memory_peak_human or 'n/a'}"
        )
    lines.append("")
    lines.append(
        f"Top Apps In Last Completed UTC Hour: {report.ranking_window_start.isoformat()} to {report.ranking_window_end.isoformat()}"
    )
    for idx, ranked in enumerate(report.ranked_apps[:10], start=1):
        label = ranked.blogname or ranked.home_url or ranked.app.app_id
        lines.append(f"{idx}. {label} [{ranked.app.app_id}] backend_requests={ranked.request_count}")
    for analysis in report.app_analyses:
        lines.append("")
        lines.extend(render_app_analysis(analysis))
    lines.append("")
    lines.append("Actionable Warnings")
    if report.actionable_warnings:
        lines.extend(f"- {warning}" for warning in report.actionable_warnings)
    else:
        lines.append("- none")
    return "\n".join(lines)


def render_app_analysis(analysis: AppAnalysis) -> list[str]:
    app = analysis.ranked_app.app
    label = analysis.ranked_app.blogname or analysis.ranked_app.home_url or app.app_id
    italian_labels = translate_labels(analysis.categories)
    lines = [f"Deep Dive: {label} [{app.app_id}]"]
    lines.append(f"Priority: {analysis.priority}")
    lines.append(f"Suspicion score: {analysis.suspicion_score}")
    lines.append(f"Etichette operative: {', '.join(italian_labels)}")
    lines.append("Cosa controllare subito:")
    lines.extend(f"  - {item}" for item in build_immediate_actions(italian_labels))
    lines.append(f"Log directory: {app.log_dir}")
    lines.append("Backend traffic:")
    lines.extend(format_pairs(analysis.backend_summary.get("top_paths"), "  top paths"))
    lines.extend(format_pairs(analysis.backend_summary.get("top_ips"), "  top ips"))
    lines.extend(format_pairs(analysis.backend_summary.get("top_status_codes"), "  top status"))
    lines.extend(format_pairs(analysis.backend_summary.get("sensitive_endpoints"), "  sensitive endpoints"))
    lines.append(f"  top ip share: {analysis.backend_summary.get('top_ip_share_pct', 0)}%")
    lines.append(f"  internal requests: {analysis.backend_summary.get('internal_requests', 0)}")
    lines.append(f"  bot requests: {analysis.backend_summary.get('bot_requests', 0)}")
    lines.append("Static log signals:")
    lines.extend(format_pairs(analysis.static_summary.get("top_suspicious_paths"), "  suspicious paths"))
    lines.extend(format_pairs(analysis.static_summary.get("top_assets"), "  heavy assets"))
    lines.append("PHP access signals:")
    lines.extend(format_pairs(analysis.php_summary.get("top_final_targets"), "  top targets"))
    lines.extend(format_pairs(analysis.php_summary.get("latency_buckets"), "  latency buckets"))
    lines.extend(format_pairs(analysis.php_summary.get("memory_buckets"), "  memory buckets"))
    lines.append(f"  avg latency sec: {analysis.php_summary.get('avg_latency_sec')}")
    lines.append(f"  p95 latency sec: {analysis.php_summary.get('p95_latency_sec')}")
    lines.append(f"  avg memory mb: {analysis.php_summary.get('avg_memory_mb')}")
    lines.append(f"  costly request count: {analysis.php_summary.get('costly_request_count', 0)}")
    for sample in analysis.php_summary.get("expensive_samples", [])[:5]:
        lines.append(
            f"  expensive sample: target={sample['target']} status={sample['status']} duration={sample['duration_sec']} memory_mb={sample['memory_mb']}"
        )
    lines.append("PHP slow log signals:")
    lines.append(f"  slow event count: {analysis.php_slow_summary.get('slow_event_count', 0)}")
    lines.extend(format_pairs(analysis.php_slow_summary.get("top_slow_plugins"), "  top slow plugins"))
    lines.extend(format_pairs(analysis.php_slow_summary.get("top_slow_paths"), "  top slow paths"))
    lines.extend(format_pairs(analysis.php_slow_summary.get("entrypoint_signals"), "  slow entrypoint signals"))
    lines.extend(format_pairs(analysis.php_slow_summary.get("top_slow_signatures"), "  top slow signatures"))
    lines.extend(format_pairs(analysis.php_slow_summary.get("top_slow_plugin_combinations"), "  top plugin combinations"))
    lines.extend(format_pairs(analysis.php_slow_summary.get("functional_categories"), "  functional categories"))
    for sample in analysis.php_slow_summary.get("sample_events", [])[:5]:
        lines.append(
            f"  slow sample: script={sample['script_filename']} plugins={','.join(sample['plugin_slugs']) or '-'} categories={','.join(sample['functional_categories']) or '-'}"
        )
    lines.append("WP-Cron signals:")
    lines.append(f"  cron runs: {analysis.cron_summary.get('run_count', 0)}")
    lines.extend(format_pairs(analysis.cron_summary.get("top_events"), "  top events"))
    lines.extend(format_pairs(analysis.cron_summary.get("tracked_marker_hits"), "  tracked markers"))
    lines.append(f"  cron signal strength: {analysis.enrichment.get('cron_signal_strength')}")
    lines.extend(format_named_counts(analysis.enrichment.get("cron_top_hooks"), "  top wp-cli hooks", "hook"))
    lines.extend(format_named_counts(analysis.enrichment.get("cron_suspected_sources"), "  suspected cron sources", "source"))
    lines.append(
        "  action scheduler: "
        f"detected={analysis.enrichment.get('action_scheduler_detected')} "
        f"pending={analysis.enrichment.get('action_scheduler_pending')} "
        f"failed={analysis.enrichment.get('action_scheduler_failed')} "
        f"old_pending={analysis.enrichment.get('action_scheduler_old_pending')}"
    )
    lines.extend(
        format_named_counts(analysis.enrichment.get("action_scheduler_top_hooks"), "  action scheduler top hooks", "hook")
    )
    lines.append("Backend error signals:")
    lines.extend(format_pairs(analysis.error_summary.get("top_signatures"), "  top signatures"))
    lines.extend(format_pairs(analysis.error_summary.get("top_files"), "  top files"))
    lines.append("Enrichment:")
    if analysis.enrichment.get("mode") == "fixture-skipped":
        lines.append("  fixture mode: wp-cli enrichment skipped")
    else:
        lines.append(f"  home_url: {analysis.enrichment.get('home_url')}")
        lines.append(f"  blogname: {analysis.enrichment.get('blogname')}")
        plugins = analysis.enrichment.get("plugins", [])[:10]
        lines.append(f"  active plugins: {', '.join(plugins) if plugins else 'unavailable'}")
        cron_events = analysis.enrichment.get("cron_events", [])[:10]
        lines.append(f"  wp cron event list: {', '.join(cron_events) if cron_events else 'unavailable'}")
    return lines


def format_pairs(pairs, label: str) -> list[str]:
    if not pairs:
        return [f"{label}: none"]
    lines = [f"{label}:"]
    for key, value in pairs[:10]:
        lines.append(f"    {key}: {value}")
    return lines


def format_named_counts(items, label: str, name_key: str) -> list[str]:
    if not items:
        return [f"{label}: none"]
    lines = [f"{label}:"]
    for item in items[:10]:
        lines.append(f"    {item.get(name_key)}: {item.get('count')}")
    return lines


def fmt(value) -> str:
    return "n/a" if value is None else str(value)


def translate_labels(labels: list[str]) -> list[str]:
    return [ITALIAN_LABELS.get(label, label) for label in labels]


def build_immediate_actions(italian_labels: list[str]) -> list[str]:
    actions: list[str] = []
    seen = set()
    for label in italian_labels:
        for action in ACTION_RULES.get(label, []):
            if action not in seen:
                seen.add(action)
                actions.append(action)
    if not actions:
        return ["Controlla i log dell'app e confronta traffico, cron, slow log ed errori backend."]
    return actions[:4]


def export_json(report: AnalysisReport, path: Path | None, include_debug: bool = False) -> None:
    payload = build_json_payload(report, include_debug=include_debug)
    text = json.dumps(payload, indent=2, default=str)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")


def export_csv(report: AnalysisReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "timestamp_utc",
                "server_name",
                "app_id",
                "priority",
                "suspicion_score",
                "backend_requests",
                "avg_latency_sec",
                "p95_latency_sec",
                "costly_request_count",
                "slow_event_count",
                "cron_runs",
                "bot_requests",
                "backend_error_count",
                "top_ip_share",
                "labels_it",
                "main_action_it",
            ]
        )
        for analysis in report.app_analyses:
            writer.writerow(build_csv_row(report, analysis))


def build_report_paths(output_dir: Path, server_name: str, inspection_timestamp) -> dict[str, Path]:
    safe_server = filesystem_safe(server_name)
    day = inspection_timestamp.strftime("%Y-%m-%d")
    stamp = inspection_timestamp.strftime("%Y-%m-%dT%H-%M-%SZ")
    base = output_dir / safe_server / day
    return {
        "text": base / f"inspection-{stamp}.txt",
        "json": base / f"inspection-{stamp}.json",
        "csv": base / f"inspection-{stamp}.csv",
    }


def write_text_report(report: AnalysisReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_text(report) + "\n", encoding="utf-8")


def filesystem_safe(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value.strip())
    return cleaned or "unknown-server"


def build_csv_row(report: AnalysisReport, analysis: AppAnalysis) -> list:
    italian_labels = translate_labels(analysis.categories)
    actions = build_immediate_actions(italian_labels)
    bot_requests = analysis.backend_summary.get("bot_requests", 0) + analysis.static_summary.get("bot_requests", 0)
    return [
        isoformat_utc(report.inspection_timestamp),
        report.server_name,
        analysis.ranked_app.app.app_id,
        analysis.priority,
        analysis.suspicion_score,
        analysis.backend_summary.get("total_requests", 0),
        analysis.php_summary.get("avg_latency_sec"),
        analysis.php_summary.get("p95_latency_sec"),
        analysis.php_summary.get("costly_request_count", 0),
        analysis.php_slow_summary.get("slow_event_count", 0),
        analysis.cron_summary.get("run_count", 0),
        bot_requests,
        analysis.error_summary.get("total_events", 0),
        analysis.backend_summary.get("top_ip_share_pct", 0),
        "|".join(italian_labels),
        actions[0] if actions else "",
    ]


def build_json_payload(report: AnalysisReport, include_debug: bool = False) -> dict:
    analyzed_by_app_id = {analysis.ranked_app.app.app_id: analysis for analysis in report.app_analyses}
    ranked_apps_payload = []
    for idx, ranked in enumerate(report.ranked_apps, start=1):
        ranked_apps_payload.append(
            {
                "rank": idx,
                "app_id": ranked.app.app_id,
                "display_name": ranked.blogname or ranked.home_url or ranked.app.app_id,
                "backend_requests": ranked.request_count,
                "priority": ranked.priority,
                "suspicion_score": ranked.suspicion_score,
                "labels_it": translate_labels(ranked.categories),
            }
        )

    top_suspects_payload = [build_compact_app_summary(report, analysis) for analysis in report.app_analyses]
    app_details_payload = [build_app_detail_payload(report, analysis) for analysis in report.app_analyses]
    high_priority_total = sum(1 for ranked in report.ranked_apps if ranked.priority == "ALTA")
    top_suspect_high_priority = sum(1 for analysis in report.app_analyses if analysis.priority == "ALTA")

    payload = {
        "contract_version": "serverbottleneck.v1",
        "generated_at_utc": isoformat_utc(report.inspection_timestamp),
        "server_name": report.server_name,
        "fixture_mode": report.fixture_mode,
        "analysis_window": {
            "start_utc": isoformat_utc(report.ranking_window_start),
            "end_utc": isoformat_utc(report.ranking_window_end),
        },
        "server_snapshot": {
            "timestamp_utc": isoformat_utc(report.snapshot.timestamp),
            "source": report.snapshot.source,
            "load_averages": list(report.snapshot.load_averages),
            "ram_total_mb": report.snapshot.ram_total_mb,
            "ram_used_mb": report.snapshot.ram_used_mb,
            "ram_available_mb": report.snapshot.ram_available_mb,
            "swap_total_mb": report.snapshot.swap_total_mb,
            "swap_used_mb": report.snapshot.swap_used_mb,
            "php_fpm_process_count": report.snapshot.php_fpm_process_count,
            "top_cpu_processes": report.snapshot.top_cpu_processes,
            "top_memory_processes": report.snapshot.top_memory_processes,
            "redis_detected": report.snapshot.redis_detected,
            "redis_reachable": report.snapshot.redis_reachable,
            "redis_used_memory_human": report.snapshot.redis_used_memory_human,
            "redis_used_memory_peak_human": report.snapshot.redis_used_memory_peak_human,
            "redis_connected_clients": report.snapshot.redis_connected_clients,
            "redis_keyspace_hits": report.snapshot.redis_keyspace_hits,
            "redis_keyspace_misses": report.snapshot.redis_keyspace_misses,
            "redis_evicted_keys": report.snapshot.redis_evicted_keys,
            "redis_uptime_in_seconds": report.snapshot.redis_uptime_in_seconds,
            "redis_status": report.snapshot.redis_status,
        },
        "ranked_apps": ranked_apps_payload,
        "top_suspect_apps": top_suspects_payload,
        "high_priority_total": high_priority_total,
        "additional_high_priority_count": max(0, high_priority_total - top_suspect_high_priority),
        "app_details": app_details_payload,
        "final_warnings": report.actionable_warnings,
    }
    if include_debug:
        payload["debug"] = {
            "server_snapshot": {
                "wp_related_processes": report.snapshot.wp_related_processes,
            },
            "app_details_verbose": [build_app_detail_payload(report, analysis, include_debug=True) for analysis in report.app_analyses]
        }
    return payload


def build_compact_app_summary(report: AnalysisReport, analysis: AppAnalysis) -> dict:
    italian_labels = translate_labels(analysis.categories)
    actions = build_immediate_actions(italian_labels)
    return {
        "app_id": analysis.ranked_app.app.app_id,
        "display_name": analysis.ranked_app.blogname or analysis.ranked_app.home_url or analysis.ranked_app.app.app_id,
        "priority": analysis.priority,
        "suspicion_score": analysis.suspicion_score,
        "backend_requests": analysis.backend_summary.get("total_requests", 0),
        "labels_it": italian_labels,
        "main_action_it": actions[0] if actions else None,
    }


def build_app_detail_payload(report: AnalysisReport, analysis: AppAnalysis, include_debug: bool = False) -> dict:
    italian_labels = translate_labels(analysis.categories)
    actions = build_immediate_actions(italian_labels)
    bot_requests = analysis.backend_summary.get("bot_requests", 0) + analysis.static_summary.get("bot_requests", 0)
    cron_diagnostics = build_cron_diagnostics(analysis)
    action_scheduler_diagnostics = build_action_scheduler_diagnostics(analysis)
    slowlog_diagnostics = build_slowlog_diagnostics(analysis)
    payload = {
        "app_id": analysis.ranked_app.app.app_id,
        "display_name": analysis.ranked_app.blogname or analysis.ranked_app.home_url or analysis.ranked_app.app.app_id,
        "priority": analysis.priority,
        "suspicion_score": analysis.suspicion_score,
        "labels_it": italian_labels,
        "main_action_it": actions[0] if actions else None,
        "summary": {
            "backend_requests": analysis.backend_summary.get("total_requests", 0),
            "top_ip_share_pct": analysis.backend_summary.get("top_ip_share_pct", 0),
            "bot_requests": bot_requests,
            "backend_error_count": analysis.error_summary.get("total_events", 0),
            "avg_latency_sec": analysis.php_summary.get("avg_latency_sec"),
            "p95_latency_sec": analysis.php_summary.get("p95_latency_sec"),
            "costly_request_count": analysis.php_summary.get("costly_request_count", 0),
            "slow_event_count": analysis.php_slow_summary.get("slow_event_count", 0),
            "cron_runs": analysis.cron_summary.get("run_count", 0),
        },
        "signals": {
            "top_backend_path": first_pair_value(analysis.backend_summary.get("top_paths", [])),
            "top_php_target": first_pair_value(analysis.php_summary.get("top_final_targets", [])),
            "top_slow_plugin": first_pair_value(analysis.php_slow_summary.get("top_slow_plugins", [])),
            "top_backend_error_file": first_pair_value(analysis.error_summary.get("top_files", [])),
            "top_cron_event": first_pair_value(analysis.cron_summary.get("top_events", [])),
        },
        **cron_diagnostics,
        **action_scheduler_diagnostics,
        **slowlog_diagnostics,
    }
    if include_debug:
        payload["debug"] = {
            "log_directory": str(analysis.ranked_app.app.log_dir),
            "labels_en": analysis.categories,
            "immediate_actions_it": actions,
            "backend": {
                "top_paths": build_pair_objects(analysis.backend_summary.get("top_paths", []), "path"),
                "top_ips": build_pair_objects(analysis.backend_summary.get("top_ips", []), "ip"),
                "top_status_codes": build_pair_objects(analysis.backend_summary.get("top_status_codes", []), "status_code"),
                "sensitive_endpoints": build_pair_objects(analysis.backend_summary.get("sensitive_endpoints", []), "endpoint"),
            },
            "static": {
                "top_suspicious_paths": build_pair_objects(analysis.static_summary.get("top_suspicious_paths", []), "path"),
                "top_assets": build_pair_objects(analysis.static_summary.get("top_assets", []), "path"),
            },
            "php_access": {
                "latency_buckets": build_pair_objects(analysis.php_summary.get("latency_buckets", []), "bucket"),
                "memory_buckets": build_pair_objects(analysis.php_summary.get("memory_buckets", []), "bucket"),
                "expensive_samples": analysis.php_summary.get("expensive_samples", []),
                "costly_samples": analysis.php_summary.get("costly_samples", []),
            },
            "php_slow": {
                "top_plugin_paths": build_pair_objects(analysis.php_slow_summary.get("top_plugin_paths", []), "path"),
                "entrypoint_signals": build_pair_objects(analysis.php_slow_summary.get("entrypoint_signals", []), "signal"),
                "top_slow_signatures": build_pair_objects(analysis.php_slow_summary.get("top_slow_signatures", []), "signature"),
                "top_slow_plugin_combinations": build_pair_objects(
                    analysis.php_slow_summary.get("top_slow_plugin_combinations", []),
                    "plugin_combination",
                ),
                "functional_categories": build_pair_objects(analysis.php_slow_summary.get("functional_categories", []), "category"),
                "sample_events": analysis.php_slow_summary.get("sample_events", []),
            },
            "wp_cron": {
                "tracked_marker_hits": build_pair_objects(analysis.cron_summary.get("tracked_marker_hits", []), "marker"),
                "cron_top_hooks": analysis.enrichment.get("cron_top_hooks", []),
                "cron_suspected_sources": analysis.enrichment.get("cron_suspected_sources", []),
                "cron_due_now": analysis.enrichment.get("cron_due_now"),
                "cron_unique_hooks": analysis.enrichment.get("cron_unique_hooks"),
                "cron_total_events": analysis.enrichment.get("cron_total_events"),
            },
            "action_scheduler": {
                "detected": analysis.enrichment.get("action_scheduler_detected"),
                "pending": analysis.enrichment.get("action_scheduler_pending"),
                "failed": analysis.enrichment.get("action_scheduler_failed"),
                "old_pending": analysis.enrichment.get("action_scheduler_old_pending"),
                "top_hooks": analysis.enrichment.get("action_scheduler_top_hooks", []),
            },
            "backend_errors": {
                "top_signatures": build_pair_objects(analysis.error_summary.get("top_signatures", []), "signature"),
                "top_files": build_pair_objects(analysis.error_summary.get("top_files", []), "file"),
                "top_severities": build_pair_objects(analysis.error_summary.get("top_severities", []), "severity"),
            },
            "enrichment": {
                "home_url": analysis.enrichment.get("home_url"),
                "blogname": analysis.enrichment.get("blogname"),
                "plugins": analysis.enrichment.get("plugins", []),
                "cron_events": analysis.enrichment.get("cron_events", []),
                "mode": analysis.enrichment.get("mode"),
            },
        }
    return payload


def build_pair_objects(pairs: list[tuple], key_name: str) -> list[dict]:
    items = []
    for key, count in pairs:
        items.append({key_name: key, "count": count})
    return items


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def first_pair_value(pairs: list[tuple]):
    if not pairs:
        return None
    key, count = pairs[0]
    return {"value": key, "count": count}


def build_cron_diagnostics(analysis: AppAnalysis) -> dict:
    return {
        "cron_top_hooks": analysis.enrichment.get("cron_top_hooks", []),
        "cron_signal_strength": analysis.enrichment.get("cron_signal_strength"),
        "cron_suspected_sources": analysis.enrichment.get("cron_suspected_sources", []),
    }


def build_action_scheduler_diagnostics(analysis: AppAnalysis) -> dict:
    return {
        "action_scheduler_detected": analysis.enrichment.get("action_scheduler_detected", False),
        "action_scheduler_pending": analysis.enrichment.get("action_scheduler_pending", 0),
        "action_scheduler_failed": analysis.enrichment.get("action_scheduler_failed", 0),
        "action_scheduler_old_pending": analysis.enrichment.get("action_scheduler_old_pending", 0),
        "action_scheduler_top_hooks": analysis.enrichment.get("action_scheduler_top_hooks", []),
    }


def build_slowlog_diagnostics(analysis: AppAnalysis) -> dict:
    return {
        "slowlog_top_paths": build_pair_objects(analysis.php_slow_summary.get("top_slow_paths", []), "path"),
        "slowlog_suspected_plugins": build_pair_objects(analysis.php_slow_summary.get("top_slow_plugins", []), "plugin"),
        "slowlog_entrypoint_signals": build_pair_objects(analysis.php_slow_summary.get("entrypoint_signals", []), "signal"),
    }
