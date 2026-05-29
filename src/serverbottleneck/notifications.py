from __future__ import annotations

import argparse
import json
import smtplib
import socket
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from .storage import load_storage_history, parse_iso


DEFAULT_CONFIG = Path("config/notifications.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send Server Bottleneck Analyzer SMTP alerts and daily reports",
        epilog=(
            "Examples:\n"
            "  python3 -m serverbottleneck.notifications --data-dir ../data --server WP_Q --mode alert --dry-run\n"
            "  python3 -m serverbottleneck.notifications --data-dir ../data --server WP_Q --mode daily\n"
            "  python3 -m serverbottleneck.notifications --data-dir ../data --server WP_Q --mode smtp-test"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", type=Path, default=Path("../data"), help="Directory containing analyzer reports")
    parser.add_argument("--server", required=True, help="Server name, for example WP_Q")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Local notification config JSON")
    parser.add_argument("--mode", choices=["alert", "daily", "smtp-test"], default="alert", help="Notification type to evaluate")
    parser.add_argument("--dry-run", action="store_true", help="Print the email body without sending")
    parser.add_argument("--force", action="store_true", help="Ignore alert cooldown")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    if args.mode == "smtp-test":
        if args.dry_run:
            print(smtp_summary(config))
            return 0
        if not config.get("enabled", False):
            print(f"Notifications disabled in {args.config}. Set enabled=true after SMTP configuration.")
            return 0
        test_smtp_connection(config)
        print("SMTP connection/login OK.")
        return 0

    data_dir = args.data_dir.expanduser()
    storage_history = load_storage_history(data_dir, args.server)
    inspection_history = load_report_history(data_dir, args.server, "inspection-*.json")
    if not storage_history:
        print(f"No storage snapshots found for server {args.server}.")
        return 1

    if args.mode == "daily":
        subject, body, should_send = build_daily_report(args.server, storage_history, inspection_history, config)
    else:
        subject, body, should_send = build_alert_report(args.server, storage_history, inspection_history, config)

    if args.dry_run:
        print(subject)
        print("")
        print(body)
        return 0

    if not config.get("enabled", False):
        print(f"Notifications disabled in {args.config}. Set enabled=true after SMTP configuration.")
        return 0
    if not should_send:
        print("No notification needed.")
        return 0
    if args.mode == "alert" and not args.force and cooldown_active(args.data_dir, args.server, config):
        print("Alert suppressed by cooldown.")
        return 0

    send_email(config, subject, body)
    if args.mode == "alert":
        save_alert_state(args.data_dir, args.server)
    print("Notification sent.")
    return 0


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Missing config file: {path}. Copy config/notifications.example.json to this path.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON config {path}: {exc}") from exc


def build_alert_report(
    server: str,
    storage_history: list[dict[str, Any]],
    inspection_history: list[dict[str, Any]],
    config: dict[str, Any],
) -> tuple[str, str, bool]:
    latest = storage_history[-1]
    thresholds = config.get("thresholds") or {}
    disk = latest.get("server_disk") or {}
    used_pct = as_float(disk.get("used_pct"))
    free_gb = bytes_to_gb_number(disk.get("free_bytes"))
    growth_24h_gb = disk_growth_gb(storage_history, 24)
    alerts = []

    used_limit = as_float(thresholds.get("disk_used_pct_critical"))
    free_limit = as_float(thresholds.get("disk_free_gb_critical"))
    growth_limit = as_float(thresholds.get("disk_growth_gb_24h_warning"))
    if used_limit is not None and used_pct is not None and used_pct >= used_limit:
        alerts.append(f"CRITICAL disk used: {used_pct}% >= {used_limit}%")
    if free_limit is not None and free_gb is not None and free_gb <= free_limit:
        alerts.append(f"CRITICAL disk free: {free_gb:.2f} GB <= {free_limit} GB")
    if growth_limit is not None and growth_24h_gb is not None and growth_24h_gb >= growth_limit:
        alerts.append(f"WARNING disk growth 24h: +{growth_24h_gb:.2f} GB >= {growth_limit} GB")

    subject_prefix = "ALERT" if alerts else "OK"
    subject = f"[SBA] {subject_prefix} {server} disk status"
    body = build_report_body(server, storage_history, inspection_history, hours=24, alerts=alerts)
    return subject, body, bool(alerts) and bool((config.get("alerts") or {}).get("enabled", True))


def build_daily_report(
    server: str,
    storage_history: list[dict[str, Any]],
    inspection_history: list[dict[str, Any]],
    config: dict[str, Any],
) -> tuple[str, str, bool]:
    daily = config.get("daily_report") or {}
    hours = int_or_default(daily.get("hours"), 24)
    subject = f"[SBA] Daily report {server}"
    body = build_report_body(server, storage_history, inspection_history, hours=hours, alerts=[])
    return subject, body, bool(daily.get("enabled", True))


def build_report_body(
    server: str,
    storage_history: list[dict[str, Any]],
    inspection_history: list[dict[str, Any]],
    hours: int,
    alerts: list[str],
) -> str:
    latest_storage = storage_history[-1]
    storage_window = select_window(storage_history, hours)
    inspection_window = select_window(inspection_history, hours)
    latest_inspection = inspection_window[-1] if inspection_window else {}
    disk = latest_storage.get("server_disk") or {}
    lines = [
        f"Server Bottleneck Analyzer - {server}",
        f"Generated: {latest_storage.get('generated_at_utc', 'n/a')}",
        f"Window: last {hours}h, {len(storage_window)} storage snapshots, {len(inspection_window)} performance snapshots",
        "",
    ]
    if alerts:
        lines.append("ALERTS")
        lines.extend(f"- {alert}" for alert in alerts)
        lines.append("")

    load_values = [first_load(item) for item in inspection_window]
    ram_values = [nested(item, "server_snapshot", "ram_used_mb") for item in inspection_window]
    swap_values = [nested(item, "server_snapshot", "swap_used_mb") for item in inspection_window]
    ram_total = last_number([nested(item, "server_snapshot", "ram_total_mb") for item in inspection_window])
    swap_total = last_number([nested(item, "server_snapshot", "swap_total_mb") for item in inspection_window])
    cpu_count = as_float(nested(latest_inspection, "server_snapshot", "cpu_count"))

    lines.extend(
        [
            "SERVER STATUS",
            f"- CPU cores: {format_number(cpu_count)}",
            f"- CPU load avg: {format_number(avg_number(load_values))} avg / {format_number(peak_number(load_values))} peak",
            f"- RAM avg: {format_number(avg_number(ram_values))} MB / total {format_number(ram_total)} MB ({format_pct(percent(avg_number(ram_values), ram_total))})",
            f"- Swap avg: {format_number(avg_number(swap_values))} MB / total {format_number(swap_total)} MB ({format_pct(percent(avg_number(swap_values), swap_total))})",
            "",
            "DISK",
            f"- Disk usage: {bytes_to_gb(disk.get('used_bytes'))} / {bytes_to_gb(disk.get('total_bytes'))} GB ({disk.get('used_pct', 'n/a')}% used)",
            f"- Disk free: {bytes_to_gb(disk.get('free_bytes'))} GB",
            f"- Disk growth {hours}h: {format_gb(disk_growth_gb(storage_window, hours))}",
        ]
    )
    return "\n".join(lines)


def send_email(config: dict[str, Any], subject: str, body: str) -> None:
    smtp = config.get("smtp") or {}
    recipients = smtp.get("to") or []
    if isinstance(recipients, str):
        recipients = [recipients]
    if not recipients:
        raise SystemExit("SMTP config has no recipients in smtp.to.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp.get("from") or smtp.get("username")
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    host = require_value(smtp, "host")
    port = int_or_default(smtp.get("port"), 587)
    timeout = int_or_default(smtp.get("timeout_seconds"), 20)
    try:
        with open_smtp_client(smtp, host, port, timeout) as client:
            maybe_login(client, smtp)
            client.send_message(message)
    except (OSError, smtplib.SMTPException, socket.timeout) as exc:
        raise SystemExit(smtp_error_message(exc, smtp, host, port)) from exc


def test_smtp_connection(config: dict[str, Any]) -> None:
    smtp = config.get("smtp") or {}
    host = require_value(smtp, "host")
    port = int_or_default(smtp.get("port"), 587)
    timeout = int_or_default(smtp.get("timeout_seconds"), 20)
    try:
        with open_smtp_client(smtp, host, port, timeout) as client:
            maybe_login(client, smtp)
    except (OSError, smtplib.SMTPException, socket.timeout) as exc:
        raise SystemExit(smtp_error_message(exc, smtp, host, port)) from exc


def open_smtp_client(smtp: dict[str, Any], host: str, port: int, timeout: int):
    server_cls = smtplib.SMTP_SSL if smtp.get("ssl") else smtplib.SMTP
    client = server_cls(host, port, timeout=timeout)
    if smtp.get("starttls") and not smtp.get("ssl"):
        client.starttls()
    return client


def maybe_login(client: smtplib.SMTP, smtp: dict[str, Any]) -> None:
    username = smtp.get("username")
    password = smtp.get("password")
    if username and password:
        client.login(username, password)


def smtp_summary(config: dict[str, Any]) -> str:
    smtp = config.get("smtp") or {}
    return (
        f"SMTP config: host={smtp.get('host') or 'n/a'} "
        f"port={smtp.get('port') or 'n/a'} "
        f"starttls={bool(smtp.get('starttls'))} "
        f"ssl={bool(smtp.get('ssl'))} "
        f"username={smtp.get('username') or 'n/a'}"
    )


def smtp_error_message(exc: BaseException, smtp: dict[str, Any], host: str, port: int) -> str:
    mode = "SSL/TLS diretto" if smtp.get("ssl") else ("STARTTLS" if smtp.get("starttls") else "plain")
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            f"SMTP authentication failed: {exc}\n"
            f"Config usata: host={host} port={port} mode={mode} username={smtp.get('username') or 'n/a'}\n"
            "Connessione e TLS sono arrivati al server SMTP, ma username/password sono stati rifiutati.\n"
            "Verificare password mailbox, spazi copiati nel JSON, account SMTP attivo e username come email completa."
        )
    return (
        f"SMTP failed: {exc}\n"
        f"Config usata: host={host} port={port} mode={mode}\n"
        "Cause probabili: porta SMTP errata, ssl/starttls invertiti, host SMTP errato, porta filtrata dal provider/server.\n"
        "Combinazioni comuni: 587 starttls=true ssl=false; 465 starttls=false ssl=true; 25 starttls=false ssl=false."
    )


def cooldown_active(data_dir: Path, server: str, config: dict[str, Any]) -> bool:
    cooldown_hours = as_float((config.get("alerts") or {}).get("cooldown_hours"))
    if cooldown_hours is None or cooldown_hours <= 0:
        return False
    state_path = alert_state_path(data_dir, server)
    if not state_path.exists():
        return False
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    last_sent = parse_iso(payload.get("last_sent_at_utc"))
    return datetime.now(timezone.utc) - last_sent < timedelta(hours=cooldown_hours)


def save_alert_state(data_dir: Path, server: str) -> None:
    path = alert_state_path(data_dir, server)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_sent_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}, indent=2), encoding="utf-8")


