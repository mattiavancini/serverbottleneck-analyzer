from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .discovery import discover_applications
from .models import AnalysisReport, AppAnalysis, AppPaths, RankedApp
from .parsers import (
    SENSITIVE_ENDPOINTS,
    detect_functional_categories,
    is_bot_user_agent,
    is_internal_ip,
    parse_combined_access,
    parse_cron_line,
    parse_error_line,
    parse_php_app_access,
    parse_slow_log_blocks,
)
from .system_snapshot import collect_fixture_snapshot, collect_server_snapshot
from .wp_enrichment import collect_wp_enrichment, fixture_wp_enrichment


def build_report(
    applications_root: Path | None = None,
    top_n: int = 5,
    server_name: str = "unknown-server",
    fixture_mode: bool = False,
) -> AnalysisReport:
    inspection_timestamp = datetime.now(timezone.utc)
    snapshot = collect_fixture_snapshot() if fixture_mode else collect_server_snapshot()
    apps = discover_applications(applications_root)
    window_start, window_end = select_analysis_window(apps, inspection_timestamp, fixture_mode)
    initial_ranked_apps = rank_apps_by_backend_traffic(apps, window_start, window_end)
    all_analyses = [
        analyze_app(ranked_app, window_start, window_end, fixture_mode=fixture_mode, include_enrichment=False)
        for ranked_app in initial_ranked_apps
    ]
    all_analyses.sort(key=analysis_sort_key)
    ranked_apps = [analysis.ranked_app for analysis in all_analyses]
    analyses = all_analyses[:top_n]
    for analysis in analyses:
        enrich_analysis(analysis, fixture_mode)
    warnings = build_actionable_warnings(analyses, snapshot)

    return AnalysisReport(
        server_name=server_name,
        inspection_timestamp=inspection_timestamp,
        fixture_mode=fixture_mode,
        snapshot=snapshot,
        ranking_window_start=window_start,
        ranking_window_end=window_end,
        ranked_apps=ranked_apps,
        app_analyses=analyses,
        actionable_warnings=warnings,
    )


def completed_utc_hour(now: datetime) -> tuple[datetime, datetime]:
    now_utc = now.astimezone(timezone.utc)
    end = now_utc.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=1)
    return start, end


def select_analysis_window(apps: list[AppPaths], inspection_timestamp: datetime, fixture_mode: bool) -> tuple[datetime, datetime]:
    if fixture_mode:
        latest_backend_timestamp = find_latest_backend_timestamp(apps)
        if latest_backend_timestamp:
            start = latest_backend_timestamp.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
            return start, start + timedelta(hours=1)
    return completed_utc_hour(inspection_timestamp)


def find_latest_backend_timestamp(apps: list[AppPaths]) -> datetime | None:
    latest: datetime | None = None
    for app in apps:
        if not app.backend_access_logs:
            continue
        for path in app.backend_access_logs:
            for line in read_lines(path):
                record = parse_combined_access(line)
                if not record:
                    continue
                if latest is None or record.timestamp > latest:
                    latest = record.timestamp
    return latest


def rank_apps_by_backend_traffic(apps: list[AppPaths], start: datetime, end: datetime) -> list[RankedApp]:
    ranked: list[RankedApp] = []
    for app in apps:
        if not app.backend_access_logs:
            continue
        count = 0
        for path in app.backend_access_logs:
            for line in read_lines(path):
                record = parse_combined_access(line)
                if record and start <= record.timestamp < end:
                    count += 1
        ranked.append(RankedApp(app=app, request_count=count))
    ranked.sort(key=lambda item: (-item.request_count, item.app.app_id))
    return ranked


