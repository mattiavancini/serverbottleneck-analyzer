#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BLOCKS = "▁▂▃▄▅▆▇█"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
COLOR_ENABLED = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
YELLOW = "1;33"
GREEN = "32"
LIME = "92"
ORANGE = "38;5;208"
RED = "31"
DEFAULT_WINDOW_HOURS = 168
TOP_DASHBOARD_LIMIT = 15
TOP_DETAIL_LIMIT = 30
TOP_APP_FILES_LIMIT = 10
TREND_WIDTH = 72
TREE_CHILD_LIMIT = 8
TREE_MAX_LINES = 90
TABLE_WIDTH = 98
STATUS_LABEL_WIDTH = 16
STATUS_VALUE_WIDTH = TABLE_WIDTH - STATUS_LABEL_WIDTH - 7
STATUS_BAR_WIDTH = 56


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
    parser.add_argument("--hours", type=int, default=DEFAULT_WINDOW_HOURS, help="Default dashboard lookback window")
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
        print(title("MENU"))
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
        print(title(f"SERVER BOTTLENECK ANALYZER - {server}"))
        print("No local reports found for this server.")
        return
    latest_storage = storage[-1] if storage else {}
    selected_storage = select_window(storage, hours)
    selected_inspections = select_window(inspections, hours)
    observed_label = observed_window_label(selected_storage, hours)
    observed_hours = observed_window_hours(selected_storage)
    latest_inspection = inspections[-1] if inspections else {}
    first_storage = selected_storage[0] if selected_storage else {}
    first_inspection = selected_inspections[0] if selected_inspections else {}
    disk = latest_storage.get("server_disk") or {}
    disk_growth_mb = disk_growth_for_window(selected_storage)
    snapshot = latest_inspection.get("server_snapshot") if isinstance(latest_inspection.get("server_snapshot"), dict) else {}
    cpu_count = as_float(snapshot.get("cpu_count")) or as_float(os.cpu_count()) or 1.0
    latest_ram_total = as_float(snapshot.get("ram_total_mb"))
    latest_swap_total = as_float(snapshot.get("swap_total_mb"))
    load_values = [first_load(item) for item in selected_inspections]
    load_reference = avg_number(load_values)
    ram_values = [nested(item, "server_snapshot", "ram_used_mb") for item in selected_inspections]
    ram_total = latest_ram_total or last_number([nested(item, "server_snapshot", "ram_total_mb") for item in selected_inspections])
    swap_values = [nested(item, "server_snapshot", "swap_used_mb") for item in selected_inspections]
    swap_total = latest_swap_total or last_number([nested(item, "server_snapshot", "swap_total_mb") for item in selected_inspections])
    disk_values = [nested(item, "server_disk", "used_pct") for item in selected_storage]
    php_fpm_values = [nested(item, "server_snapshot", "php_fpm_process_count") for item in selected_inspections]

    print(title(f"SERVER BOTTLENECK ANALYZER - {server}"))
    print("")
    print_window_table(hours, first_storage, latest_storage, first_inspection, latest_inspection, observed_hours, observed_label)
    print("")
    print_status_table(
        cpu_count=cpu_count,
        load_values=load_values,
        load_reference=load_reference,
        ram_values=ram_values,
        ram_total=ram_total,
        swap_values=swap_values,
        swap_total=swap_total,
        disk=disk,
        disk_growth_mb=disk_growth_mb,
        php_fpm_values=php_fpm_values,
        redis_status=nested(latest_inspection, "server_snapshot", "redis_status") or "n/a",
    )
    print("")
    print(title("TREND"))
    print_trend("Load", load_values)
    print_trend("RAM", ram_values)
    print_trend("Disk", disk_values)
    print("")
    print_app_size_tree(selected_storage or [latest_storage], server)
    print("")
    print(title("TOP STORAGE GROWTH (dal primo snapshot della finestra)"))
    rows = window_growth_rows(selected_storage)
    if not rows:
        rows = (latest_storage.get("rankings") or {}).get("top_growth_apps") or []
    if not rows:
        print("none")
    for index, row in enumerate(rows[:TOP_DASHBOARD_LIMIT], start=1):
        print(
            f"{index}. {row.get('app_id')}  +{row.get('total_mb', 0)} MB  "
            f"{row.get('main_growth_bucket') or '-'}"
        )