def alert_state_path(data_dir: Path, server: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in server)
    return data_dir.expanduser() / ".notification-state" / f"{safe}-alert.json"


def select_window(history: list[dict[str, Any]], hours: int) -> list[dict[str, Any]]:
    if not history:
        return []
    cutoff = parse_iso(history[-1].get("generated_at_utc")) - timedelta(hours=max(hours, 1))
    return [payload for payload in history if parse_iso(payload.get("generated_at_utc")) >= cutoff]


def disk_growth_gb(history: list[dict[str, Any]], hours: int) -> float | None:
    window = select_window(history, hours)
    if len(window) < 2:
        return None
    first = as_float(((window[0].get("server_disk") or {}).get("used_bytes")))
    latest = as_float(((window[-1].get("server_disk") or {}).get("used_bytes")))
    if first is None or latest is None:
        return None
    return round((latest - first) / (1024 * 1024 * 1024), 2)


def load_report_history(data_dir: Path, server_name: str, pattern: str) -> list[dict[str, Any]]:
    base = data_dir / server_name
    if not base.exists():
        return []
    payloads = []
    for path in sorted(base.rglob(pattern)):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("server_name") and payload.get("server_name") != server_name:
            continue
        payloads.append(payload)
    payloads.sort(key=lambda item: parse_iso(item.get("generated_at_utc")))
    return payloads


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


