from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import CronRecord, ErrorRecord, PhpAccessRecord, RequestRecord, SlowLogEvent

COMBINED_RE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "(?P<method>\S+) (?P<target>[^"]+?) (?P<protocol>\S+)" '
    r'(?P<status>\d{3}) (?P<bytes>\S+) "(?P<referer>[^"]*)" "(?P<ua>[^"]*)"$'
)

PHP_APP_RE = re.compile(
    r'^(?P<ip>\S+) - \[(?P<ts>[^\]]+)\] "(?P<method>\S+) (?P<script>[^"]+)" '
    r'(?P<status>\d{3}) (?P<metrics>.+) "(?P<final_target>[^"]*)"$'
)

CRON_EVENT_RE = re.compile(
    r"^Executed the cron event '(?P<event>[^']+)' in (?P<duration>[0-9.]+)s\.$"
)
CRON_RUN_RE = re.compile(r"^(?P<weekday>\w{3}) (?P<day>\d{1,2}) (?P<mon>\w{3}) (?P<year>\d{4}) (?P<time>.+?) UTC ")
CRON_TOTAL_RE = re.compile(r"^Success: Executed a total of (?P<count>\d+) cron events\.$")

ERROR_SIG_RE = re.compile(
    r"\bPHP\s+(?P<severity>Recoverable fatal error|Fatal error|Parse error|Compile error|Core warning|Warning|Notice|Deprecated|Error)\s*:\s*(?P<message>.+)",
    re.IGNORECASE,
)
ERROR_BRACKET_TS_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]")
ERROR_NGINX_TS_RE = re.compile(r"^(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})")
FILE_HINT_RE = re.compile(r"(/[^\s:]+(?:\.php|\.inc))")
SLOW_HEADER_RE = re.compile(
    r"^\[(?P<ts>\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2}:\d{2})\]\s+\[pool (?P<pool>[^\]]+)\] pid (?P<pid>\d+)$"
)
SLOW_SCRIPT_RE = re.compile(r"^script_filename = (?P<script>.+)$")
PLUGIN_PATH_RE = re.compile(r"(/[^:\s]*?/wp-content/plugins/(?P<slug>[^/\s]+)/[^:\s]*)")

BOT_UA_MARKERS = (
    "bot",
    "crawler",
    "spider",
    "scan",
    "http-client",
    "go-http-client",
    "curl",
    "facebookexternalhit",
    "meta-externalagent",
)

SENSITIVE_ENDPOINTS = (
    "admin-ajax.php",
    "wp-login.php",
    "wp-cron.php",
    "wp-json",
    "xmlrpc.php",
    "wp-admin",
    "wp-load.php",
)

INTERNAL_IP_MARKERS = ("127.0.0.1", "::1")
FUNCTIONAL_CATEGORY_MARKERS = {
    "scheduler": ("action-scheduler", "action_scheduler", "scheduler", "wp-cron", "cron"),
    "cache": ("cache", "objectcache", "redis-cache", "memcached", "breeze"),
    "preload": ("preload", "pre-loader", "warmup"),
    "optimizer": ("optimize", "optimizer", "minify", "autoptimize", "imagify"),
    "seo": ("seo", "seopress", "rank-math", "yoast"),
    "ads": ("ads", "ad-inserter", "google-site-kit", "adsense"),
    "builder": ("elementor", "divi", "wpbakery", "bricks", "builder"),
    "mail/async": ("mail", "smtp", "newsletter", "queue", "async", "background"),
}


def parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%d/%b/%Y:%H:%M:%S %z").astimezone(timezone.utc)


def parse_combined_access(line: str) -> RequestRecord | None:
    match = COMBINED_RE.match(line.strip())
    if not match:
        return None
    bytes_raw = match.group("bytes")
    return RequestRecord(
        timestamp=parse_timestamp(match.group("ts")),
        ip=match.group("ip"),
        method=match.group("method"),
        request_target=f'{match.group("method")} {match.group("target")} {match.group("protocol")}',
        status=int(match.group("status")),
        bytes_sent=None if bytes_raw == "-" else int(bytes_raw),
        referer=None if match.group("referer") == "-" else match.group("referer"),
        user_agent=None if match.group("ua") == "-" else match.group("ua"),
    )