def show_growth(data_dir: Path, server: str, hours: int) -> None:
    payloads = load_payloads(data_dir, server, "storage-*.json")
    selected = select_window(payloads, hours)
    clear_screen()
    print(title(f"{growth_menu_title(hours)} - {server}"))
    print(intro("Stai vedendo", "crescita storage per app, confrontando il primo e l'ultimo snapshot nella finestra richiesta."))
    if len(selected) >= 2:
        print(intro("Confronto", f"{compact_dt(selected[0].get('generated_at_utc'))} -> {compact_dt(selected[-1].get('generated_at_utc'))}"))
    label = observed_window_label(selected, hours)
    if label:
        print(intro("Finestra dati reale", label))
    disk_growth_mb = disk_growth_for_window(selected)
    if disk_growth_mb is not None:
        print(intro("Disk growth osservato", format_mb_or_gb(disk_growth_mb)))
    print("")
    rows = window_growth_rows(selected)
    if not rows and payloads:
        latest = payloads[-1]
        rows = (latest.get("rankings") or {}).get("top_growth_apps") or []
    if not rows:
        print("none")
        return
    print(intro("Righe", f"{len(rows)} app con crescita positiva nella finestra"))
    print("")
    print_growth_table(rows)
    print("")
    print_growth_legend()


def growth_menu_title(hours: int) -> str:
    if hours == 1:
        return "MENU 2 - APP CRESCIUTE - ULTIMA ORA"
    if hours == 24:
        return "MENU 3 - APP CRESCIUTE - ULTIME 24 ORE"
    if hours == 168:
        return "MENU 4 - APP CRESCIUTE - ULTIMI 7 GIORNI"
    return f"APP CRESCIUTE - ULTIME {hours}H"


def print_growth_table(rows: list[dict[str, Any]]) -> None:
    print(title(f"{'APP':<12} {'CRESCITA':>12} {'RATE':>12} {'BUCKET':<18} LABELS"))
    print(f"{'-' * 12} {'-' * 12} {'-' * 12} {'-' * 18} {'-' * 28}")
    for row in rows:
        labels = ",".join(row.get("labels") or []) or "-"
        print(
            f"{str(row.get('app_id') or '-'):<12.12} "
            f"{format_mb_or_gb(as_float(row.get('total_mb'))):>12} "
            f"{format_rate(row.get('growth_rate_mb_per_hour')):>12} "
            f"{str(row.get('main_growth_bucket') or '-'):<18.18} "
            f"{labels}"
        )


def print_growth_legend() -> None:
    print(title("*** LEGENDA ***"))
    print(intro("crescita", "aumento totale dello spazio occupato dall'app nella finestra"))
    print(intro("rate", "media oraria della crescita nella finestra"))
    print(intro("bucket", "categoria interna che spiega dove si concentra la crescita: backup locali, cache, log, upload, import, tmp"))
    print(intro("labels", "classificazione automatica del tipo di problema probabile"))


def print_app_size_tree(storage_window: list[dict[str, Any]], server: str) -> None:
    rows = app_size_rows(storage_window)
    total_apps = len(rows)
    if not rows:
        print(title("APP SIZE TREE"))
        print("none")
        return
    root_size = sum(row["current_size_bytes"] for row in rows if row["present_latest"])
    latest_count = sum(1 for row in rows if row["present_latest"])
    print(title(f"APP SIZE TREE (finestra dati, tutte le {total_apps} app osservate)"))
    print(intro("Stai vedendo", "classifica completa delle app per dimensione; valore principale = ultimo snapshot."))
    print(intro("Copertura", f"{latest_count} app nell'ultimo snapshot; {total_apps} app osservate nella finestra"))
    if any(not row["reliable"] for row in rows):
        print(intro("Nota", "! = dimensione mantenuta/stimata per scan incompleto; verificare con il prossimo snapshot"))
    if any(row["dropped_from_window_max"] or not row["present_latest"] for row in rows):
        print(intro("Nota", "max = dimensione massima vista nella finestra; appare se l'ultimo snapshot e molto piu basso"))
    print(f"{server}/".ljust(58) + format_tree_size(root_size))
    for index, row in enumerate(rows):
        is_last = index == len(rows) - 1
        connector = "`-- " if is_last else "|-- "
        label = f"{connector}{row['app_id']}/"
        marker = " !" if not row["reliable"] else ""
        if not row["present_latest"]:
            marker += " missing latest"
        size_text = format_tree_size(row["current_size_bytes"])
        if row["dropped_from_window_max"] or not row["present_latest"]:
            size_text += f" (max {format_tree_size(row['max_size_bytes'])})"
        print(label.ljust(58) + size_text + marker)


