#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-$(readlink -f ~/applications)}"
BASE_DIR="${BASE_DIR:-$HOME/serverbottleneck}"
ANALYZER_DIR="${ANALYZER_DIR:-$BASE_DIR/analyzer}"
OUT_BASE="${OUT_BASE:-$BASE_DIR/data}"
LOG_FILE="${LOG_FILE:-$BASE_DIR/logs/collector.log}"
SERVER_NAME="${SERVER_NAME:-$(hostname)}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"

mkdir -p "$OUT_BASE" "$(dirname "$LOG_FILE")"

cd "$ANALYZER_DIR"

PYTHONPATH=src python3 -m serverbottleneck.cli \
  --applications-root "$APP_ROOT" \
  --server-name "$SERVER_NAME" \
  --output-dir "$OUT_BASE" \
  --top 5 \
  --debug-json >> "$LOG_FILE" 2>&1

find "$OUT_BASE" -type f -mtime +"$RETENTION_DAYS" -delete
