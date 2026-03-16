from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

from .analyzer import build_report
from .reporting import build_report_paths, export_csv, export_json, render_text, write_text_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hourly Cloudways-style WordPress pressure analyzer")
    parser.add_argument("--applications-root", type=Path, default=None, help="Override ~/applications discovery root")
    parser.add_argument("--top", type=int, default=5, help="Number of suspect apps to deep dive")
    parser.add_argument("--fixture-mode", action="store_true", help="Skip live system snapshot and wp-cli enrichment")
    parser.add_argument("--output-dir", type=Path, default=None, help="Persist text and JSON reports under a dated directory tree")
    parser.add_argument("--server-name", default=socket.gethostname(), help="Server identifier used in report content and output paths")
    parser.add_argument("--debug-json", action="store_true", help="Include verbose per-app debug detail in JSON output")
    parser.add_argument("--json-out", type=Path, default=None, help="Optional JSON report path")
    parser.add_argument("--csv-out", type=Path, default=None, help="Optional CSV summary path")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    report = build_report(
        applications_root=args.applications_root,
        top_n=args.top,
        server_name=args.server_name,
        fixture_mode=args.fixture_mode,
    )
    text_report = render_text(report)
    sys.stdout.write(text_report + "\n")
    if args.output_dir:
        paths = build_report_paths(args.output_dir, args.server_name, report.inspection_timestamp)
        write_text_report(report, paths["text"])
        export_json(report, paths["json"], include_debug=args.debug_json)
        export_csv(report, paths["csv"])
    if args.json_out:
        export_json(report, args.json_out, include_debug=args.debug_json)
    if args.csv_out:
        export_csv(report, args.csv_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
