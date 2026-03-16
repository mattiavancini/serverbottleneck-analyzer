from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone

from .models import ServerSnapshot


def collect_server_snapshot() -> ServerSnapshot:
    load_avg = os.getloadavg()
    meminfo = _read_meminfo()
    top_cpu = _run_ps_sorted("pcpu")
    top_mem = _run_ps_sorted("pmem")
    processes = _run_ps_plain()
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
