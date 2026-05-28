#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_WINDOW_HOURS = 168
TOP_ROWS_LIMIT = 30


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize Server Bottleneck Analyzer storage JSON snapshots",
        epilog=(
            "Examples:\n"
            "  python3 scripts/summarize_storage.py --data-dir ../data --server wp-x --hours 24\n"
            "  python3 scripts/summarize_storage.py --data-dir ../data --server wp-x --only-suspects\n"
            "  python3 scripts/summarize_storage.py --data-dir ../data --server wp-x --app abcdefghij --hours 168"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", type=Path, default=Path("../data"), help="Directory containing <server>/<date>/storage-*.json")
    parser.add_argument("--server", help="Server name, for example wp-x")
    parser.add_argument("--hours", type=int, default=DEFAULT_WINDOW_HOURS, help="Lookback window in hours for trend summaries")
    parser.add_argument("--app", help="Show details for one app_id")
    parser.add_argument("--only-suspects", action="store_true", help="Show only top suspects from the latest storage snapshot")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir.expanduser()
    payloads = load_storage_payloads(data_dir, args.server)
    if not payloads:
        print("No storage snapshots found.")
        return 1
    selected = select_window(payloads, args.hours)
    latest = selected[-1] if selected else payloads[-1]
    if args.app:
        print_app_detail(latest, args.app)
    elif args.only_suspects:
        print_suspects(latest)
    else:
        print_summary(selected or [latest], args.hours)
    return 0


def load_storage_payloads(data_dir: Path, server: str | None) -> list[dict[str, Any]]:
    base = data_dir / server if server else data_dir
    if not base.exists():
        return []
    payloads = []
    for path in sorted(base.rglob("storage-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if server and payload.get("server_name") != server:
            continue
        payload["_path"] = str(path)
        payloads.append(payload)
    payloads.sort(key=lambda item: parse_dt(item.get("generated_at_utc")))
    return payloads


def select_window(payloads: list[dict[str, Any]], hours: int) -> list[dict[str, Any]]:
    if not payloads:
        return []
    cutoff = parse_dt(payloads[-1].get("generated_at_utc")) - timedelta(hours=max(hours, 1))
    return [payload for payload in payloads if parse_dt(payload.get("generated_at_utc")) >= cutoff]


def print_summary(payloads: list[dict[str, Any]], hours: int) -> None:
    latest = payloads[-1]
    print(f"# Storage summary - {latest.get('server_name', 'unknown')}")
    print(f"- Window: last {hours}h")
    observed = observed_window_label(payloads, hours)
    if observed:
        print(f"- Data window: {observed}")
    print(f"- Snapshots: {len(payloads)}")
    print(f"- Latest: {latest.get('generated_at_utc')}")
    disk = latest.get("server_disk") or {}
    print(
        "- Disk: "
        f"used={bytes_to_gb(disk.get('used_bytes'))} GB "
        f"free={bytes_to_gb(disk.get('free_bytes'))} GB "
        f"used_pct={fmt(disk.get('used_pct'))}% "
        f"inode_used_pct={fmt(disk.get('inode_used_pct'))}%"
    )
    disk_growth = disk_growth_for_window(payloads)
    if disk_growth is not None:
        print(f"- Disk growth observed: {format_mb_or_gb(disk_growth)}")
    print("")
    print("## Top growth in selected window")
    rows = window_growth_rows(payloads)
    if not rows:
        rows = (latest.get("rankings") or {}).get("top_growth_apps") or []
    print_rows(rows)
    print("")
    print("## Top growth 24h baseline")
    print_rows((latest.get("rankings") or {}).get("top_growth_24h_apps") or [])
    print("")
    print("## Top suspects")
    print_suspects(latest)


def print_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("- none")
        return
    for row in rows[:TOP_ROWS_LIMIT]:
        print(
            "- "
            f"{row.get('app_id')} +{row.get('total_mb', 0)} MB "
            f"rate={row.get('growth_rate_mb_per_hour', 0)} MB/h "
            f"bucket={row.get('main_growth_bucket') or '-'} "
            f"labels={','.join(row.get('labels') or []) or '-'}"
        )


def observed_window_label(payloads: list[dict[str, Any]], requested_hours: int) -> str | None:
    if len(payloads) < 2:
        return None
    first = parse_dt(payloads[0].get("generated_at_utc"))
    latest = parse_dt(payloads[-1].get("generated_at_utc"))
    observed_hours = max((latest - first).total_seconds() / 3600, 0)
    if observed_hours + 0.05 < requested_hours:
        return f"{observed_hours:.1f}h available of {requested_hours}h requested"
    return f"{observed_hours:.1f}h"


def disk_growth_for_window(payloads: list[dict[str, Any]]) -> float | None:
    if len(payloads) < 2:
        return None
    first_used = as_float(nested(payloads[0], "server_disk", "used_bytes"))
    latest_used = as_float(nested(payloads[-1], "server_disk", "used_bytes"))
    if first_used is None or latest_used is None:
        return None
    return round((latest_used - first_used) / (1024 * 1024), 2)


def window_growth_rows(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(payloads) < 2:
        return []
    first_apps = apps_by_id(payloads[0])
    latest_apps = apps_by_id(payloads[-1])
    hours = max((parse_dt(payloads[-1].get("generated_at_utc")) - parse_dt(payloads[0].get("generated_at_utc"))).total_seconds() / 3600, 0.001)
    rows = []
    for app_id, latest_app in latest_apps.items():
        first_app = first_apps.get(app_id)
        if not first_app:
            continue
        deltas = bucket_deltas_between(first_app, latest_app)
        total_bytes = deltas.get("total", 0)
        if total_bytes <= 0:
            continue
        bucket = main_growth_bucket(deltas)
        total_mb = bytes_to_mb(total_bytes)
        rows.append(
            {
                "app_id": app_id,
                "total_mb": total_mb,
                "growth_rate_mb_per_hour": round(total_mb / hours, 2),
                "main_growth_bucket": bucket,
                "labels": labels_for_bucket(bucket),
            }
        )
    rows.sort(key=lambda item: (-(item.get("total_mb") or 0), item.get("app_id") or ""))
    return rows


def apps_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(app.get("app_id")): app for app in payload.get("apps") or [] if app.get("app_id")}


def bucket_deltas_between(first_app: dict[str, Any], latest_app: dict[str, Any]) -> dict[str, int]:
    first_sizes = first_app.get("sizes_bytes") or {}
    latest_sizes = latest_app.get("sizes_bytes") or {}
    keys = set(first_sizes) | set(latest_sizes)
    return {key: int(latest_sizes.get(key, 0) or 0) - int(first_sizes.get(key, 0) or 0) for key in keys}


def main_growth_bucket(deltas: dict[str, int]) -> str | None:
    ignored = {"total", "public_html", "wp_content"}
    positives = [(key, value) for key, value in deltas.items() if key not in ignored and value > 0]
    if not positives:
        return None
    positives.sort(key=lambda item: (-item[1], item[0]))
    return positives[0][0]


def labels_for_bucket(bucket: str | None) -> list[str]:
    return {
        "logs": ["log_growth"],
        "cache": ["cache_growth"],
        "uploads": ["upload_growth"],
        "wpallimport": ["wpallimport_growth"],
        "local_backups": ["backup_accumulation"],
        "tmp": ["tmp_growth"],
        "debug_log": ["debug_log_large"],
    }.get(bucket or "", [])


def nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def print_suspects(payload: dict[str, Any]) -> None:
    suspects = payload.get("top_suspects") or []
    if not suspects:
        print("- none")
        return
    for item in suspects:
        print(
            "- "
            f"{item.get('app_id')} score={item.get('suspicion_score', 0)} "
            f"+prev={item.get('delta_previous_mb', 0)} MB "
            f"+24h={item.get('delta_24h_mb', 0)} MB "
            f"bucket={item.get('main_growth_bucket') or '-'}"
        )
        if item.get("top_path"):
            print(f"  top_path={item['top_path']}")


def print_app_detail(payload: dict[str, Any], app_id: str) -> None:
    app = next((item for item in payload.get("apps") or [] if item.get("app_id") == app_id), None)
    if not app:
        print(f"App not found in latest snapshot: {app_id}")
        return
    print(f"# App storage detail - {app_id}")
    print(f"- Latest: {payload.get('generated_at_utc')}")
    print(f"- Score: {app.get('suspicion_score', 0)}")
    print(f"- Labels: {', '.join(app.get('labels') or [])}")
    print("")
    print("## Sizes")
    for key, value in sorted((app.get("sizes_bytes") or {}).items()):
        print(f"- {key}: {bytes_to_mb(value)} MB")
    print("")
    print("## Delta previous")
    delta = app.get("delta_previous") or {}
    print(f"- total: {delta.get('total_mb', 0)} MB")
    print(f"- rate: {delta.get('growth_rate_mb_per_hour', 0)} MB/h")
    print(f"- main bucket: {delta.get('main_growth_bucket') or '-'}")
    print("")
    print("## Top directories")
    for item in (app.get("top_directories") or [])[:10]:
        print(f"- {item.get('size_mb')} MB {item.get('path')}")
    print("")
    print("## Top files")
    for item in (app.get("top_files") or [])[:10]:
        print(f"- {item.get('size_mb')} MB {item.get('path')}")


def parse_dt(value: Any) -> datetime:
    if not isinstance(value, str):
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def bytes_to_mb(value: Any) -> float:
    try:
        return round((value or 0) / (1024 * 1024), 2)
    except TypeError:
        return 0.0


def bytes_to_gb(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return str(round(value / (1024 * 1024 * 1024), 2))
    except TypeError:
        return "n/a"


def format_mb_or_gb(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value > 0 else ""
    if abs(value) >= 1024:
        return f"{sign}{round(value / 1024, 2)} GB"
    return f"{sign}{round(value, 2)} MB"


def fmt(value: Any) -> str:
    return "n/a" if value is None else str(value)


if __name__ == "__main__":
    raise SystemExit(main())
