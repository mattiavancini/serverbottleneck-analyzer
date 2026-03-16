from __future__ import annotations

from pathlib import Path

from .models import AppPaths


def resolve_applications_root() -> Path:
    applications = Path("~/applications").expanduser()
    return applications.resolve()


def discover_applications(root: Path | None = None) -> list[AppPaths]:
    applications_root = root or resolve_applications_root()
    apps: list[AppPaths] = []
    if not applications_root.exists():
        return apps

    for app_root in sorted(p for p in applications_root.iterdir() if p.is_dir()):
        log_dir = app_root / "logs"
        if not log_dir.is_dir():
            continue
        app = AppPaths(
            app_id=app_root.name,
            app_root=app_root,
            log_dir=log_dir,
            backend_access_logs=sorted(log_dir.glob("backend_wordpress*.access.log")),
            static_access_logs=sorted(log_dir.glob("static_wordpress*.access.log")),
            php_access_logs=sorted(log_dir.glob("php-app.access.log")),
            php_slow_logs=sorted(log_dir.glob("php-app.slow.log*")),
            wp_cron_logs=sorted(log_dir.glob("wp-cron.log")),
            backend_error_logs=sorted(log_dir.glob("backend_wordpress*.error.log")),
        )
        apps.append(app)
    return apps