def parse_php_app_access(line: str) -> PhpAccessRecord | None:
    match = PHP_APP_RE.match(line.strip())
    if not match:
        return None
    metric_tokens = match.group("metrics").split()
    if len(metric_tokens) < 4:
        return None
    duration_raw, memory_raw, cpu_raw, io_raw = metric_tokens[-4:]
    return PhpAccessRecord(
        timestamp=parse_timestamp(match.group("ts")),
        ip=match.group("ip"),
        method=match.group("method"),
        script_target=match.group("script"),
        status=int(match.group("status")),
        duration_sec=_to_float(duration_raw),
        memory_bytes=_to_int(memory_raw),
        cpu_pct=_to_percent(cpu_raw),
        io_pct=_to_percent(io_raw),
        final_request_target=None if match.group("final_target") == "-" else match.group("final_target"),
    )


def parse_cron_line(line: str, current_timestamp: datetime | None) -> CronRecord | None:
    stripped = line.strip()
    if not stripped:
        return None
    run_match = CRON_RUN_RE.match(stripped)
    if run_match:
        timestamp = datetime.strptime(
            f"{run_match.group('day')} {run_match.group('mon')} {run_match.group('year')} {run_match.group('time')}",
            "%d %b %Y %I:%M:%S %p",
        ).replace(tzinfo=timezone.utc)
        return CronRecord(timestamp=timestamp, kind="run", message=stripped)
    event_match = CRON_EVENT_RE.match(stripped)
    if event_match:
        return CronRecord(
            timestamp=current_timestamp,
            kind="event",
            event_name=event_match.group("event"),
            duration_sec=float(event_match.group("duration")),
            message=stripped,
        )
    total_match = CRON_TOTAL_RE.match(stripped)
    if total_match:
        return CronRecord(
            timestamp=current_timestamp,
            kind="summary",
            message=stripped,
        )
    return CronRecord(timestamp=current_timestamp, kind="message", message=stripped)


def parse_error_line(line: str) -> ErrorRecord | None:
    stripped = line.strip()
    if not stripped:
        return None
    timestamp = parse_error_timestamp(stripped)
    severity = None
    signature = stripped
    message = stripped
    error_match = ERROR_SIG_RE.search(stripped)
    if error_match:
        severity = normalize_error_severity(error_match.group("severity"))
        message = error_match.group("message")
        signature = normalize_error_signature(message)
    file_hint_match = FILE_HINT_RE.search(stripped)
    file_hint = file_hint_match.group(1) if file_hint_match else None
    return ErrorRecord(
        timestamp=timestamp,
        severity=severity,
        signature=signature,
        file_hint=file_hint,
        raw_message=message,
    )


def parse_slow_log_blocks(lines) -> list[SlowLogEvent]:
    events: list[SlowLogEvent] = []
    current: list[str] = []
    for line in lines:
        stripped = line.rstrip("\n")
        if SLOW_HEADER_RE.match(stripped):
            if current:
                event = parse_slow_log_block(current)
                if event:
                    events.append(event)
            current = [stripped]
        elif current:
            current.append(stripped)
    if current:
        event = parse_slow_log_block(current)
        if event:
            events.append(event)
    return events