def app_size_rows(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest = payloads[-1] if payloads else {}
    latest_apps = apps_by_id(latest)
    max_sizes: dict[str, int] = {}
    for payload in payloads:
        for app in payload.get("apps") or []:
            app_id = str(app.get("app_id") or "")
            if not app_id:
                continue
            size = int((app.get("sizes_bytes") or {}).get("total") or 0)
            max_sizes[app_id] = max(max_sizes.get(app_id, 0), size)
    rows = []
    for app_id, max_size in max_sizes.items():
        latest_app = latest_apps.get(app_id)
        present_latest = latest_app is not None
        current_size = int(((latest_app or {}).get("sizes_bytes") or {}).get("total") or 0)
        quality = (((latest_app or {}).get("size_quality") or {}).get("total") or {})
        dropped = max_size > 0 and current_size < max_size * 0.75 and (max_size - current_size) >= 512 * 1024 * 1024
        rows.append(
            {
                "app_id": app_id,
                "current_size_bytes": current_size,
                "max_size_bytes": max_size,
                "present_latest": present_latest,
                "dropped_from_window_max": dropped,
                "reliable": quality.get("reliable", True),
            }
        )
    rows.sort(key=lambda row: (-max(row["current_size_bytes"], row["max_size_bytes"]), row["app_id"]))
    return rows


def show_top_directories(data_dir: Path, server: str) -> None:
    latest = latest_payload(data_dir, server, "storage-*.json")
    clear_screen()
    print(title(f"TOP DIRECTORIES - {server}"))
    print(intro("Stai vedendo", f"le prime {TOP_DETAIL_LIMIT} directory piu pesanti trovate nell'ultimo snapshot storage."))
    print("")
    rows = []
    for app in latest.get("apps") or []:
        for item in app.get("top_directories") or []:
            rows.append({"app_id": app.get("app_id"), **item})
    rows.sort(key=lambda item: (-(item.get("size_bytes") or 0), item.get("app_id") or "", item.get("path") or ""))
    for item in rows[:TOP_DETAIL_LIMIT]:
        print(f"{item.get('app_id')}  {item.get('size_mb')} MB  {item.get('path')}")
    if not rows:
        print("none")


def show_top_files(data_dir: Path, server: str) -> None:
    latest = latest_payload(data_dir, server, "storage-*.json")
    clear_screen()
    print(title(f"TOP FILES - {server}"))
    print(intro("Stai vedendo", f"i primi {TOP_DETAIL_LIMIT} file piu grandi trovati nell'ultimo snapshot storage."))
    print("")
    rows = (latest.get("rankings") or {}).get("top_large_files") or []
    for item in rows[:TOP_DETAIL_LIMIT]:
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
    print(title(f"APP DETAIL - {server}/{app_id}"))
    print("")
    print(intro("Storage score", str(app.get("suspicion_score", 0))))
    print(intro("Nota", "lo storage score e solo un indicatore storage dell'ultimo snapshot; non include ancora performance/PHP."))
    print(intro("Labels", ", ".join(app.get("labels") or []) or "-"))
    warnings = app.get("scan_warnings") or []
    if warnings:
        print(intro("Scan warnings", f"{len(warnings)} avvisi; alcune dimensioni possono essere stimate o mantenute dal precedente snapshot"))
    print("")
    print(title("Tree"))
    for line in render_app_tree(app):
        print(line)
    print("")
    print(title("Top directories"))
    top_dirs = app_directory_rows(app)
    for item in top_dirs[:TOP_DETAIL_LIMIT]:
        print(f"{bytes_to_mb(item.get('size_bytes'))} MB  {item.get('path')}")
    print("")
    print(title(f"Top files ({TOP_APP_FILES_LIMIT})"))
    top_file_rows = sorted(app.get("top_files") or [], key=lambda item: (-(item.get("size_bytes") or 0), item.get("path") or ""))
    for item in top_file_rows[:TOP_APP_FILES_LIMIT]:
        print(f"{item.get('size_mb')} MB  {item.get('path')}")


def render_app_tree(app: dict[str, Any]) -> list[str]:
    app_id = str(app.get("app_id") or "app")
    app_root = normalize_path(app.get("app_root") or "")
    nodes = app_directory_nodes(app, infer_parents=True)
    root_size = int((app.get("sizes_bytes") or {}).get("total") or 0)
    lines = [f"{app_id}/".ljust(58) + format_tree_size(root_size)]
    if not nodes:
        lines.append("  nessuna directory pesante disponibile nello snapshot")
        return lines

    main_path = main_tree_path(app, nodes)
    render_children(app_root, "", lines, nodes, main_path, depth=0)
    if len(lines) > TREE_MAX_LINES:
        return lines[:TREE_MAX_LINES] + ["  ... albero abbreviato: usa Top directories per la lista completa"]
    return lines


def app_directory_rows(app: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = app_directory_nodes(app, infer_parents=False)
    rows = [{"path": path, "size_bytes": size} for path, size in nodes.items()]
    rows.sort(key=lambda item: (-(item.get("size_bytes") or 0), item.get("path") or ""))
    return rows


def app_directory_nodes(app: dict[str, Any], infer_parents: bool) -> dict[str, int]:
    app_root = normalize_path(app.get("app_root") or "")
    sizes = app.get("sizes_bytes") or {}
    paths = app.get("paths") or {}
    nodes: dict[str, int] = {}

    def add_node(raw_path: Any, raw_size: Any) -> None:
        path = normalize_path(raw_path)
        if not path or not app_root or path == app_root:
            return
        if not path.startswith(app_root.rstrip("/") + "/"):
            return
        size = int(raw_size or 0)
        if size <= 0:
            return
        nodes[path] = max(nodes.get(path, 0), size)
        if not infer_parents:
            return
        parent = path.rsplit("/", 1)[0]
        while parent and parent != app_root and parent.startswith(app_root.rstrip("/") + "/"):
            nodes[parent] = max(nodes.get(parent, 0), size)
            parent = parent.rsplit("/", 1)[0]

    for key, path in paths.items():
        if key in {"total", "debug_log"}:
            continue
        add_node(path, sizes.get(key))

    for item in app.get("top_directories") or []:
        add_node(item.get("path"), item.get("size_bytes"))

    return nodes


def render_children(
    parent: str,
    prefix: str,
    lines: list[str],
    nodes: dict[str, int],
    main_path: str | None,
    depth: int,
) -> None:
    children = direct_children(parent, nodes)
    if not children:
        return
    children = children[:TREE_CHILD_LIMIT]
    for index, path in enumerate(children):
        is_last = index == len(children) - 1
        connector = "`-- " if is_last else "|-- "
        child_prefix = "    " if is_last else "|   "
        name = tree_node_name(path)
        marker = " <- PRINCIPALE" if path == main_path else ""
        label = f"{prefix}{connector}{name}/"
        lines.append(label.ljust(58) + f"{format_tree_size(nodes[path])}{marker}")
        if depth < 4:
            render_children(path, prefix + child_prefix, lines, nodes, main_path, depth + 1)


def direct_children(parent: str, nodes: dict[str, int]) -> list[str]:
    prefix = parent.rstrip("/") + "/"
    output = []
    for path in nodes:
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix) :]
        if rest and "/" not in rest:
            output.append(path)
    output.sort(key=lambda path: (-nodes[path], path))
    return output


