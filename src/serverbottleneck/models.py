from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class ServerSnapshot:
    timestamp: datetime
    source: str
    load_averages: tuple[float, float, float]
    ram_total_mb: float | None
    ram_used_mb: float | None
    ram_available_mb: float | None
    swap_total_mb: float | None
    swap_used_mb: float | None
    php_fpm_process_count: int
    top_cpu_processes: list[str]
    top_memory_processes: list[str]
    wp_related_processes: list[str]


@dataclass
class AppPaths:
    app_id: str
    app_root: Path
    log_dir: Path
    backend_access_logs: list[Path] = field(default_factory=list)
    static_access_logs: list[Path] = field(default_factory=list)
    php_access_logs: list[Path] = field(default_factory=list)
    php_slow_logs: list[Path] = field(default_factory=list)
    wp_cron_logs: list[Path] = field(default_factory=list)
    backend_error_logs: list[Path] = field(default_factory=list)


@dataclass
class RankedApp:
    app: AppPaths
    request_count: int
    suspicion_score: int = 0
    priority: str | None = None
    categories: list[str] = field(default_factory=list)
    home_url: str | None = None
    blogname: str | None = None


@dataclass
class RequestRecord:
    timestamp: datetime
    ip: str
    method: str
    request_target: str
    status: int
    bytes_sent: int | None
    referer: str | None
    user_agent: str | None

    @property
    def path(self) -> str:
        if " " in self.request_target:
            parts = self.request_target.split(" ")
            if len(parts) >= 2:
                return parts[1]
        return self.request_target


@dataclass
class PhpAccessRecord:
    timestamp: datetime
    ip: str
    method: str
    script_target: str
    status: int
    duration_sec: float | None
    memory_bytes: int | None
    cpu_pct: float | None
    io_pct: float | None
    final_request_target: str | None


@dataclass
class CronRecord:
    timestamp: datetime | None
    kind: str
    event_name: str | None = None
    duration_sec: float | None = None
    message: str | None = None


@dataclass
class ErrorRecord:
    timestamp: datetime | None
    severity: str | None
    signature: str
    file_hint: str | None
    raw_message: str


@dataclass
class SlowLogEvent:
    timestamp: datetime | None
    pool: str | None
    pid: int | None
    script_filename: str | None
    stack_lines: list[str]
    plugin_paths: list[str]
    plugin_slugs: list[str]
    signature: str
    functional_categories: list[str]


@dataclass
class AppAnalysis:
    ranked_app: RankedApp
    priority: str
    suspicion_score: int
    categories: list[str]
    backend_summary: dict
    static_summary: dict
    php_summary: dict
    php_slow_summary: dict
    cron_summary: dict
    error_summary: dict
    enrichment: dict


@dataclass
class AnalysisReport:
    server_name: str
    inspection_timestamp: datetime
    fixture_mode: bool
    snapshot: ServerSnapshot
    ranking_window_start: datetime
    ranking_window_end: datetime
    ranked_apps: list[RankedApp]
    app_analyses: list[AppAnalysis]
    actionable_warnings: list[str]
