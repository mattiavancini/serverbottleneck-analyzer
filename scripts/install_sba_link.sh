#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/bin"

mkdir -p "$BIN_DIR"
ln -sfn "$SCRIPT_DIR/sba" "$BIN_DIR/sba"

echo "Installed: $BIN_DIR/sba"
echo "Open dashboard with: sba --server WP_Q"
echo "If 'sba' is not found, run: export PATH=\"\$HOME/bin:\$PATH\""
