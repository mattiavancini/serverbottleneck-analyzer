from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .discovery import discover_applications

TOP_DIRS_LIMIT = 100
TOP_DIRS_DEPTH = 4
TOP_FILES_LIMIT = 20
RECENT_FILES_WINDOW_HOURS = 24
DU_TIMEOUT_SEC = 20
MAX_FILE_SCAN_ITEMS = 8000
MAX_FILE_SCAN_DEPTH = 8


def collect_storage_report(
    applications_root: Path | None,
    server_name: str,
    fixture_mode: bool = False,
    generated_at: datetime | None = None,
    previous_payload: dict[str, Any] | None = None,
    baseline_24h_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timestamp = generated_at or datetime.now(timezone.utc)
    apps = discover_applications(applications_root)
    resolved_root = (applications_root or Path("~/applications").expanduser()).resolve()
    previous_apps = apps_by_id(previous_payload)
    baseline_apps = apps_by_id(baseline_24h_payload)

    app_payloads = []
    for app in apps:
        current = collect_app_storage(app.app_id, app.app_root, app.log_dir, fixture_mode)
        add_delta(current, previous_apps.get(app.app_id), "delta_previous", timestamp, previous_payload)
        add_delta(current, baseline_apps.get(app.app_id), "delta_24h", timestamp, baseline_24h_payload)
        current["labels"] = classify_storage_app(current)
        current["suspicion_score"] = compute_storage_score(current)
        app_payloads.append(current)

    app_payloads.sort(key=storage_app_sort_key)
    rankings = build_rankings(app_payloads)
    top_suspects = build_top_suspects(app_payloads)
    warnings = build_storage_warnings(rankings, top_suspects)

    return {
        "contract_version": "serverbottleneck.storage.v1",
        "generated_at_utc": isoformat_utc(timestamp),
        "server_name": server_name,
        "fixture_mode": fixture_mode,
        "collection_policy": {
            "max_depth": MAX_FILE_SCAN_DEPTH,
            "top_dirs_depth": TOP_DIRS_DEPTH,
            "max_file_scan_depth": MAX_FILE_SCAN_DEPTH,
            "top_dirs_limit": TOP_DIRS_LIMIT,
            "top_files_limit": TOP_FILES_LIMIT,
            "recent_files_window_hours": RECENT_FILES_WINDOW_HOURS,
            "max_file_scan_items_per_app": MAX_FILE_SCAN_ITEMS,
            "du_timeout_sec": DU_TIMEOUT_SEC,
            "excluded_paths": [],
        },
        "server_disk": collect_fixture_disk_snapshot() if fixture_mode else collect_disk_snapshot(resolved_root),
        "apps": app_payloads,
        "rankings": rankings,
        "top_suspects": top_suspects,
        "warnings": warnings,
    }


def collect_app_storage(app_id: str, app_root: Path, log_dir: Path, fixture_mode: bool) -> dict[str, Any]:
    paths = known_storage_paths(app_root, log_dir)
    sizes = {name: size_path(path, fixture_mode) for name, path in paths.items()}
    roots_for_file_scan = candidate_file_scan_roots(paths)
    return {
        "app_id": app_id,
        "app_root": str(app_root),
        "sizes_bytes": sizes,
        "paths": {name: str(path) for name, path in paths.items()},
        "top_directories": [] if fixture_mode else top_directories(app_root),
        "top_files": [] if fixture_mode else top_files(roots_for_file_scan, TOP_FILES_LIMIT, recent_only=False),
        "recent_large_files": [] if fixture_mode else top_files(roots_for_file_scan, TOP_FILES_LIMIT, recent_only=True),
    }


def known_storage_paths(app_root: Path, log_dir: Path) -> dict[str, Path]:
    public_html = app_root / "public_html"
    wp_content = public_html / "wp-content"
    uploads = wp_content / "uploads"
    return {
        "total": app_root,
        "logs": log_dir,
        "public_html": public_html,
        "wp_content": wp_content,
        "cache": wp_content / "cache",
        "uploads": uploads,
        "wpallimport": uploads / "wpallimport",
        "local_backups": app_root / "local_backups",
        "tmp": app_root / "tmp",
        "debug_log": wp_content / "debug.log",
    }


def candidate_file_scan_roots(paths: dict[str, Path]) -> list[Path]:
    candidates = [
        paths["logs"],
        paths["cache"],
        paths["uploads"],
        paths["wpallimport"],
        paths["local_backups"],
        paths["tmp"],
        paths["debug_log"],
    ]
    seen: set[Path] = set()
    roots = []
    for path in candidates:
        resolved = path.resolve() if path.exists() else path
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(path)
    return roots


def size_path(path: Path, fixture_mode: bool = False) -> int:
    if fixture_mode:
        return 0
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    du_size = du_size_bytes(path)
    if du_size is not None:
        return du_size
    return python_size_bytes(path, max_items=MAX_FILE_SCAN_ITEMS)


def du_size_bytes(path: Path) -> int | None:
    commands = (
        ["du", "-s", "-B1", str(path)],
        ["du", "-sk", str(path)],
    )
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=DU_TIMEOUT_SEC)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode != 0 or not proc.stdout.strip():
            continue
        first = proc.stdout.splitlines()[0].split()[0]
        try:
            value = int(first)
        except ValueError:
            continue
        if "-sk" in cmd:
            value *= 1024
        return value
    return None


