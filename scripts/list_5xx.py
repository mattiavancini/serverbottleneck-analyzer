#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlsplit


PLUGIN_PATH_RE = re.compile(r"/(?:wp-content/)?plugins/(?P<slug>[^/\s'\"]+)", re.IGNORECASE)
WP_JSON_RE = re.compile(r"/wp-json/", re.IGNORECASE)
CORE_REST_NAMESPACES = {"wp", "oembed"}


@dataclass
class ReportRun:
    path: Path
    payload: dict[str, Any]
    generated_at: datetime


@dataclass
class Event5xx:
    timestamp: datetime
    server: str
    app: str
    status: int | str
    source: str
    url: str | None
    endpoint: str | None
    plugin: str | None
    count: int = 1
    aggregate: bool = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List only 5xx events from inspection JSON reports")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Directory that contains <server>/<YYYY-MM-DD>/inspection-*.json reports",
    )
    parser.add_argument("--server", help="Optional server name filter, for example wp-x")
    parser.add_argument("--last", type=int, default=10, help="Number of latest inspection reports to scan")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir.expanduser()
    runs, skipped = load_report_runs(data_dir, args.server)
    runs.sort(key=lambda run: (run.generated_at, str(run.path)))
    selected = select_last_runs(runs, args.last)

    events: list[Event5xx] = []
    for run in selected:
        events.extend(extract_5xx_events(run))
    events.sort(key=lambda event: (event.timestamp, event.server, event.app, event.source, str(event.status), event.url or ""))

    print_events(events)
    print_summary(events)
    print_skipped(skipped)
    return 0