def analyze_app(
    ranked_app: RankedApp,
    start: datetime,
    end: datetime,
    fixture_mode: bool = False,
    include_enrichment: bool = False,
) -> AppAnalysis:
    app = ranked_app.app
    backend_summary = analyze_backend(app, start, end)
    static_summary = analyze_static(app, start, end)
    php_summary = analyze_php_access(app, start, end)
    php_slow_summary = analyze_php_slow(app, start, end)
    cron_summary = analyze_wp_cron(app)
    error_summary = analyze_backend_errors(app, start, end)
    enrichment = fixture_wp_enrichment() if fixture_mode or not include_enrichment else collect_wp_enrichment(app.app_root)
    categories = classify_app(backend_summary, static_summary, php_summary, php_slow_summary, cron_summary)
    categories = add_error_heavy_label(categories, error_summary)
    suspicion_score = compute_suspicion_score(categories, backend_summary, static_summary, php_summary, php_slow_summary, cron_summary, error_summary)
    priority = compute_priority(
        categories,
        backend_summary,
        php_summary,
        php_slow_summary,
        cron_summary,
        error_summary,
        suspicion_score,
    )
    ranked_app.suspicion_score = suspicion_score
    ranked_app.priority = priority
    ranked_app.categories = categories
    if include_enrichment:
        ranked_app.home_url = enrichment.get("home_url")
        ranked_app.blogname = enrichment.get("blogname")
    return AppAnalysis(
        ranked_app=ranked_app,
        priority=priority,
        suspicion_score=suspicion_score,
        categories=categories,
        backend_summary=backend_summary,
        static_summary=static_summary,
        php_summary=php_summary,
        php_slow_summary=php_slow_summary,
        cron_summary=cron_summary,
        error_summary=error_summary,
        enrichment=enrichment,
    )


def enrich_analysis(analysis: AppAnalysis, fixture_mode: bool) -> None:
    if fixture_mode:
        analysis.enrichment = fixture_wp_enrichment()
        return
    enrichment = collect_wp_enrichment(analysis.ranked_app.app.app_root)
    analysis.enrichment = enrichment
    analysis.ranked_app.home_url = enrichment.get("home_url")
    analysis.ranked_app.blogname = enrichment.get("blogname")


def analyze_backend(app: AppPaths, start: datetime, end: datetime) -> dict:
    paths = Counter()
    ips = Counter()
    ip_paths = Counter()
    statuses = Counter()
    sensitive = Counter()
    internal_requests = 0
    bot_requests = 0
    backend_5xx_count = 0
    total = 0
    for path in app.backend_access_logs:
        for line in read_lines(path):
            record = parse_combined_access(line)
            if not record or not (start <= record.timestamp < end):
                continue
            total += 1
            paths[record.path] += 1
            ips[record.ip] += 1
            ip_paths[(record.ip, record.path)] += 1
            statuses[record.status] += 1
            if 500 <= record.status <= 599:
                backend_5xx_count += 1
            if is_internal_ip(record.ip):
                internal_requests += 1
            if is_bot_user_agent(record.user_agent):
                bot_requests += 1
            for endpoint in SENSITIVE_ENDPOINTS:
                if endpoint in record.path:
                    sensitive[endpoint] += 1
    top_ip_share = round((ips.most_common(1)[0][1] / total) * 100, 2) if total and ips else 0.0
    return {
        "total_requests": total,
        "top_paths": paths.most_common(10),
        "top_ips": ips.most_common(10),
        "top_ip_paths": [({"ip": ip, "path": path}, count) for (ip, path), count in ip_paths.most_common(10)],
        "top_status_codes": statuses.most_common(),
        "sensitive_endpoints": sensitive.most_common(),
        "top_ip_share_pct": top_ip_share,
        "internal_requests": internal_requests,
        "bot_requests": bot_requests,
        "backend_5xx_count": backend_5xx_count,
    }


def analyze_static(app: AppPaths, start: datetime, end: datetime) -> dict:
    suspicious_paths = Counter()
    user_agents = Counter()
    asset_paths = Counter()
    total = 0
    for path in app.static_access_logs:
        for line in read_lines(path):
            record = parse_combined_access(line)
            if not record or not (start <= record.timestamp < end):
                continue
            total += 1
            if any(marker in record.path for marker in (".well-known", ".php", "xmlrpc", "wp-")):
                suspicious_paths[record.path] += 1
            if record.path.endswith((".css", ".js", ".jpg", ".png", ".webp", ".woff", ".svg")):
                asset_paths[record.path] += 1
            if record.user_agent:
                user_agents[record.user_agent] += 1
    return {
        "total_requests": total,
        "top_suspicious_paths": suspicious_paths.most_common(10),
        "top_user_agents": user_agents.most_common(10),
        "top_assets": asset_paths.most_common(10),
        "bot_requests": sum(count for ua, count in user_agents.items() if is_bot_user_agent(ua)),
    }