def python_size_bytes(path: Path, max_items: int) -> int:
    total = 0
    seen = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [item for item in dirs if not should_skip_dir(item)]
        for filename in files:
            seen += 1
            if seen > max_items:
                return total
            try:
                total += (Path(root) / filename).stat().st_size
            except OSError:
                continue
    return total


def top_directories(app_root: Path) -> list[dict[str, Any]]:
    if not app_root.exists():
        return []
    rows = du_depth_rows(app_root)
    rows = [row for row in rows if row["path"] != str(app_root)]
    rows.sort(key=lambda item: (-item["size_bytes"], item["path"]))
    return rows[:TOP_DIRS_LIMIT]


def du_depth_rows(path: Path) -> list[dict[str, Any]]:
    commands = (
        ["du", "-x", "-B1", f"--max-depth={TOP_DIRS_DEPTH}", str(path)],
        ["du", "-x", "-B1", "-d", str(TOP_DIRS_DEPTH), str(path)],
        ["du", "-k", "-d", str(TOP_DIRS_DEPTH), str(path)],
    )
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=DU_TIMEOUT_SEC)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode != 0 or not proc.stdout.strip():
            continue
        multiplier = 1024 if "-k" in cmd else 1
        rows = []
        for line in proc.stdout.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue
            try:
                size = int(parts[0]) * multiplier
            except ValueError:
                continue
            rows.append({"path": parts[1], "size_bytes": size, "size_mb": bytes_to_mb(size)})
        if rows:
            return rows
    return python_top_directories(path)


def python_top_directories(path: Path) -> list[dict[str, Any]]:
    rows = []
    for child in safe_iterdir(path):
        if child.is_dir():
            size = python_size_bytes(child, max_items=MAX_FILE_SCAN_ITEMS)
            rows.append({"path": str(child), "size_bytes": size, "size_mb": bytes_to_mb(size)})
    return rows


