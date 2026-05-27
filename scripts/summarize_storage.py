#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


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
    parser.add_argument("--hours", type=int, default=24, help="Lookback window in hours for trend summaries")
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
    print("")
    print("## Top growth since previous snapshot")
    print_rows((latest.get("rankings") or {}).get("top_growth_apps") or [])
    print("")
    print("## Top growth 24h")
    print_rows((latest.get("rankings") or {}).get("top_growth_24h_apps") or [])
    print("")
    print("## Top suspects")
    print_suspects(latest)


def print_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("- none")
        return
    for row in rows[:10]:
        print(
            "- "
            f"{row.get('app_id')} +{row.get('total_mb', 0)} MB "
            f"rate={row.get('growth_rate_mb_per_hour', 0)} MB/h "
            f"bucket={row.get('main_growth_bucket') or '-'} "
            f"labels={','.join(row.get('labels') or []) or '-'}"
        )


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


def fmt(value: Any) -> str:
    return "n/a" if value is None else str(value)


if __name__ == "__main__":
    raise SystemExit(main())
