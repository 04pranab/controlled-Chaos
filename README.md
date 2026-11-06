# Controlled Chaos

Local-first personal workspace: tasks, inbox, notes, journal with Day Closure, projects, calendar, focus timer with calm
bundled music (or your own audio files), search, themes, Adaptive Desk, daily planning, reminders/alarms, export/backup/restore.
Notes and journal entries autosave about a second after you stop typing. Optional **Company**: an end-to-end encrypted chat with people on the same Wi-Fi (off by default). **Completely offline**: no accounts, cloud,
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
pip install -e ".[web,dev]" && python -m pytest -q          # 228 tests
python -m controlled_chaos.utilities.audit .                 # offline audit
python tools/offline_e2e.py                                  # Linux, needs Playwright + root: real browser with no network
```

## Focus music
Four original bundled loops (`tools/make_music.py`) plus your own mp3/wav/ogg/m4a/aac/flac files (copied into the app's private `music/` folder; max 40 MB each). Off until you press Play; optional 'Play with focus sessions'. Needs the `flet-audio` package (a normal dependency). Browsers such as Chrome block sound that does not start from a click.

## Company (chat on the same Wi-Fi)
Settings-free: open **Company**, enter a display name, turn it on, create a company and share its join code, or join with a code.
- Shares only your display name and the message text; never tasks, notes, journal, calendar or settings. Encrypted with AES-256-GCM using a key derived from the join code.
- UDP on the local network (port 47821); sends only to private/broadcast addresses, and the app's offline guard still blocks the internet and all TCP/DNS. Off by default; Android is excluded (it would need the network permission this build deliberately omits).
- Anyone with the code can read and post under any name, so treat it like a password. Delivery is best effort. Your firewall may ask to allow the app on private networks.
- Tested: two app copies exchanging messages over real sockets, late joiner history, forged/duplicate packets, guard limits. Not tested: real Wi-Fi routers/AP isolation, Windows firewall prompts, more than two devices.

## Status (honest)
Verified here: tests, offline audit, installed-wheel run with no network, DB integrity, restart persistence.
Focus music: files load and copy correctly in a real browser, but actual sound output was not confirmed (headless browser, no speakers) and playback has not been tried on Android.
**Not yet verified** (the workflows and `flet build` commands have never run): Linux/Windows/Android builds, packaged-app
behaviour, Android notifications (no native bridge yet: reports "unavailable"), Windows toasts, real Linux `notify-send`.
Android INTERNET permission is disabled in `pyproject.toml`; if a device build fails to start, resolve it rather than re-enabling silently.
Assets (icons, fonts, sounds, music) live in `src/assets/`: `flet build` only bundles assets that sit next to `main.py`. Regenerate brand assets with `tools/make_brand_assets.py` from `assets_src/masters/` (never edited). No license file is included; add one before publishing.