def top_files(paths: list[Path], limit: int, recent_only: bool) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=RECENT_FILES_WINDOW_HOURS)
    items: list[dict[str, Any]] = []
    seen = 0
    for root in paths:
        if not root.exists():
            continue
        if root.is_file():
            item = file_payload(root)
            if item and (not recent_only or parse_iso(item["modified_at_utc"]) >= cutoff):
                items.append(item)
            continue
        for current_root, dirs, files in os.walk(root):
            depth = relative_depth(Path(current_root), root)
            if depth >= MAX_FILE_SCAN_DEPTH:
                dirs[:] = []
            else:
                dirs[:] = [item for item in dirs if not should_skip_dir(item)]
            for filename in files:
                seen += 1
                if seen > MAX_FILE_SCAN_ITEMS:
                    break
                item = file_payload(Path(current_root) / filename)
                if not item:
                    continue
                if recent_only and parse_iso(item["modified_at_utc"]) < cutoff:
                    continue
                items.append(item)
            if seen > MAX_FILE_SCAN_ITEMS:
                break
    items.sort(key=lambda item: (-item["size_bytes"], item["path"]))
    return items[:limit]


def file_payload(path: Path) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return {
        "path": str(path),
        "size_bytes": stat.st_size,
        "size_mb": bytes_to_mb(stat.st_size),
        "modified_at_utc": isoformat_utc(modified),
    }


def collect_disk_snapshot(reference_path: Path) -> dict[str, Any]:
    usage = None
    try:
        usage = shutil.disk_usage(reference_path)
    except OSError:
        try:
            usage = shutil.disk_usage(reference_path.anchor or "/")
        except OSError:
            usage = None
    filesystems = collect_df(reference_path)
    inode = collect_inode_usage(reference_path)
    if usage is None:
        return {"filesystems": filesystems, "total_bytes": None, "used_bytes": None, "free_bytes": None, "used_pct": None, "inode_used_pct": inode}
    used_pct = round((usage.used / usage.total) * 100, 2) if usage.total else None
    return {
        "filesystems": filesystems,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_pct": used_pct,
        "inode_used_pct": inode,
    }


def collect_fixture_disk_snapshot() -> dict[str, Any]:
    return {
        "filesystems": [],
        "total_bytes": None,
        "used_bytes": None,
        "free_bytes": None,
        "used_pct": None,
        "inode_used_pct": None,
    }