def main_tree_path(app: dict[str, Any], nodes: dict[str, int]) -> str | None:
    paths = app.get("paths") or {}
    delta = app.get("delta_previous") or {}
    bucket = delta.get("main_growth_bucket")
    if isinstance(bucket, str) and paths.get(bucket):
        path = normalize_path(paths.get(bucket))
        if path in nodes:
            return path
    if not nodes:
        return None
    return max(nodes, key=lambda path: nodes[path])


def normalize_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").rstrip("/")


def tree_node_name(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1] or path


def format_tree_size(value: Any) -> str:
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        size = 0.0
    units = ["B", "K", "M", "G", "T"]
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    if index == 0:
        return f"{int(size)}{units[index]}"
    if size >= 100:
        return f"{round(size)}{units[index]}"
    return f"{round(size, 1)}{units[index]}"


def choose_server(servers: list[str], current: str) -> str:
    clear_screen()
    print(title("SERVER DISPONIBILI"))
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


def print_window_table(
    hours: int,
    first_storage: dict[str, Any],
    latest_storage: dict[str, Any],
    first_inspection: dict[str, Any],
    latest_inspection: dict[str, Any],
    observed_hours: float | None,
    observed_label: str | None,
) -> None:
    col_width = (TABLE_WIDTH - 7) // 2
    print(box_top(TABLE_WIDTH))
    print(box_full(intro("Finestra target", f"ultimi {format_hours(hours)}"), TABLE_WIDTH))
    print(box_mid(TABLE_WIDTH))
    print(two_col_row(title("PRIMO SNAPSHOT"), title("ULTIMO SNAPSHOT"), col_width))
    print(two_col_row(intro("storage", compact_dt(first_storage.get('generated_at_utc'))), intro("storage", compact_dt(latest_storage.get('generated_at_utc'))), col_width))
    print(two_col_row(intro("performance", compact_dt(first_inspection.get('generated_at_utc'))), intro("performance", compact_dt(latest_inspection.get('generated_at_utc'))), col_width))
    print(box_mid(TABLE_WIDTH))
    if observed_hours is None:
        print(box_full(intro("Finestra dati", f"nessun confronto disponibile / target {format_hours(hours)}"), TABLE_WIDTH))
        print(box_full(intro("Riempimento", f"{bar(0, 100, width=52)} 0.0%"), TABLE_WIDTH))
    else:
        pct = min(max((observed_hours / max(hours, 1)) * 100, 0.0), 100.0)
        print(box_full(intro("Finestra dati", observed_label or format_hours(observed_hours)), TABLE_WIDTH))
        print(box_full(intro("Riempimento", f"{bar(pct, 100, width=52)} {round(pct, 1)}% del target"), TABLE_WIDTH))
    print(box_bottom(TABLE_WIDTH))