def analyze_php_access(app: AppPaths, start: datetime, end: datetime) -> dict:
    targets = Counter()
    latency_buckets = Counter()
    memory_buckets = Counter()
    expensive_samples = []
    costly_samples = []
    durations = []
    memories = []
    php_5xx_count = 0
    for path in app.php_access_logs:
        for line in read_lines(path):
            record = parse_php_app_access(line)
            if not record or not (start <= record.timestamp < end):
                continue
            if 500 <= record.status <= 599:
                php_5xx_count += 1
            final_target = record.final_request_target or record.script_target
            targets[final_target] += 1
            if record.duration_sec is not None:
                durations.append(record.duration_sec)
                latency_buckets[latency_bucket(record.duration_sec)] += 1
            if record.memory_bytes is not None:
                memories.append(record.memory_bytes)
                memory_buckets[memory_bucket(record.memory_bytes)] += 1
            score = (
                (record.duration_sec or 0.0),
                (record.memory_bytes or 0),
            )
            expensive_samples.append((score, record))
            if (record.duration_sec is not None and record.duration_sec >= 2.0) or (
                record.memory_bytes is not None and record.memory_bytes >= 256 * 1024 * 1024
            ):
                costly_samples.append((score, record))
    expensive_samples.sort(key=lambda item: item[0], reverse=True)
    costly_samples.sort(key=lambda item: item[0], reverse=True)
    return {
        "total_requests": sum(targets.values()),
        "top_final_targets": targets.most_common(10),
        "latency_buckets": latency_buckets.most_common(),
        "memory_buckets": memory_buckets.most_common(),
        "avg_latency_sec": round(sum(durations) / len(durations), 3) if durations else None,
        "p95_latency_sec": percentile(durations, 95),
        "avg_memory_mb": round((sum(memories) / len(memories)) / (1024 * 1024), 2) if memories else None,
        "costly_request_count": len(costly_samples),
        "php_5xx_count": php_5xx_count,
        "expensive_samples": [
            {
                "timestamp": sample.timestamp.isoformat(),
                "target": sample.final_request_target or sample.script_target,
                "status": sample.status,
                "duration_sec": sample.duration_sec,
                "memory_mb": round(sample.memory_bytes / (1024 * 1024), 2) if sample.memory_bytes else None,
                "cpu_pct": sample.cpu_pct,
                "io_pct": sample.io_pct,
            }
            for _, sample in expensive_samples[:10]
        ],
        "costly_samples": [
            {
                "timestamp": sample.timestamp.isoformat(),
                "target": sample.final_request_target or sample.script_target,
                "status": sample.status,
                "duration_sec": sample.duration_sec,
                "memory_mb": round(sample.memory_bytes / (1024 * 1024), 2) if sample.memory_bytes else None,
                "cpu_pct": sample.cpu_pct,
                "io_pct": sample.io_pct,
            }
            for _, sample in costly_samples[:10]
        ],
    }


def analyze_wp_cron(app: AppPaths) -> dict:
    run_count = 0
    events = Counter()
    warnings = Counter()
    slowest = []
    current_timestamp = None
    tracked_markers = (
        "action_scheduler_run_queue",
        "objectcache_metrics_snapshot",
        "objectcache",
        "broken-link-checker",
        "rsssl",
        "seopress",
        "breeze",
    )
    marker_hits = Counter()
    for path in app.wp_cron_logs:
        for line in read_lines(path):
            record = parse_cron_line(line, current_timestamp)
            if not record:
                continue
            if record.kind == "run":
                run_count += 1
                current_timestamp = record.timestamp
            elif record.kind == "event" and record.event_name:
                events[record.event_name] += 1
                slowest.append((record.duration_sec or 0.0, record.event_name, record.timestamp))
                for marker in tracked_markers:
                    if marker in record.event_name:
                        marker_hits[marker] += 1
            elif record.kind in {"message", "summary"} and record.message:
                lowered = record.message.lower()
                if "warning" in lowered or "notice" in lowered:
                    warnings[record.message] += 1
                for marker in tracked_markers:
                    if marker in lowered:
                        marker_hits[marker] += 1
    slowest.sort(reverse=True)
    return {
        "run_count": run_count,
        "top_events": events.most_common(10),
        "slowest_events": [
            {"event_name": event, "duration_sec": duration, "timestamp": ts.isoformat() if ts else None}
            for duration, event, ts in slowest[:10]
        ],
        "warnings": warnings.most_common(10),
        "tracked_marker_hits": marker_hits.most_common(),
    }