def avg_number(values: list[Any]) -> float | None:
    numbers = numbers_only(values)
    if not numbers:
        return None
    return round(sum(numbers) / len(numbers), 2)


def peak_number(values: list[Any]) -> float | None:
    numbers = numbers_only(values)
    if not numbers:
        return None
    return round(max(numbers), 2)


def last_number(values: list[Any]) -> float | None:
    numbers = numbers_only(values)
    if not numbers:
        return None
    return numbers[-1]


def numbers_only(values: list[Any]) -> list[float]:
    return [number for value in values if (number := as_float(value)) is not None]


def percent(value: float | None, total: float | None) -> float | None:
    if value is None or total is None or total <= 0:
        return None
    return round((value / total) * 100, 2)


def as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def bytes_to_gb(value: Any) -> str:
    number = bytes_to_gb_number(value)
    return "n/a" if number is None else str(round(number, 2))


def bytes_to_gb_number(value: Any) -> float | None:
    number = as_float(value)
    if number is None:
        return None
    return number / (1024 * 1024 * 1024)


def format_number(value: Any) -> str:
    number = as_float(value)
    if number is None:
        return "n/a"
    if float(number).is_integer():
        return str(int(number))
    return str(round(number, 2))


def format_pct(value: float | None) -> str:
    return "n/a%" if value is None else f"{value}%"


def format_gb(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value > 0 else ""
    return f"{sign}{value} GB"


def require_value(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if not value:
        raise SystemExit(f"SMTP config missing smtp.{key}.")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