def print_status_table(
    cpu_count: float,
    load_values: list[Any],
    load_reference: float | None,
    ram_values: list[Any],
    ram_total: float | None,
    swap_values: list[Any],
    swap_total: float | None,
    disk: dict[str, Any],
    disk_growth_mb: float | None,
    php_fpm_values: list[Any],
    redis_status: str,
) -> None:
    print(box_top(TABLE_WIDTH))
    print(box_full(title("SERVER STATUS"), TABLE_WIDTH))
    print(box_mid(TABLE_WIDTH))
    print(status_row("CPU cores", str(int(cpu_count))))
    print(status_row("Load avg", f"{avg(load_values)} media / {peak(load_values)} picco - {load_status(load_reference, cpu_count)}"))
    print(status_detail(f"{load_bar(load_reference, cpu_count, width=STATUS_BAR_WIDTH)} media"))
    print(status_detail("scala: 1.00/core = CPU occupata; >1.50/core = coda alta"))
    ram_pct = percent(avg_number(ram_values), ram_total)
    print(status_row("RAM used", f"{avg(ram_values)} MB media / {peak(ram_values)} MB picco - {fmt_pct(ram_pct)}"))
    print(status_detail(f"{bar(ram_pct, 100, width=STATUS_BAR_WIDTH)} media"))
    if swap_total and swap_total > 0:
        swap_pct = percent(avg_number(swap_values), swap_total)
        print(status_row("Swap used", f"{avg(swap_values)} MB media / {peak(swap_values)} MB picco - {fmt_pct(swap_pct)}"))
        print(status_detail(f"{bar(swap_pct, 100, width=STATUS_BAR_WIDTH)} media"))
    disk_pct = as_float(disk.get("used_pct"))
    print(status_row("Disk used", f"{bytes_to_gb(disk.get('used_bytes'))} GB / {bytes_to_gb(disk.get('total_bytes'))} GB - {fmt(disk.get('used_pct'))}%"))
    print(status_detail(f"{bar(disk_pct, 100, width=STATUS_BAR_WIDTH)} ultimo snapshot"))
    print(status_row("Disk free", f"{bytes_to_gb(disk.get('free_bytes'))} GB"))
    print(status_row("Disk growth", f"{format_mb_or_gb(disk_growth_mb)} dal primo snapshot della finestra"))
    print(status_row("PHP-FPM proc", f"{avg(php_fpm_values)} media / {peak(php_fpm_values)} picco"))
    print(status_row("Redis", redis_status))
    print(box_bottom(TABLE_WIDTH))


