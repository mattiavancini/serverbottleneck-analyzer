from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone

from .models import ServerSnapshot


def collect_server_snapshot() -> ServerSnapshot:
    load_avg = os.getloadavg()
    meminfo = _read_meminfo()
    top_cpu = _run_ps_sorted("pcpu")
    top_mem = _run_ps_sorted("pmem")
    processes = _run_ps_plain()
    redis_metrics = _collect_redis_metrics(processes)
    php_fpm_count = sum(1 for line in processes if "php-fpm" in line or "php-fpm8" in line)
    wp_related = [
        line for line in processes
        if "wp cron event run" in line or "wp cron event list" in line or "wp-cli" in line or "wp-cron.php" in line
    ][:10]
    return ServerSnapshot(
        timestamp=datetime.now(timezone.utc),
        source="live-server",
        load_averages=load_avg,
        ram_total_mb=_kb_to_mb(meminfo.get("MemTotal")),
        ram_used_mb=_calc_mem_used(meminfo),
        ram_available_mb=_kb_to_mb(meminfo.get("MemAvailable")),
        swap_total_mb=_kb_to_mb(meminfo.get("SwapTotal")),
        swap_used_mb=_calc_swap_used(meminfo),
        php_fpm_process_count=php_fpm_count,
        top_cpu_processes=top_cpu[:5],
        top_memory_processes=top_mem[:5],
        wp_related_processes=wp_related[:10],
        redis_detected=redis_metrics["redis_detected"],
        redis_reachable=redis_metrics["redis_reachable"],
        redis_used_memory_human=redis_metrics["redis_used_memory_human"],
        redis_used_memory_peak_human=redis_metrics["redis_used_memory_peak_human"],
        redis_connected_clients=redis_metrics["redis_connected_clients"],
        redis_keyspace_hits=redis_metrics["redis_keyspace_hits"],
        redis_keyspace_misses=redis_metrics["redis_keyspace_misses"],
        redis_evicted_keys=redis_metrics["redis_evicted_keys"],
        redis_uptime_in_seconds=redis_metrics["redis_uptime_in_seconds"],
        redis_status=redis_metrics["redis_status"],
    )


def collect_fixture_snapshot() -> ServerSnapshot:
    return ServerSnapshot(
        timestamp=datetime.now(timezone.utc),
        source="fixture",
        load_averages=(0.0, 0.0, 0.0),
        ram_total_mb=None,
        ram_used_mb=None,
        ram_available_mb=None,
        swap_total_mb=None,
        swap_used_mb=None,
        php_fpm_process_count=0,
        top_cpu_processes=[],
        top_memory_processes=[],
        wp_related_processes=[],
        redis_detected=False,
        redis_reachable=False,
        redis_used_memory_human=None,
        redis_used_memory_peak_human=None,
        redis_connected_clients=None,
        redis_keyspace_hits=None,
        redis_keyspace_misses=None,
        redis_evicted_keys=None,
        redis_uptime_in_seconds=None,
        redis_status="UNAVAILABLE",
    )


def _read_meminfo() -> dict[str, int]:
    results: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if ":" not in line:
                    continue
                key, raw = line.split(":", 1)
                value = raw.strip().split()[0]
                results[key] = int(value)
    except OSError:
        pass
    return results


def _run_ps(cmd: list[str]) -> list[str]:
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except OSError:
        return []
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return lines[1:]


def _run_ps_sorted(sort_key: str) -> list[str]:
    commands = [
        ["ps", "-eo", "pid,pcpu,pmem,comm,args", f"--sort=-{sort_key}"],
        ["ps", "-axo", "pid,pcpu,pmem,comm,args", "-r"],
    ]
    for cmd in commands:
        lines = _run_ps(cmd)
        if lines:
            return lines
    return []


def _run_ps_plain() -> list[str]:
    commands = [
        ["ps", "-eo", "comm,args"],
        ["ps", "-axo", "comm,args"],
    ]
    for cmd in commands:
        lines = _run_ps(cmd)
        if lines:
            return lines
    return []


def _kb_to_mb(value: int | None) -> float | None:
    if value is None:
        return None
    return round(value / 1024.0, 1)


def _calc_mem_used(meminfo: dict[str, int]) -> float | None:
    total = meminfo.get("MemTotal")
    avail = meminfo.get("MemAvailable")
    if total is None or avail is None:
        return None
    return round((total - avail) / 1024.0, 1)


def _calc_swap_used(meminfo: dict[str, int]) -> float | None:
    total = meminfo.get("SwapTotal")
    free = meminfo.get("SwapFree")
    if total is None or free is None:
        return None
    return round((total - free) / 1024.0, 1)


def _collect_redis_metrics(processes: list[str]) -> dict:
    redis_detected = any("redis-server" in line for line in processes) or shutil.which("redis-cli") is not None
    defaults = {
        "redis_detected": redis_detected,
        "redis_reachable": False,
        "redis_used_memory_human": None,
        "redis_used_memory_peak_human": None,
        "redis_connected_clients": None,
        "redis_keyspace_hits": None,
        "redis_keyspace_misses": None,
        "redis_evicted_keys": None,
        "redis_uptime_in_seconds": None,
        "redis_status": "UNAVAILABLE",
    }
    redis_cli = shutil.which("redis-cli")
    if not redis_cli:
        return defaults
    try:
        proc = subprocess.run(
            [redis_cli, "--raw", "INFO", "memory", "stats", "clients", "server"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        defaults["redis_status"] = "DEGRADED" if redis_detected else "UNAVAILABLE"
        return defaults
    if proc.returncode != 0 or not proc.stdout.strip() or "NOAUTH" in proc.stdout or "NOAUTH" in proc.stderr:
        defaults["redis_status"] = "DEGRADED" if redis_detected else "UNAVAILABLE"
        return defaults

    info = _parse_redis_info(proc.stdout)
    keyspace_hits = _to_int(info.get("keyspace_hits"))
    keyspace_misses = _to_int(info.get("keyspace_misses"))
    evicted_keys = _to_int(info.get("evicted_keys"))
    degraded = bool((evicted_keys or 0) > 0)
    if keyspace_hits is not None and keyspace_misses is not None and keyspace_misses > keyspace_hits:
        degraded = True

    return {
        "redis_detected": True,
        "redis_reachable": True,
        "redis_used_memory_human": info.get("used_memory_human"),
        "redis_used_memory_peak_human": info.get("used_memory_peak_human"),
        "redis_connected_clients": _to_int(info.get("connected_clients")),
        "redis_keyspace_hits": keyspace_hits,
        "redis_keyspace_misses": keyspace_misses,
        "redis_evicted_keys": evicted_keys,
        "redis_uptime_in_seconds": _to_int(info.get("uptime_in_seconds")),
        "redis_status": "DEGRADED" if degraded else "OK",
    }


def _parse_redis_info(output: str) -> dict[str, str]:
    info: dict[str, str] = {}
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        info[key] = value
    return info


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