def collect_df(reference_path: Path) -> list[dict[str, str]]:
    try:
        proc = subprocess.run(["df", "-P", str(reference_path)], check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    parts = lines[1].split()
    if len(parts) < 6:
        return []
    return [{"filesystem": parts[0], "size_1k": parts[1], "used_1k": parts[2], "available_1k": parts[3], "used_pct": parts[4], "mounted_on": parts[5]}]


def collect_inode_usage(reference_path: Path) -> float | None:
    try:
        proc = subprocess.run(["df", "-Pi", str(reference_path)], check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    parts = lines[1].split()
    if len(parts) < 5:
        return None
    try:
        return float(parts[4].rstrip("%"))
    except ValueError:
        return None


def add_delta(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    key: str,
    timestamp: datetime,
    previous_payload: dict[str, Any] | None,
) -> None:
    if not previous or not previous_payload:
        current[key] = empty_delta()
        return
    previous_ts = parse_iso(previous_payload.get("generated_at_utc"))
    hours = max((timestamp - previous_ts).total_seconds() / 3600, 0.001)
    total_delta = current["sizes_bytes"].get("total", 0) - (previous.get("sizes_bytes") or {}).get("total", 0)
    bucket_deltas = {}
    for bucket, value in current["sizes_bytes"].items():
        old_value = (previous.get("sizes_bytes") or {}).get(bucket, 0)
        bucket_deltas[bucket] = value - old_value
    current[key] = {
        "previous_snapshot_utc": isoformat_utc(previous_ts),
        "hours_between": round(hours, 3),
        "total_bytes": total_delta,
        "total_mb": bytes_to_mb(total_delta),
        "growth_rate_mb_per_hour": round(bytes_to_mb(total_delta) / hours, 2),
        "bucket_deltas_bytes": bucket_deltas,
        "main_growth_bucket": main_growth_bucket(bucket_deltas),
    }


def empty_delta() -> dict[str, Any]:
    return {
        "previous_snapshot_utc": None,
        "hours_between": None,
        "total_bytes": 0,
        "total_mb": 0.0,
        "growth_rate_mb_per_hour": 0.0,
        "bucket_deltas_bytes": {},
        "main_growth_bucket": None,
    }


def main_growth_bucket(deltas: dict[str, int]) -> str | None:
    candidates = {key: value for key, value in deltas.items() if key not in {"total", "public_html", "wp_content"}}
    positives = [(key, value) for key, value in candidates.items() if value > 0]
    if not positives:
        return None
    positives.sort(key=lambda item: (-item[1], item[0]))
    return positives[0][0]


def classify_storage_app(app: dict[str, Any]) -> list[str]:
    labels = []
    delta = app.get("delta_previous") or {}
    bucket = delta.get("main_growth_bucket")
    total_mb = delta.get("total_mb") or 0
    rate = delta.get("growth_rate_mb_per_hour") or 0
    sizes = app.get("sizes_bytes") or {}
    if bucket == "logs":
        labels.append("log_growth")
    if bucket == "cache":
        labels.append("cache_growth")
    if bucket == "uploads":
        labels.append("upload_growth")
    if bucket == "wpallimport":
        labels.append("wpallimport_growth")
    if bucket == "local_backups":
        labels.append("backup_accumulation")
    if bucket == "tmp":
        labels.append("tmp_growth")
    if bucket == "debug_log" or sizes.get("debug_log", 0) >= 100 * 1024 * 1024:
        labels.append("debug_log_large")
    if total_mb >= 1024 or rate >= 512:
        labels.append("fast_growth")
    if total_mb >= 5120:
        labels.append("critical_growth")
    return labels or ["stable_or_unknown"]


def compute_storage_score(app: dict[str, Any]) -> int:
    delta = app.get("delta_previous") or {}
    delta_24h = app.get("delta_24h") or {}
    score = 0
    score += min(int(max(delta.get("total_mb") or 0, 0) // 100), 40)
    score += min(int(max(delta_24h.get("total_mb") or 0, 0) // 250), 40)
    score += min(int(max(delta.get("growth_rate_mb_per_hour") or 0, 0) // 50), 30)
    labels = set(app.get("labels") or [])
    if "critical_growth" in labels:
        score += 30
    if "fast_growth" in labels:
        score += 20
    if labels & {"log_growth", "wpallimport_growth", "backup_accumulation", "debug_log_large"}:
        score += 10
    return score


def build_rankings(apps: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "top_growth_apps": compact_app_rows(apps, "delta_previous"),
        "top_growth_24h_apps": compact_app_rows(apps, "delta_24h"),
        "top_total_apps": compact_total_rows(apps),
        "top_large_files": top_large_files_global(apps),
    }


def compact_app_rows(apps: list[dict[str, Any]], delta_key: str) -> list[dict[str, Any]]:
    rows = []
    for app in apps:
        delta = app.get(delta_key) or {}
        if (delta.get("total_bytes") or 0) <= 0:
            continue
        rows.append(
            {
                "app_id": app["app_id"],
                "total_mb": delta.get("total_mb", 0),
                "growth_rate_mb_per_hour": delta.get("growth_rate_mb_per_hour", 0),
                "main_growth_bucket": delta.get("main_growth_bucket"),
                "labels": app.get("labels", []),
                "suspicion_score": app.get("suspicion_score", 0),
            }
        )
    rows.sort(key=lambda item: (-(item["total_mb"] or 0), item["app_id"]))
    return rows[:20]


def compact_total_rows(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        {
            "app_id": app["app_id"],
            "total_mb": bytes_to_mb((app.get("sizes_bytes") or {}).get("total", 0)),
            "labels": app.get("labels", []),
        }
        for app in apps
    ]
    rows.sort(key=lambda item: (-(item["total_mb"] or 0), item["app_id"]))
    return rows[:20]


def top_large_files_global(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for app in apps:
        for item in app.get("top_files") or []:
            rows.append({"app_id": app["app_id"], **item})
    rows.sort(key=lambda item: (-(item["size_bytes"] or 0), item["app_id"], item["path"]))
    return rows[:20]


def build_top_suspects(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    suspects = [app for app in apps if app.get("suspicion_score", 0) > 0]
    suspects.sort(key=storage_app_sort_key)
    return [
        {
            "app_id": app["app_id"],
            "suspicion_score": app.get("suspicion_score", 0),
            "delta_previous_mb": (app.get("delta_previous") or {}).get("total_mb", 0),
            "delta_24h_mb": (app.get("delta_24h") or {}).get("total_mb", 0),
            "growth_rate_mb_per_hour": (app.get("delta_previous") or {}).get("growth_rate_mb_per_hour", 0),
            "main_growth_bucket": (app.get("delta_previous") or {}).get("main_growth_bucket"),
            "labels": app.get("labels", []),
            "top_path": first_top_path(app),
        }
        for app in suspects[:5]
    ]


def build_storage_warnings(rankings: dict[str, Any], top_suspects: list[dict[str, Any]]) -> list[str]:
    warnings = []
    for suspect in top_suspects:
        delta_mb = suspect.get("delta_previous_mb") or 0
        if delta_mb >= 1024:
            warnings.append(f"{suspect['app_id']}: storage grew by {delta_mb:.1f} MB since previous snapshot.")
    if not warnings and not rankings.get("top_growth_apps"):
        warnings.append("No positive storage growth detected against the previous snapshot.")
    return warnings


def storage_app_sort_key(app: dict[str, Any]) -> tuple[Any, ...]:
    delta = app.get("delta_previous") or {}
    delta_24h = app.get("delta_24h") or {}
    return (
        -(app.get("suspicion_score") or 0),
        -(delta.get("total_bytes") or 0),
        -(delta_24h.get("total_bytes") or 0),
        app.get("app_id") or "",
    )


def first_top_path(app: dict[str, Any]) -> str | None:
    top_dirs = app.get("top_directories") or []
    if top_dirs:
        return top_dirs[0].get("path")
    top_files_items = app.get("top_files") or []
    if top_files_items:
        return top_files_items[0].get("path")
    return None


def load_storage_history(data_dir: Path, server_name: str) -> list[dict[str, Any]]:
    base = data_dir / filesystem_safe(server_name)
    if not base.exists():
        base = data_dir / server_name
    if not base.exists():
        return []
    payloads = []
    for path in sorted(base.rglob("storage-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload["_path"] = str(path)
        payloads.append(payload)
    payloads.sort(key=lambda item: parse_iso(item.get("generated_at_utc")))
    return payloads


def select_previous_payload(history: list[dict[str, Any]], timestamp: datetime) -> dict[str, Any] | None:
    candidates = [payload for payload in history if parse_iso(payload.get("generated_at_utc")) < timestamp]
    return candidates[-1] if candidates else None


def select_baseline_payload(history: list[dict[str, Any]], timestamp: datetime, hours: int) -> dict[str, Any] | None:
    target = timestamp - timedelta(hours=hours)
    candidates = [payload for payload in history if parse_iso(payload.get("generated_at_utc")) <= target]
    return candidates[-1] if candidates else None


def apps_by_id(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not payload:
        return {}
    return {str(app.get("app_id")): app for app in payload.get("apps") or [] if app.get("app_id")}


def safe_iterdir(path: Path):
    try:
        return list(path.iterdir())
    except OSError:
        return []


def should_skip_dir(name: str) -> bool:
    return name in {".git", ".svn", ".hg"}


def relative_depth(path: Path, root: Path) -> int:
    try:
        return len(path.relative_to(root).parts)
    except ValueError:
        return 0


def bytes_to_mb(value: int | float | None) -> float:
    if value is None:
        return 0.0
    return round(value / (1024 * 1024), 2)


def parse_iso(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def filesystem_safe(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value.strip())
    return cleaned or "unknown-server"