def compact_dt(value: Any) -> str:
    if not value:
        return "n/a"
    parsed = parse_dt(value)
    if parsed.timestamp() <= 0:
        return str(value)
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def box_top(width: int) -> str:
    return "+" + "-" * (width - 2) + "+"


def box_mid(width: int) -> str:
    return "+" + "-" * (width - 2) + "+"


def box_bottom(width: int) -> str:
    return "+" + "-" * (width - 2) + "+"


def box_full(text: str, width: int) -> str:
    return "| " + fit_text(text, width - 4) + " |"


def two_col_row(left: str, right: str, col_width: int) -> str:
    return "| " + fit_text(left, col_width) + " | " + fit_text(right, col_width) + " |"


def status_row(label: str, value: str) -> str:
    return "| " + fit_text(label_color(label), STATUS_LABEL_WIDTH) + " | " + fit_text(value, STATUS_VALUE_WIDTH) + " |"


def status_detail(value: str) -> str:
    return "| " + " " * STATUS_LABEL_WIDTH + " | " + fit_text(value, STATUS_VALUE_WIDTH) + " |"


def fit_text(value: Any, width: int) -> str:
    text = str(value)
    visible = visible_len(text)
    if visible > width:
        plain = strip_ansi(text)
        return plain[: max(width - 1, 0)] + "…" if width > 1 else plain[:width]
    return text + " " * (width - visible)


def title(value: Any) -> str:
    return color(str(value), YELLOW)


def label_color(value: Any) -> str:
    return color(str(value), GREEN)


def intro(label: str, value: str) -> str:
    return f"{label_color(label + ':')} {value}"


def color(value: str, code: str) -> str:
    if not COLOR_ENABLED:
        return value
    return f"\033[{code}m{value}\033[0m"


def strip_ansi(value: str) -> str:
    return ANSI_RE.sub("", value)


def visible_len(value: str) -> int:
    return len(strip_ansi(value))


def observed_window_hours(payloads: list[dict[str, Any]]) -> float | None:
    if len(payloads) < 2:
        return None
    first = parse_dt(payloads[0].get("generated_at_utc"))
    latest = parse_dt(payloads[-1].get("generated_at_utc"))
    return max((latest - first).total_seconds() / 3600, 0)


