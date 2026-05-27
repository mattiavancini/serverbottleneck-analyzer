#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BLOCKS = "▁▂▃▄▅▆▇█"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SSH terminal menu for Server Bottleneck Analyzer local reports",
        epilog=(
            "Examples:\n"
            "  python3 scripts/serverbottleneck_menu.py --data-dir ../data\n"
            "  python3 scripts/serverbottleneck_menu.py --data-dir ../data --server wp-x --once\n"
            "  python3 scripts/serverbottleneck_menu.py --data-dir ../data --server wp-x --hours 168"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", type=Path, default=Path("../data"), help="Directory containing analyzer reports")
    parser.add_argument("--server", help="Server to show first")
    parser.add_argument("--hours", type=int, default=24, help="Default dashboard lookback window")
    parser.add_argument("--once", action="store_true", help="Print dashboard once and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_output()
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir.expanduser()
    servers = list_servers(data_dir)
    if not servers:
        print("No reports found. Check --data-dir.")
        return 1
    server = args.server if args.server in servers else servers[0]
    if args.once:
        print_dashboard(data_dir, server, args.hours)
        return 0
    run_menu(data_dir, server, args.hours, servers)
    return 0


def run_menu(data_dir: Path, server: str, hours: int, servers: list[str]) -> None:
    current_server = server
    current_hours = hours
    while True:
        clear_screen()
        print_dashboard(data_dir, current_server, current_hours)
        print("")
        print("MENU")
        print("[1] Server status")
        print("[2] App cresciute ultima ora")
        print("[3] App cresciute ultime 24 ore")
        print("[4] App cresciute ultimi 7 giorni")
        print("[5] Top directory pesanti")
        print("[6] Top file grandi/recenti")
        print("[7] Dettaglio app")
        print("[8] Cambia server")
        print("[0] Esci")
        choice = input("Scegli: ").strip()
        if choice == "0":
            return
        if choice == "1":
            current_hours = ask_hours(current_hours)
        elif choice == "2":
            show_growth(data_dir, current_server, 1)
        elif choice == "3":
            show_growth(data_dir, current_server, 24)
        elif choice == "4":
            show_growth(data_dir, current_server, 168)
        elif choice == "5":
            show_top_directories(data_dir, current_server)
        elif choice == "6":
            show_top_files(data_dir, current_server)
        elif choice == "7":
            show_app_detail(data_dir, current_server)
        elif choice == "8":
            current_server = choose_server(servers, current_server)
        pause()


def print_dashboard(data_dir: Path, server: str, hours: int) -> None:
    storage = load_payloads(data_dir, server, "storage-*.json")
    inspections = load_payloads(data_dir, server, "inspection-*.json")
    if not storage and not inspections:
        print(f"SERVER BOTTLENECK ANALYZER - {server}")
        print("No local reports found for this server.")
        return
    latest_storage = storage[-1] if storage else {}
    selected_storage = select_window(storage, hours)
    selected_inspections = select_window(inspections, hours)
    latest_inspection = inspections[-1] if inspections else {}
    disk = latest_storage.get("server_disk") or {}
    growth_label, growth_value = storage_growth_label(latest_storage)
    snapshot = latest_inspection.get("server_snapshot") if isinstance(latest_inspection.get("server_snapshot"), dict) else {}
    cpu_count = as_float(snapshot.get("cpu_count")) or as_float(os.cpu_count()) or 1.0
    latest_load = first_load(latest_inspection)
    latest_ram_used = as_float(snapshot.get("ram_used_mb"))
    latest_ram_total = as_float(snapshot.get("ram_total_mb"))
    latest_swap_used = as_float(snapshot.get("swap_used_mb"))
    latest_swap_total = as_float(snapshot.get("swap_total_mb"))
    load_values = [first_load(item) for item in selected_inspections]
    ram_values = [nested(item, "server_snapshot", "ram_used_mb") for item in selected_inspections]
    disk_values = [nested(item, "server_disk", "used_pct") for item in selected_storage]
    php_fpm_values = [nested(item, "server_snapshot", "php_fpm_process_count") for item in selected_inspections]

    print(f"SERVER BOTTLENECK ANALYZER - {server}")
    print("")
    print(f"Periodo: ultime {hours}h")
    print(f"Ultimo storage snapshot: {latest_storage.get('generated_at_utc', 'n/a')}")
    print(f"Ultimo performance snapshot: {latest_inspection.get('generated_at_utc', 'n/a')}")
    print("")
    print("SERVER STATUS")
    print(f"CPU cores:      {int(cpu_count)}")
    print(
        f"Disk used:      {bytes_to_gb(disk.get('used_bytes'))} GB / {bytes_to_gb(disk.get('total_bytes'))} GB"
        f"   {bar(as_float(disk.get('used_pct')), 100)} {fmt(disk.get('used_pct'))}%"
    )
    print(f"Disk free:      {bytes_to_gb(disk.get('free_bytes'))} GB")
    print(f"Disk growth:    {fmt(growth_value)} MB / {growth_label}")
    print(
        f"Load avg:       {avg(load_values)} avg / {peak(load_values)} peak   "
        f"{load_bar(latest_load, cpu_count)} {load_status(latest_load, cpu_count)}"
    )
    print("                 scale: 1.00/core = CPU slots busy; >1.50/core = high queue")
    print(
        f"RAM used:       {avg(ram_values)} MB avg / {peak(ram_values)} MB peak   "
        f"{bar(percent(latest_ram_used, latest_ram_total), 100)} {fmt_pct(percent(latest_ram_used, latest_ram_total))}"
    )
    if latest_swap_total and latest_swap_total > 0:
        print(
            f"Swap used:      {fmt(latest_swap_used)} MB / {fmt(latest_swap_total)} MB   "
            f"{bar(percent(latest_swap_used, latest_swap_total), 100)} {fmt_pct(percent(latest_swap_used, latest_swap_total))}"
        )
    print(f"PHP-FPM proc:   {avg(php_fpm_values)} avg / {peak(php_fpm_values)} peak")
    print(f"Redis:          {nested(latest_inspection, 'server_snapshot', 'redis_status') or 'n/a'}")
    print("")
    print("TREND")
    print(f"Load:  {sparkline(load_values)}")
    print(f"RAM:   {sparkline(ram_values)}")
    print(f"Disk:  {sparkline(disk_values)}")
    print("")
    print("TOP STORAGE GROWTH")
    rows = (latest_storage.get("rankings") or {}).get("top_growth_24h_apps") or (latest_storage.get("rankings") or {}).get("top_growth_apps") or []
    if not rows:
        print("none")
    for index, row in enumerate(rows[:5], start=1):
        print(
            f"{index}. {row.get('app_id')}  +{row.get('total_mb', 0)} MB  "
            f"{row.get('main_growth_bucket') or '-'}  score={row.get('suspicion_score', 0)}"
        )


def show_growth(data_dir: Path, server: str, hours: int) -> None:
    latest = latest_payload(data_dir, server, "storage-*.json")
    clear_screen()
    print(f"TOP STORAGE GROWTH - {server} - {hours}h")
    print("")
    key = "top_growth_24h_apps" if hours >= 24 else "top_growth_apps"
    rows = (latest.get("rankings") or {}).get(key) or []
    if not rows:
        print("none")
    for row in rows[:20]:
        print(
            f"{row.get('app_id')}  +{row.get('total_mb', 0)} MB  "
            f"rate={row.get('growth_rate_mb_per_hour', 0)} MB/h  "
            f"bucket={row.get('main_growth_bucket') or '-'}  "
            f"labels={','.join(row.get('labels') or []) or '-'}"
        )


def show_top_directories(data_dir: Path, server: str) -> None:
    latest = latest_payload(data_dir, server, "storage-*.json")
    clear_screen()
    print(f"TOP DIRECTORIES - {server}")
    print("")
    rows = []
    for app in latest.get("apps") or []:
        for item in app.get("top_directories") or []:
            rows.append({"app_id": app.get("app_id"), **item})
    rows.sort(key=lambda item: (-(item.get("size_bytes") or 0), item.get("app_id") or "", item.get("path") or ""))
    for item in rows[:30]:
        print(f"{item.get('app_id')}  {item.get('size_mb')} MB  {item.get('path')}")
    if not rows:
        print("none")


def show_top_files(data_dir: Path, server: str) -> None:
    latest = latest_payload(data_dir, server, "storage-*.json")
    clear_screen()
    print(f"TOP FILES - {server}")
    print("")
    rows = (latest.get("rankings") or {}).get("top_large_files") or []
    for item in rows[:30]:
        print(f"{item.get('app_id')}  {item.get('size_mb')} MB  {item.get('modified_at_utc')}  {item.get('path')}")
    if not rows:
        print("none")


def show_app_detail(data_dir: Path, server: str) -> None:
    latest = latest_payload(data_dir, server, "storage-*.json")
    app_id = input("App id: ").strip()
    clear_screen()
    app = next((item for item in latest.get("apps") or [] if item.get("app_id") == app_id), None)
    if not app:
        print("App not found.")
        return
    print(f"APP DETAIL - {server}/{app_id}")
    print("")
    print(f"Score: {app.get('suspicion_score', 0)}")
    print(f"Labels: {', '.join(app.get('labels') or [])}")
    print("")
    print("Sizes")
    for key, value in sorted((app.get("sizes_bytes") or {}).items()):
        print(f"{key:14} {bytes_to_mb(value)} MB")
    print("")
    print("Top directories")
    for item in (app.get("top_directories") or [])[:15]:
        print(f"{item.get('size_mb')} MB  {item.get('path')}")
    print("")
    print("Top files")
    for item in (app.get("top_files") or [])[:15]:
        print(f"{item.get('size_mb')} MB  {item.get('path')}")


def choose_server(servers: list[str], current: str) -> str:
    clear_screen()
    print("SERVER DISPONIBILI")
    for index, server in enumerate(servers, start=1):
        marker = "*" if server == current else " "
        print(f"[{index}] {marker} {server}")
    raw = input("Scegli server: ").strip()
    try:
        index = int(raw) - 1
    except ValueError:
        return current
    if 0 <= index < len(servers):
        return servers[index]
    return current


def ask_hours(current: int) -> int:
    raw = input(f"Ore da visualizzare [{current}]: ").strip()
    if not raw:
        return current
    try:
        return max(1, int(raw))
    except ValueError:
        return current


def list_servers(data_dir: Path) -> list[str]:
    servers = set()
    if not data_dir.exists():
        return []
    for path in sorted(data_dir.iterdir()):
        if path.is_dir():
            servers.add(path.name)
    for payload in load_payloads(data_dir, None, "storage-*.json") + load_payloads(data_dir, None, "inspection-*.json"):
        if payload.get("server_name"):
            servers.add(str(payload["server_name"]))
    return sorted(servers)


def latest_payload(data_dir: Path, server: str, pattern: str) -> dict[str, Any]:
    payloads = load_payloads(data_dir, server, pattern)
    return payloads[-1] if payloads else {}


def load_payloads(data_dir: Path, server: str | None, pattern: str) -> list[dict[str, Any]]:
    base = data_dir / server if server else data_dir
    if not base.exists():
        return []
    payloads = []
    for path in sorted(base.rglob(pattern)):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if server and payload.get("server_name") and payload.get("server_name") != server:
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


def total_positive_growth(payload: dict[str, Any], delta_key: str) -> float:
    total = 0.0
    for app in payload.get("apps") or []:
        delta = app.get(delta_key) or {}
        total += max(delta.get("total_mb") or 0, 0)
    return round(total, 2)


def storage_growth_label(payload: dict[str, Any]) -> tuple[str, float]:
    has_24h = any((app.get("delta_24h") or {}).get("previous_snapshot_utc") for app in payload.get("apps") or [])
    if has_24h:
        return "24h", total_positive_growth(payload, "delta_24h")
    return "previous snapshot", total_positive_growth(payload, "delta_previous")


def first_load(payload: dict[str, Any]) -> float | None:
    values = nested(payload, "server_snapshot", "load_averages")
    if isinstance(values, list) and values:
        return as_float(values[0])
    return None


def nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def sparkline(values: list[Any]) -> str:
    numbers = [as_float(value) for value in values if as_float(value) is not None]
    if not numbers:
        return "n/a"
    if len(numbers) == 1:
        return BLOCKS[-1]
    low = min(numbers)
    high = max(numbers)
    if high == low:
        return BLOCKS[0] * len(numbers)
    output = []
    for value in numbers:
        index = int(round(((value - low) / (high - low)) * (len(BLOCKS) - 1)))
        output.append(BLOCKS[index])
    return "".join(output)


def bar(value: float | None, maximum: float, width: int = 18) -> str:
    if value is None or maximum <= 0:
        return "[" + "-" * width + "]"
    ratio = max(0.0, min(float(value) / maximum, 1.0))
    filled = int(round(ratio * width))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def load_bar(load_value: float | None, cpu_count: float, width: int = 18) -> str:
    if load_value is None or cpu_count <= 0:
        return "[" + "-" * width + "]"
    # Full bar at 2.0 load per core so the display still shows overload headroom.
    return bar(load_value / cpu_count, 2.0, width=width)


def load_status(load_value: float | None, cpu_count: float) -> str:
    if load_value is None or cpu_count <= 0:
        return "n/a"
    ratio = load_value / cpu_count
    if ratio < 0.7:
        return f"OK ({ratio:.2f}/core)"
    if ratio < 1.0:
        return f"BUSY ({ratio:.2f}/core)"
    if ratio < 1.5:
        return f"HIGH ({ratio:.2f}/core)"
    return f"CRITICAL ({ratio:.2f}/core)"


def percent(value: float | None, total: float | None) -> float | None:
    if value is None or total is None or total <= 0:
        return None
    return round((value / total) * 100, 2)


def fmt_pct(value: float | None) -> str:
    return "n/a%" if value is None else f"{value}%"


def avg(values: list[Any]) -> str:
    numbers = [as_float(value) for value in values if as_float(value) is not None]
    if not numbers:
        return "n/a"
    return str(round(sum(numbers) / len(numbers), 2))


def peak(values: list[Any]) -> str:
    numbers = [as_float(value) for value in values if as_float(value) is not None]
    if not numbers:
        return "n/a"
    return str(round(max(numbers), 2))


def as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def pause() -> None:
    input("\nInvio per continuare...")


def configure_output() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