def analyze_php_slow(app: AppPaths, start: datetime, end: datetime) -> dict:
    slow_plugins = Counter()
    slow_paths = Counter()
    plugin_paths = Counter()
    slow_signatures = Counter()
    plugin_combinations = Counter()
    category_hits = Counter()
    entrypoint_signals = Counter()
    sample_events = []
    event_count = 0

    for path in app.php_slow_logs:
        events = parse_slow_log_blocks(read_lines(path))
        for event in events:
            if event.timestamp and not (start <= event.timestamp < end):
                continue
            event_count += 1
            if event.script_filename:
                slow_paths[event.script_filename] += 1
                for signal in detect_slowlog_entrypoint_signals(event.script_filename):
                    entrypoint_signals[signal] += 1
            for slug in event.plugin_slugs:
                slow_plugins[slug] += 1
            for plugin_path in event.plugin_paths:
                plugin_paths[plugin_path] += 1
            if event.plugin_slugs:
                plugin_combinations[",".join(event.plugin_slugs)] += 1
            slow_signatures[event.signature] += 1
            effective_categories = event.functional_categories or detect_functional_categories(event.signature)
            for category in effective_categories:
                category_hits[category] += 1
            sample_events.append(
                {
                    "timestamp": event.timestamp.isoformat() if event.timestamp else None,
                    "pool": event.pool,
                    "pid": event.pid,
                    "script_filename": event.script_filename,
                    "plugin_slugs": event.plugin_slugs,
                    "functional_categories": effective_categories,
                    "signature": event.signature,
                }
            )

    return {
        "slow_event_count": event_count,
        "top_slow_plugins": slow_plugins.most_common(10),
        "top_slow_paths": slow_paths.most_common(10),
        "top_plugin_paths": plugin_paths.most_common(10),
        "top_slow_signatures": slow_signatures.most_common(10),
        "top_slow_plugin_combinations": plugin_combinations.most_common(10),
        "functional_categories": category_hits.most_common(),
        "entrypoint_signals": entrypoint_signals.most_common(),
        "sample_events": sample_events[:10],
    }


def analyze_backend_errors(app: AppPaths, start: datetime, end: datetime) -> dict:
    signatures = Counter()
    files = Counter()
    severities = Counter()
    total = 0
    timestamped_events = 0
    untimestamped_events = 0
    skipped_out_of_window = 0
    for path in app.backend_error_logs:
        for line in read_lines(path):
            record = parse_error_line(line)
            if not record:
                continue
            if record.timestamp is not None:
                if not (start <= record.timestamp < end):
                    skipped_out_of_window += 1
                    continue
                timestamped_events += 1
            else:
                # Legacy backend lines often have no parseable timestamp; keep them visible but track the limitation.
                untimestamped_events += 1
            total += 1
            signatures[record.signature] += 1
            if record.file_hint:
                files[record.file_hint] += 1
            if record.severity:
                severities[record.severity] += 1
    return {
        "total_events": total,
        "timestamped_events": timestamped_events,
        "untimestamped_events": untimestamped_events,
        "skipped_out_of_window": skipped_out_of_window,
        "top_signatures": signatures.most_common(10),
        "top_files": files.most_common(10),
        "top_severities": severities.most_common(),
    }


