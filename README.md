# Controlled Chaos

Local-first personal workspace: tasks, inbox, notes, journal with Day Closure, projects, calendar, focus timer, search,
themes, Adaptive Desk, daily planning, reminders/alarms, export/backup/restore. **Completely offline**: no accounts, cloud,
analytics or remote assets. Data lives in one SQLite file (`controlled_chaos.db`) in app-private storage.

Python 3.10+ (tested on 3.13) · Flet 1.0.5 · SQLite.

## Run from source
```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[web]"                              # browser mode, fully offline at run time
controlled-chaos --web                               # opens http://127.0.0.1:8550
# native window instead: pip install -e ".[desktop]" && controlled-chaos
#   (Flet downloads its desktop client once, on first run)
```
Data: Linux `~/.local/share/controlled_chaos/`, Windows `%APPDATA%\ControlledChaos\`.

## Build installable apps (needs internet once, for Flutter)
```bash
pip install "flet==1.0.5" "flet-cli==1.0.5" "flet-desktop==1.0.5"
flet build linux  --yes     # on Linux   (apt: libgtk-3-dev libmpv-dev mpv ninja-build) -> build/linux
flet build windows --yes    # on Windows (Visual Studio "Desktop development with C++") -> build/windows
flet build apk --yes        # Android (JDK 17) -> build/apk
flet build aab --yes        # Google Play bundle -> build/aab
```
Or let GitHub build them: **Actions → build → Run workflow** (or push a tag like `v1.0.0`); download from the run's Artifacts.

### Android signing
You must supply your own key; none is included. `keytool -genkey -v -keystore upload.jks -keyalg RSA -keysize 2048 -validity 10000 -alias upload`,
then pass `--android-signing-key-store`, `--android-signing-key-store-password`, `--android-signing-key-password`,
`--android-signing-key-alias` to `flet build`, or add the four GitHub secrets named in `.github/workflows/build.yml`. Never commit the keystore (it is git-ignored).

## Test
```bash
pip install -e ".[web,dev]" && python -m pytest -q          # 189 tests
python -m controlled_chaos.utilities.audit .                 # offline audit
python tools/offline_e2e.py                                  # Linux, needs Playwright + root: real browser with no network
```

## Status (honest)
Verified here: tests, offline audit, installed-wheel run with no network, DB integrity, restart persistence.
**Not yet verified** (the workflows and `flet build` commands have never run): Linux/Windows/Android builds, packaged-app
behaviour, Android notifications (no native bridge yet: reports "unavailable"), Windows toasts, real Linux `notify-send`.
Android INTERNET permission is disabled in `pyproject.toml`; if a device build fails to start, resolve it rather than re-enabling silently.
Brand assets: regenerate with `tools/make_brand_assets.py` from `assets_src/masters/` (never edited). No license file is included; add one before publishing.
