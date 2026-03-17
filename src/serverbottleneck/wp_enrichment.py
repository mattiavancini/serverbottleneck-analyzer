from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def collect_wp_enrichment(app_root: Path, timeout: int = 10) -> dict:
    wp_binary = shutil.which("wp")
    if not wp_binary:
        return {
            "home_url": None,
            "blogname": None,
            "plugins": [],
            "cron_events": [],
            "cron_top_hooks": [],
            "cron_signal_strength": "UNAVAILABLE",
            "cron_suspected_sources": [],
            "action_scheduler_detected": False,
            "action_scheduler_pending": 0,
            "action_scheduler_failed": 0,
            "action_scheduler_old_pending": 0,
            "action_scheduler_top_hooks": [],
        }
    public_html = app_root / "public_html"
    if not public_html.is_dir():
        public_html = app_root
    plugins = _run_wp_lines(wp_binary, public_html, ["plugin", "list", "--status=active", "--field=name"], timeout)
    cron_events = _run_wp_json(
        wp_binary,
        public_html,
        ["cron", "event", "list", "--fields=hook,next_run_gmt", "--format=json"],
        timeout,
    )
    cron_summary = _summarize_cron_events(cron_events, plugins)
    action_scheduler = _collect_action_scheduler(wp_binary, public_html, plugins, timeout)
    return {
        "home_url": _run_wp(wp_binary, public_html, ["option", "get", "home"], timeout),
        "blogname": _run_wp(wp_binary, public_html, ["option", "get", "blogname"], timeout),
        "plugins": plugins,
        "cron_events": [item["hook"] for item in cron_events if item.get("hook")],
        "cron_top_hooks": cron_summary["cron_top_hooks"],
        "cron_signal_strength": cron_summary["cron_signal_strength"],
        "cron_suspected_sources": cron_summary["cron_suspected_sources"],
        "cron_due_now": cron_summary["cron_due_now"],
        "cron_unique_hooks": cron_summary["cron_unique_hooks"],
        "cron_total_events": cron_summary["cron_total_events"],
        "action_scheduler_detected": action_scheduler["action_scheduler_detected"],
        "action_scheduler_pending": action_scheduler["action_scheduler_pending"],
        "action_scheduler_failed": action_scheduler["action_scheduler_failed"],
        "action_scheduler_old_pending": action_scheduler["action_scheduler_old_pending"],
        "action_scheduler_top_hooks": action_scheduler["action_scheduler_top_hooks"],
    }


def fixture_wp_enrichment() -> dict:
    return {
        "home_url": None,
        "blogname": None,
        "plugins": [],
        "cron_events": [],
        "cron_top_hooks": [],
        "cron_signal_strength": "UNAVAILABLE",
        "cron_suspected_sources": [],
        "cron_due_now": 0,
        "cron_unique_hooks": 0,
        "cron_total_events": 0,
        "action_scheduler_detected": False,
        "action_scheduler_pending": 0,
        "action_scheduler_failed": 0,
        "action_scheduler_old_pending": 0,
        "action_scheduler_top_hooks": [],
        "mode": "fixture-skipped",
    }