def classify_app(backend: dict, static: dict, php: dict, php_slow: dict, cron: dict) -> list[str]:
    categories: list[str] = []
    backend_total = backend.get("total_requests", 0)
    bot_requests = backend.get("bot_requests", 0) + static.get("bot_requests", 0)
    suspicious_static = sum(count for _, count in static.get("top_suspicious_paths", []))
    top_ip_share = backend.get("top_ip_share_pct", 0)
    php_p95 = php.get("p95_latency_sec")
    php_avg = php.get("avg_latency_sec")
    php_total = php.get("total_requests", 0)
    costly_request_count = php.get("costly_request_count", 0)
    slow_event_count = php_slow.get("slow_event_count", 0)
    cron_run_count = cron.get("run_count", 0)

    if backend_total >= 500:
        categories.append("high traffic")
    if (
        (php_p95 is not None and php_p95 >= 2.0)
        or (php_avg is not None and php_avg >= 1.5 and php_total >= 5)
        or costly_request_count >= 3
    ):
        categories.append("high PHP cost")
    if cron_run_count >= 10 or cron.get("tracked_marker_hits"):
        categories.append("cron-heavy")
    if backend.get("internal_requests", 0) > 0 and backend.get("total_requests", 0) > 0:
        share = backend["internal_requests"] / backend["total_requests"]
        if share >= 0.3:
            categories.append("internal-traffic dominated")
    if bot_requests >= 50 or suspicious_static >= 20 or (backend_total >= 20 and top_ip_share >= 80):
        categories.append("bot/probe heavy")
    if any(marker.startswith("objectcache") for marker, _ in cron.get("tracked_marker_hits", [])) or any(
        category == "cache" for category, _ in php_slow.get("functional_categories", [])
    ):
        categories.append("cache churn suspected")
    if (
        (cron_run_count >= 5)
        or backend.get("sensitive_endpoints")
        or any(category == "scheduler" for category, _ in php_slow.get("functional_categories", []))
    ):
        categories.append("high internal workload")
    if slow_event_count >= 3 or any(count >= 2 for _, count in php_slow.get("top_slow_signatures", [])):
        categories.append("slow-cost heavy")
    return categories or ["needs manual review"]


def add_error_heavy_label(categories: list[str], error_summary: dict) -> list[str]:
    if (
        error_summary.get("total_events", 0) >= 10
        or any(count >= 5 for _, count in error_summary.get("top_signatures", []))
        or any(count >= 5 for _, count in error_summary.get("top_files", []))
    ):
        if "error-heavy" not in categories:
            categories.append("error-heavy")
    return categories


def compute_priority(
    categories: list[str],
    backend: dict,
    php: dict,
    php_slow: dict,
    cron: dict,
    error_summary: dict,
    suspicion_score: int,
) -> str:
    php_p95 = php.get("p95_latency_sec") or 0.0
    php_total = php.get("total_requests", 0)
    costly_request_count = php.get("costly_request_count", 0)
    slow_event_count = php_slow.get("slow_event_count", 0)
    admin_ajax_hits = 0
    for endpoint, count in backend.get("sensitive_endpoints", []):
        if endpoint == "admin-ajax.php":
            admin_ajax_hits = count
            break

    strong_php_evidence = (
        "slow-cost heavy" in categories
        or slow_event_count >= 3
        or costly_request_count >= 3
        or (php_p95 >= 2.5 and php_total >= 5)
        or (admin_ajax_hits >= 20 and (costly_request_count >= 2 or php_p95 >= 2.0 or slow_event_count >= 1))
    )
    moderate_pressure = (
        "bot/probe heavy" in categories
        or "error-heavy" in categories
        or "cron-heavy" in categories
        or "cache churn suspected" in categories
        or error_summary.get("total_events", 0) >= 5
        or cron.get("run_count", 0) >= 5
    )

    if strong_php_evidence and suspicion_score >= 60:
        return "ALTA"
    if moderate_pressure:
        return "MEDIA"
    return "BASSA"


