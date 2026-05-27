#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ReportRun:
    path: Path
    payload: dict[str, Any]
    generated_at: datetime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize Server Bottleneck Analyzer JSON reports")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("~/serverbottleneck/data").expanduser(),
        help="Directory that contains <server>/<YYYY-MM-DD>/inspection-*.json reports",
    )
    parser.add_argument("--server", help="Optional server name filter, for example wp-e")
    parser.add_argument("--last", type=int, default=10, help="Number of latest runs to analyze")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir.expanduser()
    runs, skipped = load_report_runs(data_dir, args.server)
    runs.sort(key=lambda run: (run.generated_at, str(run.path)))
    selected = runs[-max(args.last, 0):] if args.last else []
    print_summary(data_dir, selected, len(runs), skipped)
    return 0


def load_report_runs(data_dir: Path, server: str | None) -> tuple[list[ReportRun], list[tuple[Path, str]]]:
    base = data_dir / server if server else data_dir
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
        generated_at = parse_datetime(payload.get("generated_at_utc")) or file_mtime(path)
        runs.append(ReportRun(path=path, payload=payload, generated_at=generated_at))
    return runs, skipped


def print_summary(data_dir: Path, runs: list[ReportRun], total_available: int, skipped: list[tuple[Path, str]]) -> None:
    print("# Report summary")
    print("")
    print_runs(data_dir, runs, total_available)
    print("")
    print_server_health(runs)
    print("")
    print_recurring_top_apps(runs)
    print("")
    print_apps_with_5xx(runs)
    print("")
    print_apps_with_backend_errors(runs)
    print("")
    print_recurring_slow_plugins(runs)
    print("")
    print_notes(runs, skipped)


def print_runs(data_dir: Path, runs: list[ReportRun], total_available: int) -> None:
    print("## Runs analyzed")
    if not runs:
        print("- Nessuna run trovata.")
        return
    print(f"- Run analizzate: {len(runs)} ultime su {total_available} disponibili.")
    for run in runs:
        payload = run.payload
        window = payload.get("analysis_window") or {}
        rel_path = relative_path(run.path, data_dir)
        print(
            "- "
            f"{fmt_dt(run.generated_at)} | server={payload.get('server_name', 'unknown')} | "
            f"window={window.get('start_utc', 'n/a')} -> {window.get('end_utc', 'n/a')} | "
            f"file={rel_path}"
        )


def print_server_health(runs: list[ReportRun]) -> None:
    print("## Server health")
    if not runs:
        print("- Nessun dato server.")
        return
    for run in runs:
        payload = run.payload
        snapshot = payload.get("server_snapshot") or {}
        print(
            "- "
            f"{fmt_dt(run.generated_at)} | server={payload.get('server_name', 'unknown')} | "
            f"load={format_load(snapshot.get('load_averages'))} | "
            f"swap_mb={fmt_value(snapshot.get('swap_used_mb'))} | "
            f"php_fpm={fmt_value(snapshot.get('php_fpm_process_count'))} | "
            f"redis={snapshot.get('redis_status', 'n/a')}"
        )


def print_recurring_top_apps(runs: list[ReportRun]) -> None:
    print("## Recurring top apps")
    stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"runs": 0, "servers": set(), "display": None, "max_score": 0, "last_priority": None})
    for run in runs:
        server = run.payload.get("server_name", "unknown")
        seen_in_run: set[str] = set()
        for app in top_apps_for_run(run.payload):
            app_id = app.get("app_id")
            if not app_id or app_id in seen_in_run:
                continue
            seen_in_run.add(app_id)
            item = stats[app_id]
            item["runs"] += 1
            item["servers"].add(server)
            item["display"] = app.get("display_name") or item["display"] or app_id
            item["max_score"] = max(item["max_score"], int_or_zero(app.get("suspicion_score")))
            item["last_priority"] = app.get("priority") or item["last_priority"]

    if not stats:
        print("- Nessuna app top trovata.")
        return
    for app_id, item in sorted(stats.items(), key=lambda pair: (-pair[1]["runs"], -pair[1]["max_score"], pair[0]))[:15]:
        servers = ",".join(sorted(item["servers"]))
        print(
            "- "
            f"{item['display']} [{app_id}] | run={item['runs']} | server={servers} | "
            f"priority={item['last_priority'] or 'n/a'} | max_score={item['max_score']}"
        )


