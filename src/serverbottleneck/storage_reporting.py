from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .reporting import filesystem_safe


def build_storage_report_paths(output_dir: Path, server_name: str, generated_at_utc: str) -> dict[str, Path]:
    safe_server = filesystem_safe(server_name)
    generated_at = parse_utc(generated_at_utc)
    day = generated_at.strftime("%Y-%m-%d")
    stamp = generated_at.strftime("%Y-%m-%dT%H-%M-%SZ")
    base = output_dir / safe_server / day
    return {
        "text": base / f"storage-{stamp}.txt",
        "json": base / f"storage-{stamp}.json",
        "csv": base / f"storage-growth-{stamp}.csv",
    }


def export_storage_json(payload: dict[str, Any], path: Path | None) -> None:
    text = json.dumps(clean_payload(payload), indent=2, default=str)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")


def write_storage_text_report(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_storage_text(payload) + "\n", encoding="utf-8")


def export_storage_csv(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "timestamp_utc",
                "server_name",
                "app_id",
                "total_mb",
                "delta_previous_mb",
                "delta_24h_mb",
                "growth_rate_mb_per_hour",
                "main_growth_bucket",
                "suspicion_score",
                "labels",
                "top_path",
            ]
        )
        for app in payload.get("apps") or []:
            previous = app.get("delta_previous") or {}
            delta_24h = app.get("delta_24h") or {}
            writer.writerow(
                [
                    payload.get("generated_at_utc"),
                    payload.get("server_name"),
                    app.get("app_id"),
                    bytes_to_mb((app.get("sizes_bytes") or {}).get("total")),
                    previous.get("total_mb", 0),
                    delta_24h.get("total_mb", 0),
                    previous.get("growth_rate_mb_per_hour", 0),
                    previous.get("main_growth_bucket"),
                    app.get("suspicion_score", 0),
                    "|".join(app.get("labels") or []),
                    first_top_path(app),
                ]
            )


def render_storage_text(payload: dict[str, Any]) -> str:
    lines = []
    disk = payload.get("server_disk") or {}
    lines.append(f"Storage report: {payload.get('server_name')}")
    lines.append(f"Generated at (UTC): {payload.get('generated_at_utc')}")
    lines.append("")
    lines.append("Server disk")
    lines.append(
        "  "
        f"used={bytes_to_gb(disk.get('used_bytes'))} GB / total={bytes_to_gb(disk.get('total_bytes'))} GB "
        f"free={bytes_to_gb(disk.get('free_bytes'))} GB used_pct={fmt(disk.get('used_pct'))}% inode_used_pct={fmt(disk.get('inode_used_pct'))}%"
    )
    lines.append("")
    lines.append("Top suspects")
    suspects = payload.get("top_suspects") or []
    if not suspects:
        lines.append("  none")
    for index, suspect in enumerate(suspects, start=1):
        lines.append(
            "  "
            f"{index}. {suspect.get('app_id')} "
            f"+{suspect.get('delta_previous_mb', 0)} MB previous "
            f"+{suspect.get('delta_24h_mb', 0)} MB 24h "
            f"bucket={suspect.get('main_growth_bucket') or '-'} "
            f"score={suspect.get('suspicion_score', 0)}"
        )
        if suspect.get("top_path"):
            lines.append(f"     top_path={suspect['top_path']}")
        labels = ", ".join(suspect.get("labels") or [])
        lines.append(f"     labels={labels or '-'}")
    lines.append("")
    lines.append("Top growth since previous snapshot")
    append_ranking(lines, payload, "top_growth_apps")
    lines.append("")
    lines.append("Top growth 24h")
    append_ranking(lines, payload, "top_growth_24h_apps")
    lines.append("")
    lines.append("Warnings")
    for warning in payload.get("warnings") or ["none"]:
        lines.append(f"  - {warning}")
    return "\n".join(lines)


def append_ranking(lines: list[str], payload: dict[str, Any], key: str) -> None:
    rows = (payload.get("rankings") or {}).get(key) or []
    if not rows:
        lines.append("  none")
        return
    for index, row in enumerate(rows[:10], start=1):
        lines.append(
            "  "
            f"{index}. {row.get('app_id')} +{row.get('total_mb', 0)} MB "
            f"rate={row.get('growth_rate_mb_per_hour', 0)} MB/h "
            f"bucket={row.get('main_growth_bucket') or '-'}"
        )


def clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "_path" not in payload:
        return payload
    cleaned = dict(payload)
    cleaned.pop("_path", None)
    return cleaned


def first_top_path(app: dict[str, Any]) -> str | None:
    top_dirs = app.get("top_directories") or []
    if top_dirs:
        return top_dirs[0].get("path")
    top_files = app.get("top_files") or []
    if top_files:
        return top_files[0].get("path")
    return None


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


def parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
