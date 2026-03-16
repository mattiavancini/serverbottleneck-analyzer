from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def collect_wp_enrichment(app_root: Path, timeout: int = 10) -> dict:
    wp_binary = shutil.which("wp")
    if not wp_binary:
        return {
            "home_url": None,
            "blogname": None,
            "plugins": [],
            "cron_events": [],
        }
    public_html = app_root / "public_html"
    if not public_html.is_dir():
        public_html = app_root
    return {
        "home_url": _run_wp(wp_binary, public_html, ["option", "get", "home"], timeout),
        "blogname": _run_wp(wp_binary, public_html, ["option", "get", "blogname"], timeout),
        "plugins": _run_wp_lines(wp_binary, public_html, ["plugin", "list", "--status=active", "--field=name"], timeout),
        "cron_events": _run_wp_lines(wp_binary, public_html, ["cron", "event", "list", "--fields=hook", "--format=csv"], timeout),
    }


def fixture_wp_enrichment() -> dict:
    return {
        "home_url": None,
        "blogname": None,
        "plugins": [],
        "cron_events": [],
        "mode": "fixture-skipped",
    }


def _run_wp(wp_binary: str, cwd: Path, args: list[str], timeout: int) -> str | None:
    try:
        proc = subprocess.run(
            [wp_binary, *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = proc.stdout.strip()
    return output or None


def _run_wp_lines(wp_binary: str, cwd: Path, args: list[str], timeout: int) -> list[str]:
    value = _run_wp(wp_binary, cwd, args, timeout)
    if not value:
        return []
    return [line.strip() for line in value.splitlines() if line.strip()]
