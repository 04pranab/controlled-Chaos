#!/usr/bin/env bash
# Offline installer for Controlled Chaos (Linux, browser mode). Installs per-user; needs no root and no network.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"
"$PY" - <<'P' || { echo "Python $(python3 -V 2>&1) found; this bundle's wheels target the Python version in its file name (see INSTALLATION.md)." >&2; exit 1; }
import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)
P
APP="${XDG_DATA_HOME:-$HOME/.local/share}/controlled-chaos"
BIN="$HOME/.local/bin"
mkdir -p "$APP" "$BIN" "${XDG_DATA_HOME:-$HOME/.local/share}/applications" "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
"$PY" -m venv "$APP/venv"
"$APP/venv/bin/pip" install --no-index --find-links "$HERE/wheelhouse" "controlled-chaos[web]" >/dev/null
cp "$HERE/controlled-chaos.png" "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps/controlled-chaos.png"
cat > "$BIN/controlled-chaos" <<L
#!/usr/bin/env bash
exec "$APP/venv/bin/python" -m controlled_chaos.main --web "\$@"
L
chmod +x "$BIN/controlled-chaos"
cat > "${XDG_DATA_HOME:-$HOME/.local/share}/applications/controlled-chaos.desktop" <<D
[Desktop Entry]
Type=Application
Name=Controlled Chaos
Comment=Local-first tasks, notes, journal and focus. Works offline.
Exec=$BIN/controlled-chaos
Icon=controlled-chaos
Categories=Office;Utility;
Terminal=false
D
echo "Installed. Run: controlled-chaos   (make sure $BIN is on your PATH)"
