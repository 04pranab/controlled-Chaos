"""Application-wide constants and default settings."""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "Controlled Chaos"
TAGLINE = "Make room for what matters."
DB_FILENAME = "controlled_chaos.db"
BACKUP_EXT = ".ccbackup"

# Default user-adjustable settings. Values are stored as strings in the DB.
DEFAULT_SETTINGS: dict[str, str] = {
    "theme": "paper",                 # paper | ink | studio
    "adaptive_desk": "1",
    "recommendations": "1",
    "reduced_motion": "0",
    "text_scale": "1.0",
    "texture": "0",
    "display_name": "",
    "timezone": "",                   # IANA name; empty = device local zone
    "work_start": "09:00",
    "work_end": "17:00",
    "high_energy_start": "09:00",
    "high_energy_end": "12:00",
    "focus_minutes": "25",
    "break_minutes": "5",
    "closure_hour": "19",             # offer Day Closure from this local hour
    "scratchpad": "",
    "notifications": "1",             # master switch for reminders and alerts
    "notif_sound_on": "1",
    "notif_sound_default": "soft",    # soft | classic | focus | alarm
    "notif_sound_alarm": "alarm",
    "notif_sound_focus": "focus",
    "snooze_default": "10",
    "task_done_policy": "cancel",     # what a completed task does to its reminder: cancel | keep
    "focus_alerts": "1",
    "music_track": "bundled:rain",     # bundled:<name> | file:<name in app music folder>
    "music_volume": "0.5",
    "company_on": "0",                # LAN chat: off until the person turns it on
    "company_name": "",               # the only thing about you that Company shares
    "music_auto": "0",                # start/stop music with focus sessions (off by default)
    "planning_on": "0",
    "planning_time": "08:30",
    "planning_days": "0,1,2,3,4,5,6",
    "closure_on": "0",
    "closure_time": "20:30",
    "closure_days": "0,1,2,3,4,5,6",
    "alarm_ring_seconds": "90",       # how long an alarm keeps repeating if nobody answers
    "default_estimate": "30",
    "right_panel": "1",
    "greeting_name": "",
}


class StorageError(RuntimeError):
    pass


def _forbidden_roots() -> list[Path]:
    """Places the primary database must never live: temp and cache directories."""
    import tempfile
    roots = [Path(tempfile.gettempdir()), Path("/tmp"), Path("/var/tmp"), Path("/var/cache"),
             Path.home() / ".cache"]
    for var in ("TMPDIR", "TEMP", "TMP", "XDG_CACHE_HOME"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]))
    return [r.resolve() for r in roots if str(r)]


def storage_source() -> str:
    """Which rule picked the data directory (shown in Settings for transparency)."""
    if os.environ.get("FLET_APP_STORAGE_DATA"):
        return "FLET_APP_STORAGE_DATA"
    if os.environ.get("CC_DATA_DIR"):
        return "CC_DATA_DIR (development/test override)"
    return "per-user data folder (development fallback)"


def data_dir() -> Path:
    """Durable, app-private directory for the primary database.

    Order: ``FLET_APP_STORAGE_DATA`` (set by Flet in packaged Android/desktop apps) ->
    ``CC_DATA_DIR`` (explicit development/test override) -> per-user data folder (only
    when running from source).  Temp/cache locations are refused unless the explicit
    override is used, so the database can never silently end up somewhere the OS may purge.
    """
    flet_dir = os.environ.get("FLET_APP_STORAGE_DATA")
    override = os.environ.get("CC_DATA_DIR")
    chosen = flet_dir or override
    if chosen:
        base = Path(chosen)
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home())) / "ControlledChaos"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "controlled_chaos"
    base.mkdir(parents=True, exist_ok=True)
    resolved = base.resolve()
    if not override or flet_dir:   # the override is the only sanctioned way to point at a scratch location
        for bad in _forbidden_roots():
            if resolved == bad or bad in resolved.parents:
                raise StorageError(f"refusing to keep user data in a temporary or cache folder: {resolved}")
    return base


def db_path() -> Path:
    return data_dir() / DB_FILENAME
