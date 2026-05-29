#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${BASE_DIR:-$HOME/serverbottleneck}"
ANALYZER_DIR="${ANALYZER_DIR:-$BASE_DIR/analyzer}"
OUT_BASE="${OUT_BASE:-$BASE_DIR/data}"
SERVER_NAME="${SERVER_NAME:-$(hostname)}"
CONFIG_FILE="${CONFIG_FILE:-$ANALYZER_DIR/config/notifications.json}"
MODE="${1:-alert}"

if [ "$#" -gt 0 ]; then
  shift
fi

cd "$ANALYZER_DIR"

PYTHONPATH=src python3 -m serverbottleneck.notifications \
  --data-dir "$OUT_BASE" \
  --server "$SERVER_NAME" \
  --config "$CONFIG_FILE" \
  --mode "$MODE" \
  "$@"