def load_report_runs(data_dir: Path, server: str | None) -> tuple[list[ReportRun], list[tuple[Path, str]]]:
    if server and (data_dir / server).exists():
        base = data_dir / server
    else:
        base = data_dir

    if not base.exists():
        return [], [(base, "directory not found")]

    runs: list[ReportRun] = []
    skipped: list[tuple[Path, str]] = []
    for path in sorted(base.rglob("inspection-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            skipped.append((path, str(exc)))
            continue

        payload_server = str(payload.get("server_name") or "")
        if server and payload_server and payload_server != server:
            continue
        if server and not payload_server and server not in path.parts:
            continue

        generated_at = parse_datetime(payload.get("generated_at_utc")) or file_mtime(path)
        runs.append(ReportRun(path=path, payload=payload, generated_at=generated_at))
    return runs, skipped


def select_last_runs(runs: list[ReportRun], last: int) -> list[ReportRun]:
    if last <= 0:
        return []
    return runs[-last:]


def extract_5xx_events(run: ReportRun) -> list[Event5xx]:
    payload = run.payload
    server = str(payload.get("server_name") or "unknown")
    fallback_timestamp = report_event_timestamp(payload, run.generated_at)
    events: list[Event5xx] = []

    for app in collect_app_payloads(payload):
        app_id = str(app.get("app_id") or app.get("display_name") or "unknown")
        plugin_hint = context_plugin_hint(app)

        backend_raw = extract_raw_events(run, app, "backend", fallback_timestamp, plugin_hint)
        php_raw = extract_raw_events(run, app, "php", fallback_timestamp, plugin_hint)
        events.extend(backend_raw)
        events.extend(php_raw)

        backend_count = source_5xx_count(app, "backend")
        php_count = source_5xx_count(app, "php")

        events.extend(
            build_aggregate_events(
                server=server,
                app=app_id,
                source="backend",
                total_count=backend_count,
                known_raw_count=sum(event.count for event in backend_raw),
                status_counts=backend_status_counts(app),
                timestamp=fallback_timestamp,
                plugin_hint=plugin_hint,
            )
        )
        events.extend(
            build_aggregate_events(
                server=server,
                app=app_id,
                source="php",
                total_count=php_count,
                known_raw_count=sum(event.count for event in php_raw),
                status_counts=php_status_counts(app),
                timestamp=fallback_timestamp,
                plugin_hint=plugin_hint,
            )
        )

    return events


def collect_app_payloads(payload: dict[str, Any]) -> list[dict[str, Any]]:
    by_app: dict[str, dict[str, Any]] = {}
    for app in iterable_dicts(payload.get("top_suspect_apps")):
        app_id = str(app.get("app_id") or "")
        if app_id:
            by_app[app_id] = app
    for app in iterable_dicts(payload.get("app_details")):
        app_id = str(app.get("app_id") or "")
        if app_id:
            by_app[app_id] = app
    debug = payload.get("debug") if isinstance(payload.get("debug"), dict) else {}
    for app in iterable_dicts(debug.get("app_details_verbose")):
        app_id = str(app.get("app_id") or "")
        if app_id:
            by_app[app_id] = app
    return list(by_app.values())


def extract_raw_events(
    run: ReportRun,
    app: dict[str, Any],
    source: str,
    fallback_timestamp: datetime,
    plugin_hint: str | None,
) -> list[Event5xx]:
    server = str(run.payload.get("server_name") or "unknown")
    app_id = str(app.get("app_id") or app.get("display_name") or "unknown")
    debug = app.get("debug") if isinstance(app.get("debug"), dict) else {}
    source_payload = debug.get("backend") if source == "backend" else debug.get("php_access")

    records: list[dict[str, Any]] = []
    if isinstance(source_payload, dict):
        records.extend(find_event_shaped_5xx_records(source_payload))

    seen: set[tuple[str, str, str, str]] = set()
    events: list[Event5xx] = []
    for record in records:
        status = get_status(record)
        if status is None:
            continue
        url = pick_url(record)
        timestamp = parse_datetime(first_present(record, ("timestamp", "timestamp_utc", "time", "date"))) or fallback_timestamp
        endpoint = detect_rest_endpoint(url)
        plugin = detect_plugin(record, url, endpoint) or plugin_hint
        key = (fmt_dt(timestamp), source, str(status), url or "")
        if key in seen:
            continue
        seen.add(key)
        events.append(
            Event5xx(
                timestamp=timestamp,
                server=server,
                app=app_id,
                status=status,
                source=source,
                url=url,
                endpoint=endpoint,
                plugin=plugin,
            )
        )
    return events


def find_event_shaped_5xx_records(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(value, dict):
        status = get_status(value)
        if status is not None and 500 <= status <= 599 and has_event_shape(value):
            records.append(value)
        for key, child in value.items():
            if key in {"top_status_codes", "latency_buckets", "memory_buckets"}:
                continue
            records.extend(find_event_shaped_5xx_records(child))
    elif isinstance(value, list):
        for child in value:
            records.extend(find_event_shaped_5xx_records(child))
    return records


def has_event_shape(record: dict[str, Any]) -> bool:
    url_keys = ("url", "path", "target", "request", "request_target", "final_target", "final_request_target", "script_target", "uri")
    time_keys = ("timestamp", "timestamp_utc", "time", "date")
    return any(record.get(key) for key in url_keys) or any(record.get(key) for key in time_keys)


def build_aggregate_events(
    server: str,
    app: str,
    source: str,
    total_count: int,
    known_raw_count: int,
    status_counts: list[tuple[int, int]],
    timestamp: datetime,
    plugin_hint: str | None,
) -> list[Event5xx]:
    remaining = max(0, total_count - known_raw_count)
    if remaining <= 0:
        return []

    events: list[Event5xx] = []
    allocated = 0
    for status, count in status_counts:
        if count <= 0:
            continue
        use_count = min(count, remaining - allocated)
        if use_count <= 0:
            break
        events.append(
            Event5xx(
                timestamp=timestamp,
                server=server,
                app=app,
                status=status,
                source=source,
                url=None,
                endpoint=None,
                plugin=plugin_hint,
                count=use_count,
                aggregate=True,
            )
        )
        allocated += use_count

    unknown_count = remaining - allocated
    if unknown_count > 0:
        events.append(
            Event5xx(
                timestamp=timestamp,
                server=server,
                app=app,
                status="5xx",
                source=source,
                url=None,
                endpoint=None,
                plugin=plugin_hint,
                count=unknown_count,
                aggregate=True,
            )
        )
    return events


def source_5xx_count(app: dict[str, Any], source: str) -> int:
    summary = app.get("summary") if isinstance(app.get("summary"), dict) else {}
    debug = app.get("debug") if isinstance(app.get("debug"), dict) else {}
    if source == "backend":
        candidates = [
            app.get("backend_5xx_count"),
            summary.get("backend_5xx_count"),
            nested_get(debug, ("backend", "backend_5xx_count")),
        ]
    else:
        candidates = [
            app.get("php_5xx_count"),
            summary.get("php_5xx_count"),
            nested_get(debug, ("php_access", "php_5xx_count")),
        ]
    return max(int_or_zero(value) for value in candidates)


def backend_status_counts(app: dict[str, Any]) -> list[tuple[int, int]]:
    debug = app.get("debug") if isinstance(app.get("debug"), dict) else {}
    return extract_5xx_status_counts(nested_get(debug, ("backend", "top_status_codes")))


def php_status_counts(app: dict[str, Any]) -> list[tuple[int, int]]:
    debug = app.get("debug") if isinstance(app.get("debug"), dict) else {}
    return extract_5xx_status_counts(nested_get(debug, ("php_access", "top_status_codes")))


def extract_5xx_status_counts(items: Any) -> list[tuple[int, int]]:
    counts: list[tuple[int, int]] = []
    for item in iterable_dicts(items):
        status = int_or_none(first_present(item, ("status_code", "status")))
        count = int_or_zero(item.get("count"))
        if status is not None and 500 <= status <= 599 and count > 0:
            counts.append((status, count))
    return counts


def context_plugin_hint(app: dict[str, Any]) -> str | None:
    for item in iterable_dicts(app.get("slowlog_suspected_plugins")):
        plugin = item.get("plugin")
        if isinstance(plugin, str) and plugin:
            return plugin

    signals = app.get("signals") if isinstance(app.get("signals"), dict) else {}
    top_slow_plugin = signals.get("top_slow_plugin") if isinstance(signals.get("top_slow_plugin"), dict) else {}
    plugin = top_slow_plugin.get("value")
    if isinstance(plugin, str) and plugin:
        return plugin

    debug = app.get("debug") if isinstance(app.get("debug"), dict) else {}
    php_slow = debug.get("php_slow") if isinstance(debug.get("php_slow"), dict) else {}

    for item in iterable_dicts(php_slow.get("sample_events")):
        slugs = item.get("plugin_slugs")
        if isinstance(slugs, list) and slugs:
            return str(slugs[0])

    for item in iterable_dicts(php_slow.get("top_plugin_paths")):
        plugin = plugin_from_text(item.get("path"))
        if plugin:
            return plugin

    for item in iterable_dicts(php_slow.get("top_slow_plugin_combinations")):
        combo = item.get("plugin_combination")
        if isinstance(combo, str) and combo:
            return combo.split(",")[0].strip() or None

    backend_errors = debug.get("backend_errors") if isinstance(debug.get("backend_errors"), dict) else {}
    for key in ("top_files", "top_signatures"):
        for item in iterable_dicts(backend_errors.get(key)):
            plugin = plugin_from_text(item.get("file") or item.get("signature"))
            if plugin:
                return plugin

    return None


def detect_plugin(record: dict[str, Any], url: str | None, endpoint: str | None) -> str | None:
    for value in [url, endpoint, *record.values()]:
        plugin = plugin_from_text(value)
        if plugin:
            return plugin
    return plugin_from_rest_endpoint(endpoint)


def plugin_from_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = PLUGIN_PATH_RE.search(value)
    if not match:
        return None
    return match.group("slug")


def plugin_from_rest_endpoint(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    parts = [part for part in endpoint.strip("/").split("/") if part]
    if not parts:
        return None
    namespace = parts[0]
    if namespace.lower() in CORE_REST_NAMESPACES:
        return None
    return namespace


def detect_rest_endpoint(url: str | None) -> str | None:
    if not url:
        return None
    path, query = split_path_query(url)
    lowered = path.lower()
    marker = "/wp-json/"
    index = lowered.find(marker)
    if index >= 0:
        endpoint = path[index + len("/wp-json") :]
        return endpoint or "/"

    params = parse_qs(query, keep_blank_values=True)
    rest_route = params.get("rest_route")
    if rest_route and rest_route[0]:
        return unquote(rest_route[0])
    return None


def split_path_query(value: str) -> tuple[str, str]:
    candidate = normalize_request_target(value)
    parsed = urlsplit(candidate)
    if parsed.scheme and parsed.netloc:
        return parsed.path or "/", parsed.query
    if "?" in candidate:
        path, query = candidate.split("?", 1)
        return path or "/", query
    return candidate or "/", ""


def pick_url(record: dict[str, Any]) -> str | None:
    for key in ("url", "path", "target", "final_target", "final_request_target", "request_target", "request", "script_target", "uri"):
        value = record.get(key)
        if isinstance(value, str) and value and value != "-":
            return normalize_request_target(value)
    return None


def normalize_request_target(value: str) -> str:
    cleaned = value.strip()
    parts = cleaned.split()
    if len(parts) >= 2 and parts[0].isalpha() and parts[-1].startswith("HTTP/"):
        return parts[1]
    return cleaned


def get_status(record: dict[str, Any]) -> int | None:
    return int_or_none(first_present(record, ("status", "status_code", "http_status", "response_status")))


def first_present(record: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None:
            return value
    return None


def report_event_timestamp(payload: dict[str, Any], generated_at: datetime) -> datetime:
    window = payload.get("analysis_window") if isinstance(payload.get("analysis_window"), dict) else {}
    return parse_datetime(window.get("end_utc")) or generated_at


def print_events(events: list[Event5xx]) -> None:
    if not events:
        print("No 5xx events found.")
        return

    for index, event in enumerate(events):
        if index:
            print("")
        print(f"[{fmt_dt(event.timestamp)}]")
        print(f"server={event.server}")
        print(f"app={event.app}")
        print(f"status={event.status}")
        print(f"source={event.source}")
        if event.aggregate or event.count != 1:
            print(f"count={event.count}")
        print(f"url={highlight_wp_json(event.url) if event.url else '-'}")
        print(f"endpoint={event.endpoint or '-'}")
        print(f"plugin={event.plugin or '-'}")


def print_summary(events: list[Event5xx]) -> None:
    print("")
    print("Riepilogo finale")

    app_counts = Counter()
    endpoint_counts = Counter()
    plugin_counts = Counter()
    for event in events:
        app_counts[f"{event.server}/{event.app}"] += event.count
        if event.endpoint:
            endpoint_counts[event.endpoint] += event.count
        if event.plugin:
            plugin_counts[event.plugin] += event.count

    print("top app con 5xx:")
    print_counter(app_counts)
    print("top endpoint REST:")
    print_counter(endpoint_counts)
    print("top plugin sospetti:")
    print_counter(plugin_counts)


def print_counter(counter: Counter[str], limit: int = 10) -> None:
    if not counter:
        print("- none")
        return
    for key, count in counter.most_common(limit):
        print(f"- {key}: {count}")


def print_skipped(skipped: list[tuple[Path, str]]) -> None:
    if not skipped:
        return
    print(f"warning: skipped {len(skipped)} JSON file(s)", file=sys.stderr)
    for path, reason in skipped[:5]:
        print(f"warning: {path}: {reason}", file=sys.stderr)


def highlight_wp_json(value: str | None) -> str:
    if not value:
        return "-"
    return WP_JSON_RE.sub(">>>/wp-json/<<<", value)


def fmt_dt(value: datetime) -> str:
    utc_value = value.astimezone(timezone.utc)
    if utc_value.second == 0 and utc_value.microsecond == 0:
        return utc_value.strftime("%Y-%m-%dT%H:%MZ")
    return utc_value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def file_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def int_or_zero(value: Any) -> int:
    parsed = int_or_none(value)
    return parsed if parsed is not None else 0


def int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def nested_get(value: Any, keys: Iterable[str]) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def iterable_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


if __name__ == "__main__":
    raise SystemExit(main())