def observed_window_label(payloads: list[dict[str, Any]], requested_hours: int) -> str | None:
    observed_hours = observed_window_hours(payloads)
    if observed_hours is None:
        return None
    if observed_hours + 0.05 < requested_hours:
        return f"{format_hours(observed_hours)} disponibili su {format_hours(requested_hours)} target"
    return format_hours(observed_hours)


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
        bucket_deltas = bucket_deltas_between(first_app, latest_app)
        total_bytes = bucket_deltas.get("total", 0)
        if total_bytes <= 0:
            continue
        main_bucket = main_growth_bucket(bucket_deltas)
        total_mb = bytes_to_mb(total_bytes)
        rows.append(
            {
                "app_id": app_id,
                "total_mb": total_mb,
                "growth_rate_mb_per_hour": round(total_mb / hours, 2),
                "main_growth_bucket": main_bucket,
                "labels": labels_for_bucket(main_bucket),
                "suspicion_score": latest_app.get("suspicion_score", 0),
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


def sparkline_levels(values: list[Any], width: int) -> list[int]:
    numbers = normalize_numbers(values)
    if not numbers:
        return []
    numbers = downsample(numbers, width)
    if len(numbers) == 1:
        return [len(BLOCKS) - 1]
    low = min(numbers)
    high = max(numbers)
    if high == low:
        return [0 for _ in numbers]
    levels = []
    for value in numbers:
        index = int(round(((value - low) / (high - low)) * (len(BLOCKS) - 1)))
        levels.append(index)
    return expand_levels(levels, width)


def print_trend(label: str, values: list[Any]) -> None:
    levels = sparkline_levels(values, TREND_WIDTH)
    if not levels:
        print(f"{label:<5} n/a")
        print("")
        return
    top = []
    bottom = []
    for level in levels:
        if level >= 4:
            top.append(BLOCKS[level - 4])
            bottom.append(BLOCKS[-1])
        else:
            top.append(" ")
            bottom.append(BLOCKS[level])
    print(f"{label:<5} {''.join(top)}")
    print(f"{'':<5} {''.join(bottom)}")
    print("")


def expand_levels(levels: list[int], target_width: int) -> list[int]:
    if not levels or len(levels) >= target_width:
        return levels
    repeat = max(1, target_width // len(levels))
    expanded = [level for level in levels for _ in range(repeat)]
    if len(expanded) < target_width:
        expanded.extend([levels[-1]] * (target_width - len(expanded)))
    return expanded[:target_width]


def normalize_numbers(values: list[Any]) -> list[float]:
    return [number for value in values if (number := as_float(value)) is not None]


def downsample(values: list[float], max_points: int) -> list[float]:
    if len(values) <= max_points:
        return values
    step = len(values) / max_points
    sampled = []
    for index in range(max_points):
        start = int(index * step)
        end = int((index + 1) * step)
        bucket = values[start:max(end, start + 1)]
        sampled.append(sum(bucket) / len(bucket))
    return sampled


def bar(value: float | None, maximum: float, width: int = 18) -> str:
    if value is None or maximum <= 0:
        return "[" + "-" * width + "]"
    ratio = max(0.0, min(float(value) / maximum, 1.0))
    filled = int(round(ratio * width))
    return "[" + colored_bar_fill(filled, width) + "-" * (width - filled) + "]"


def colored_bar_fill(filled: int, width: int) -> str:
    if filled <= 0:
        return ""
    segments: list[str] = []
    current_color = ""
    current_text = ""
    for index in range(filled):
        color_code = bar_color_for_position(index, width)
        if color_code != current_color and current_text:
            segments.append(color(current_text, current_color))
            current_text = ""
        current_color = color_code
        current_text += "#"
    if current_text:
        segments.append(color(current_text, current_color))
    return "".join(segments)


def bar_color_for_position(index: int, width: int) -> str:
    position = (index + 1) / max(width, 1)
    if position <= 0.30:
        return LIME
    if position <= 0.70:
        return ORANGE
    return RED


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
    numbers = normalize_numbers(values)
    if not numbers:
        return "n/a"
    return str(round(sum(numbers) / len(numbers), 2))


def peak(values: list[Any]) -> str:
    numbers = normalize_numbers(values)
    if not numbers:
        return "n/a"
    return str(round(max(numbers), 2))


def avg_number(values: list[Any]) -> float | None:
    numbers = normalize_numbers(values)
    if not numbers:
        return None
    return round(sum(numbers) / len(numbers), 2)


def last_number(values: list[Any]) -> float | None:
    numbers = normalize_numbers(values)
    if not numbers:
        return None
    return numbers[-1]


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


def format_mb_or_gb(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value > 0 else ""
    if abs(value) >= 1024:
        return f"{sign}{round(value / 1024, 2)} GB"
    return f"{sign}{round(value, 2)} MB"


def format_rate(value: Any) -> str:
    number = as_float(value)
    if number is None:
        return "n/a"
    if abs(number) >= 1024:
        return f"{round(number / 1024, 2)} GB/h"
    return f"{round(number, 2)} MB/h"


def format_hours(value: int | float) -> str:
    if value >= 168 and float(value).is_integer():
        return f"{int(value // 24)} giorni"
    if value >= 24 and value % 24 == 0:
        return f"{int(value // 24)} giorni"
    if value >= 24:
        return f"{round(value / 24, 1)} giorni"
    if float(value).is_integer():
        return f"{int(value)}h"
    return f"{value:.1f}h"


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