def _run_wp(wp_binary: str, cwd: Path, args: list[str], timeout: int) -> str | None:
    try:
        proc = subprocess.run(
            [wp_binary, *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = proc.stdout.strip()
    return output or None


def _run_wp_lines(wp_binary: str, cwd: Path, args: list[str], timeout: int) -> list[str]:
    value = _run_wp(wp_binary, cwd, args, timeout)
    if not value:
        return []
    return [line.strip() for line in value.splitlines() if line.strip()]


def _run_wp_json(wp_binary: str, cwd: Path, args: list[str], timeout: int) -> list[dict]:
    value = _run_wp(wp_binary, cwd, args, timeout)
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _collect_action_scheduler(wp_binary: str, cwd: Path, plugins: list[str], timeout: int) -> dict:
    tables = set(_run_wp_lines(wp_binary, cwd, ["db", "tables", "--all-tables-with-prefix"], timeout))
    action_table = next((table for table in tables if table.endswith("actionscheduler_actions")), None)
    detected = "woocommerce" in plugins or "action-scheduler" in plugins or action_table is not None
    if not action_table:
        return {
            "action_scheduler_detected": detected,
            "action_scheduler_pending": 0,
            "action_scheduler_failed": 0,
            "action_scheduler_old_pending": 0,
            "action_scheduler_top_hooks": [],
        }

    pending = _run_wp_sql_int(
        wp_binary,
        cwd,
        f"SELECT COUNT(*) FROM {action_table} WHERE status = 'pending';",
        timeout,
    )
    failed = _run_wp_sql_int(
        wp_binary,
        cwd,
        f"SELECT COUNT(*) FROM {action_table} WHERE status = 'failed';",
        timeout,
    )
    old_pending = _run_wp_sql_int(
        wp_binary,
        cwd,
        (
            f"SELECT COUNT(*) FROM {action_table} "
            "WHERE status = 'pending' "
            "AND scheduled_date_gmt IS NOT NULL "
            "AND scheduled_date_gmt < (UTC_TIMESTAMP() - INTERVAL 1 HOUR);"
        ),
        timeout,
    )
    top_hooks_rows = _run_wp_sql_rows(
        wp_binary,
        cwd,
        (
            f"SELECT hook, COUNT(*) AS total FROM {action_table} "
            "WHERE status = 'pending' GROUP BY hook ORDER BY total DESC LIMIT 10;"
        ),
        timeout,
    )
    return {
        "action_scheduler_detected": detected,
        "action_scheduler_pending": pending or 0,
        "action_scheduler_failed": failed or 0,
        "action_scheduler_old_pending": old_pending or 0,
        "action_scheduler_top_hooks": _rows_to_named_counts(top_hooks_rows, "hook"),
    }


def _summarize_cron_events(events: list[dict], plugins: list[str]) -> dict:
    hooks = Counter()
    due_now = 0
    suspected_sources = Counter()
    now = datetime.now(timezone.utc)
    for event in events:
        hook = (event.get("hook") or "").strip()
        if not hook:
            continue
        hooks[hook] += 1
        next_run_gmt = _parse_wp_datetime(event.get("next_run_gmt"))
        if next_run_gmt and next_run_gmt <= now:
            due_now += 1
        for source in _detect_sources_from_text(hook, plugins):
            suspected_sources[source] += 1

    top_hooks = [
        {"hook": hook, "count": count}
        for hook, count in hooks.most_common(10)
    ]
    strongest = top_hooks[0]["count"] if top_hooks else 0
    if due_now >= 20 or strongest >= 10:
        signal_strength = "HIGH"
    elif due_now >= 5 or strongest >= 4 or len(events) >= 20:
        signal_strength = "MEDIUM"
    elif top_hooks:
        signal_strength = "LOW"
    else:
        signal_strength = "UNAVAILABLE"

    return {
        "cron_top_hooks": top_hooks,
        "cron_signal_strength": signal_strength,
        "cron_suspected_sources": [
            {"source": source, "count": count}
            for source, count in suspected_sources.most_common(10)
        ],
        "cron_due_now": due_now,
        "cron_unique_hooks": len(hooks),
        "cron_total_events": sum(hooks.values()),
    }


def _detect_sources_from_text(text: str, plugins: list[str]) -> list[str]:
    lowered = text.lower()
    sources: list[str] = []
    if "action_scheduler" in lowered or "action-scheduler" in lowered:
        sources.append("action-scheduler")
    if lowered.startswith("wp_") or lowered in CORE_CRON_HOOKS:
        sources.append("wordpress-core")
    for plugin in plugins:
        normalized = plugin.lower().replace("-", "_")
        if plugin.lower() in lowered or normalized in lowered:
            sources.append(plugin)
    if not sources:
        token_match = re.match(r"([a-z0-9]+(?:[_-][a-z0-9]+)+)", lowered)
        if token_match:
            sources.append(token_match.group(1))
    return sorted(set(sources))


def _run_wp_sql_int(wp_binary: str, cwd: Path, query: str, timeout: int) -> int | None:
    value = _run_wp(
        wp_binary,
        cwd,
        ["db", "query", query, "--skip-column-names"],
        timeout,
    )
    if not value:
        return None
    try:
        return int(value.splitlines()[0].strip())
    except (ValueError, IndexError):
        return None


def _run_wp_sql_rows(wp_binary: str, cwd: Path, query: str, timeout: int) -> list[dict[str, str]]:
    value = _run_wp(
        wp_binary,
        cwd,
        ["db", "query", query, "--skip-column-names"],
        timeout,
    )
    if not value:
        return []
    rows = []
    for line in value.splitlines():
        parts = line.strip().split("\t")
        if len(parts) < 2:
            continue
        rows.append({"name": parts[0], "count": parts[1]})
    return rows


def _rows_to_named_counts(rows: list[dict[str, str]], name_key: str) -> list[dict]:
    payload = []
    for row in rows:
        try:
            count = int(row["count"])
        except (KeyError, ValueError):
            continue
        payload.append({name_key: row.get("name"), "count": count})
    return payload


def _parse_wp_datetime(value: str | None) -> datetime | None:
    if not value or value in {"0000-00-00 00:00:00", "N/A"}:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S %z"):
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


CORE_CRON_HOOKS = {
    "wp_version_check",
    "wp_update_plugins",
    "wp_update_themes",
    "wp_privacy_delete_old_export_files",
    "do_pings",
    "recovery_mode_clean_expired_keys",
}