def compute_suspicion_score(
    categories: list[str],
    backend: dict,
    static: dict,
    php: dict,
    php_slow: dict,
    cron: dict,
    error_summary: dict,
) -> int:
    score = 0
    backend_total = backend.get("total_requests", 0)
    php_p95 = php.get("p95_latency_sec") or 0.0
    costly_request_count = php.get("costly_request_count", 0)
    slow_event_count = php_slow.get("slow_event_count", 0)
    cron_runs = cron.get("run_count", 0)
    error_total = error_summary.get("total_events", 0)
    bot_requests = backend.get("bot_requests", 0) + static.get("bot_requests", 0)

    score += min(backend_total // 25, 12)
    if php_p95 >= 2.5:
        score += 24
    elif php_p95 >= 2.0:
        score += 18
    elif php_p95 >= 1.5:
        score += 10
    score += min(costly_request_count * 8, 24)
    score += min(slow_event_count * 10, 30)
    score += min(cron_runs, 10)
    score += min(error_total, 10)
    score += min(bot_requests // 10, 8)
    if "cache churn suspected" in categories:
        score += 8
    if "error-heavy" in categories:
        score += 8
    if "bot/probe heavy" in categories:
        score += 8
    return score


def analysis_sort_key(analysis: AppAnalysis) -> tuple:
    priority_rank = {"ALTA": 0, "MEDIA": 1, "BASSA": 2}
    return (
        -analysis.suspicion_score,
        priority_rank.get(analysis.priority, 9),
        -analysis.ranked_app.request_count,
        analysis.ranked_app.app.app_id,
    )


def build_actionable_warnings(analyses: list[AppAnalysis], snapshot) -> list[str]:
    warnings: list[str] = []
    if snapshot.load_averages[0] >= 4.0:
        cpu_count = snapshot.cpu_count or 1
        load_per_core = snapshot.load_averages[0] / cpu_count
        warnings.append(
            f"Load average is elevated at {snapshot.load_averages[0]:.2f} "
            f"(CPU cores={cpu_count}, load/core={load_per_core:.2f}; scale: ~1.00/core busy, >1.50/core high)."
        )
    if snapshot.swap_used_mb and snapshot.swap_used_mb > 128:
        warnings.append(f"Swap usage is non-trivial at {snapshot.swap_used_mb:.1f} MB.")
    for analysis in analyses:
        app_id = analysis.ranked_app.app.app_id
        if "high PHP cost" in analysis.categories:
            warnings.append(f"{app_id}: PHP access log shows elevated latency or memory pressure.")
        if "cron-heavy" in analysis.categories:
            warnings.append(f"{app_id}: cron activity is unusually visible in wp-cron.log.")
        if analysis.php_slow_summary.get("slow_event_count", 0) > 0:
            warnings.append(f"{app_id}: php-app.slow.log captured {analysis.php_slow_summary['slow_event_count']} slow events.")
        if analysis.error_summary["top_signatures"]:
            signature, count = analysis.error_summary["top_signatures"][0]
            if count >= 10:
                warnings.append(f"{app_id}: repeated backend warnings/errors concentrated on '{signature[:80]}'.")
    return warnings


def latency_bucket(duration: float) -> str:
    if duration < 0.5:
        return "<0.5s"
    if duration < 1.0:
        return "0.5-1s"
    if duration < 2.0:
        return "1-2s"
    if duration < 5.0:
        return "2-5s"
    return ">=5s"


def memory_bucket(memory_bytes: int) -> str:
    mb = memory_bytes / (1024 * 1024)
    if mb < 64:
        return "<64MB"
    if mb < 128:
        return "64-128MB"
    if mb < 256:
        return "128-256MB"
    return ">=256MB"


def percentile(values: list[float], pct: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = int(round((pct / 100) * (len(ordered) - 1)))
    return round(ordered[index], 3)


def detect_slowlog_entrypoint_signals(path: str) -> list[str]:
    lowered = path.lower()
    signals: list[str] = []
    if "wp-cron.php" in lowered:
        signals.append("wp-cron.php")
    if "admin-ajax.php" in lowered:
        signals.append("admin-ajax.php")
    if "/wp-json/" in lowered or "rest_route=" in lowered:
        signals.append("REST API")
    if "/wp-content/plugins/" in lowered:
        signals.append("plugin-path")
    return signals


def read_lines(path: Path):
    try:
        opener = _open_text_file
        with opener(path) as handle:
            yield from handle
    except OSError:
        return


def _open_text_file(path: Path):
    if path.suffix == ".gz":
        import gzip

        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")