def print_apps_with_5xx(runs: list[ReportRun]) -> None:
    print("## Apps with 5xx")
    stats: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"runs": 0, "display": None, "backend": 0, "php": 0})
    for run in runs:
        server = run.payload.get("server_name", "unknown")
        for app in run.payload.get("app_details") or []:
            app_id = app.get("app_id")
            if not app_id:
                continue
            summary = app.get("summary") or {}
            backend_5xx = int_or_zero(summary.get("backend_5xx_count", app.get("backend_5xx_count")))
            php_5xx = int_or_zero(summary.get("php_5xx_count", app.get("php_5xx_count")))
            if backend_5xx <= 0 and php_5xx <= 0:
                continue
            item = stats[(server, app_id)]
            item["runs"] += 1
            item["display"] = app.get("display_name") or item["display"] or app_id
            item["backend"] += backend_5xx
            item["php"] += php_5xx

    if not stats:
        print("- Nessuna app con 5xx nei dettagli disponibili.")
        return
    for (server, app_id), item in sorted(stats.items(), key=lambda pair: (-(pair[1]["backend"] + pair[1]["php"]), pair[0])):
        print(
            "- "
            f"{item['display']} [{app_id}] | server={server} | run={item['runs']} | "
            f"backend_5xx={item['backend']} | php_5xx={item['php']}"
        )


def print_apps_with_backend_errors(runs: list[ReportRun]) -> None:
    print("## Apps with backend errors")
    stats: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"runs": 0, "display": None, "errors": 0, "untimestamped": 0, "skipped": 0}
    )
    for run in runs:
        server = run.payload.get("server_name", "unknown")
        for app in run.payload.get("app_details") or []:
            app_id = app.get("app_id")
            if not app_id:
                continue
            summary = app.get("summary") or {}
            error_count = int_or_zero(summary.get("backend_error_count"))
            if error_count <= 0:
                continue
            item = stats[(server, app_id)]
            item["runs"] += 1
            item["display"] = app.get("display_name") or item["display"] or app_id
            item["errors"] += error_count
            item["untimestamped"] += int_or_zero(summary.get("backend_error_untimestamped_count"))
            item["skipped"] += int_or_zero(summary.get("backend_error_skipped_out_of_window"))

    if not stats:
        print("- Nessuna app con backend_error_count > 0 nei dettagli disponibili.")
        return
    for (server, app_id), item in sorted(stats.items(), key=lambda pair: (-pair[1]["errors"], pair[0])):
        print(
            "- "
            f"{item['display']} [{app_id}] | server={server} | run={item['runs']} | "
            f"errors={item['errors']} | untimestamped={item['untimestamped']} | skipped_out_of_window={item['skipped']}"
        )


def print_recurring_slow_plugins(runs: list[ReportRun]) -> None:
    print("## Recurring slow plugins")
    plugins = Counter()
    for run in runs:
        for app in run.payload.get("app_details") or []:
            for item in app.get("slowlog_suspected_plugins") or []:
                plugin = item.get("plugin")
                if plugin:
                    plugins[plugin] += int_or_zero(item.get("count"))

    if not plugins:
        print("- Nessun plugin ricorrente nei slow log disponibili.")
        return
    for plugin, count in plugins.most_common(15):
        print(f"- {plugin}: {count}")


def print_notes(runs: list[ReportRun], skipped: list[tuple[Path, str]]) -> None:
    print("## Notes")
    if not runs:
        print("- Verifica --data-dir e --server.")
    missing_5xx = sum(1 for run in runs if report_missing_5xx_fields(run.payload))
    if missing_5xx:
        print(f"- {missing_5xx} run non espongono i campi 5xx: probabili report generati prima della patch.")
    if skipped:
        print(f"- JSON saltati per errori di lettura/parsing: {len(skipped)}.")
        for path, reason in skipped[:5]:
            print(f"  - {path}: {reason}")
    if runs and not skipped and not missing_5xx:
        print("- Nessuna nota operativa.")


def top_apps_for_run(payload: dict[str, Any]) -> list[dict[str, Any]]:
    top_suspects = payload.get("top_suspect_apps") or []
    if top_suspects:
        return top_suspects
    return (payload.get("ranked_apps") or [])[:5]


def report_missing_5xx_fields(payload: dict[str, Any]) -> bool:
    for app in payload.get("app_details") or []:
        summary = app.get("summary") or {}
        if "backend_5xx_count" in summary or "php_5xx_count" in summary:
            return False
    return bool(payload.get("app_details"))


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def file_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def format_load(value: Any) -> str:
    if not isinstance(value, (list, tuple)):
        return "n/a"
    return " ".join(str(item) for item in value[:3]) or "n/a"


def fmt_dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def fmt_value(value: Any) -> str:
    return "n/a" if value is None else str(value)


def int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def relative_path(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