def parse_slow_log_block(lines: list[str]) -> SlowLogEvent | None:
    if not lines:
        return None
    header_match = SLOW_HEADER_RE.match(lines[0])
    if not header_match:
        return None
    timestamp = datetime.strptime(header_match.group("ts"), "%d-%b-%Y %H:%M:%S").replace(tzinfo=timezone.utc)
    script_filename = None
    stack_lines: list[str] = []
    plugin_paths: list[str] = []
    plugin_slugs: list[str] = []

    for line in lines[1:]:
        script_match = SLOW_SCRIPT_RE.match(line)
        if script_match:
            script_filename = script_match.group("script").strip()
        elif line.strip():
            stack_lines.append(line.strip())
        for path_match in PLUGIN_PATH_RE.finditer(line):
            plugin_path = path_match.group(1)
            plugin_slug = path_match.group("slug")
            plugin_paths.append(plugin_path)
            plugin_slugs.append(plugin_slug)

    normalized_slugs = sorted(set(plugin_slugs))
    signature = normalize_slow_signature(script_filename, stack_lines, normalized_slugs)
    categories = detect_functional_categories(" ".join([script_filename or "", *stack_lines, *normalized_slugs]))

    return SlowLogEvent(
        timestamp=timestamp,
        pool=header_match.group("pool"),
        pid=int(header_match.group("pid")),
        script_filename=script_filename,
        stack_lines=stack_lines,
        plugin_paths=sorted(set(plugin_paths)),
        plugin_slugs=normalized_slugs,
        signature=signature,
        functional_categories=categories,
    )


def normalize_error_signature(message: str) -> str:
    normalized = re.sub(r"\b\d+\b", "<n>", message)
    normalized = re.sub(r'"[^"]+"', '"<str>"', normalized)
    normalized = re.sub(r"'[^']+'", "'<str>'", normalized)
    return normalized.strip()


def normalize_error_severity(value: str) -> str:
    normalized = " ".join(value.lower().split())
    return {
        "warning": "Warning",
        "notice": "Notice",
        "deprecated": "Deprecated",
        "fatal error": "Fatal error",
        "parse error": "Parse error",
        "recoverable fatal error": "Recoverable fatal error",
        "compile error": "Compile error",
        "core warning": "Core warning",
        "error": "Error",
    }.get(normalized, value.strip())


def parse_error_timestamp(line: str) -> datetime | None:
    candidates: list[str] = []
    bracket_match = ERROR_BRACKET_TS_RE.match(line)
    if bracket_match:
        candidates.append(bracket_match.group("ts").strip())
    nginx_match = ERROR_NGINX_TS_RE.match(line)
    if nginx_match:
        candidates.append(nginx_match.group("ts").strip())

    for candidate in candidates:
        parsed = _parse_error_timestamp_candidate(candidate)
        if parsed:
            return parsed
    return None


def _parse_error_timestamp_candidate(value: str) -> datetime | None:
    formats = (
        "%d-%b-%Y %H:%M:%S %Z",
        "%d-%b-%Y %H:%M:%S",
        "%d/%b/%Y:%H:%M:%S %z",
        "%Y/%m/%d %H:%M:%S",
        "%a %b %d %H:%M:%S.%f %Y",
        "%a %b %d %H:%M:%S %Y",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def is_bot_user_agent(user_agent: str | None) -> bool:
    if not user_agent:
        return False
    ua = user_agent.lower()
    return any(marker in ua for marker in BOT_UA_MARKERS)


def is_internal_ip(ip: str) -> bool:
    return ip.startswith(INTERNAL_IP_MARKERS)


def normalize_slow_signature(script_filename: str | None, stack_lines: list[str], plugin_slugs: list[str]) -> str:
    script = script_filename or "<unknown-script>"
    top_stack = stack_lines[:3]
    normalized_stack = [re.sub(r"\b\d+\b", "<n>", line) for line in top_stack]
    if plugin_slugs:
        return f"{script} | plugins={','.join(plugin_slugs)} | {' || '.join(normalized_stack)}"
    return f"{script} | {' || '.join(normalized_stack)}"


def detect_functional_categories(text: str) -> list[str]:
    lowered = text.lower()
    results = [
        category
        for category, markers in FUNCTIONAL_CATEGORY_MARKERS.items()
        if any(marker in lowered for marker in markers)
    ]
    return results


def _to_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _to_percent(value: str) -> float | None:
    if value.endswith("%"):
        value = value[:-1]
    return _to_float(value)
