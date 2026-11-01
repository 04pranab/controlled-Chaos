#!/usr/bin/env bash
# Removes the program. Your data (controlled_chaos.db, backups) is NOT touched.
set -euo pipefail
D="${XDG_DATA_HOME:-$HOME/.local/share}"
rm -rf "$D/controlled-chaos/venv" "$HOME/.local/bin/controlled-chaos" "$D/applications/controlled-chaos.desktop" "$D/icons/hicolor/256x256/apps/controlled-chaos.png"
echo "Program removed. Data left in place; see INSTALLATION.md for its location."
