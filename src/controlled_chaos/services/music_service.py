"""Focus music: a few bundled original loops plus the user's own audio files.

User files are COPIED into <app data>/music/ (app-private, durable) so a track keeps working if the original is moved,
and nothing is ever read from or written to anywhere the user did not choose.  Nothing is uploaded anywhere.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from .base import AppError

BUNDLED = [("rain", "Rain", "Steady rain"), ("brown", "Brown noise", "Deep, even noise"),
           ("pad", "Soft pad", "Slow warm chord"), ("keys", "Soft keys", "Gentle, slow notes")]
EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"}
MAX_BYTES = 40 * 1024 * 1024
MAX_FILES = 60


def bundled_label(key: str) -> str:
    return next((l for k, l, _ in BUNDLED if k == key), key)


class MusicService:
    def __init__(self, ctx, folder: Optional[Path]):
        self.ctx = ctx
        self.folder = Path(folder) / "music" if folder else None

    # ------------------------------------------------------------ library
    def user_tracks(self) -> List[str]:
        if not self.folder or not self.folder.is_dir():
            return []
        return sorted(p.name for p in self.folder.iterdir() if p.is_file() and p.suffix.lower() in EXTS)

    def _safe_name(self, name: str) -> str:
        base = Path(str(name).replace("\\", "/")).name
        stem = re.sub(r"[^\w .()-]+", "_", Path(base).stem).strip(" .") or "track"
        ext = Path(base).suffix.lower()
        if ext not in EXTS:
            raise AppError("Unsupported file type. Use mp3, wav, ogg, m4a, aac or flac.")
        return stem[:80] + ext

    def add(self, name: str, data: bytes) -> str:
        if not self.folder:
            raise AppError("Music files can't be stored in this session.")
        if len(data) > MAX_BYTES:
            raise AppError("That file is larger than 40 MB. Pick a shorter track.")
        if len(data) < 64:
            raise AppError("That file looks empty.")
        if len(self.user_tracks()) >= MAX_FILES:
            raise AppError("Your music library is full (60 tracks). Remove some first.")
        safe = self._safe_name(name)
        self.folder.mkdir(parents=True, exist_ok=True)
        target, n = self.folder / safe, 1
        while target.exists():
            n += 1
            target = self.folder / f"{Path(safe).stem} ({n}){Path(safe).suffix}"
        tmp = target.with_name(target.name + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        return target.name

    def add_path(self, path: str) -> str:
        p = Path(path)
        if not p.is_file():
            raise AppError("That file could not be found.")
        if p.stat().st_size > MAX_BYTES:
            raise AppError("That file is larger than 40 MB. Pick a shorter track.")
        return self.add(p.name, p.read_bytes())

    def remove(self, name: str) -> None:
        if self.folder and name in self.user_tracks():
            (self.folder / name).unlink(missing_ok=True)
        if self.ctx.setting("music_track") == f"file:{name}":
            self.ctx.db.execute("INSERT INTO settings(key,value) VALUES('music_track','bundled:rain') "
                                "ON CONFLICT(key) DO UPDATE SET value=excluded.value")

    # ------------------------------------------------------------ choice
    def options(self) -> List[Tuple[str, str]]:
        return [(f"bundled:{k}", l) for k, l, _ in BUNDLED] + [(f"file:{n}", Path(n).stem) for n in self.user_tracks()]

    def resolve(self, track: str):
        """-> ("asset", "music/rain.wav") | ("bytes", data) | None (missing)."""
        kind, _, name = (track or "").partition(":")
        if kind == "bundled" and any(k == name for k, _, _ in BUNDLED):
            return ("asset", f"music/{name}.wav")
        if kind == "file" and name in self.user_tracks():
            return ("bytes", (self.folder / name).read_bytes())
        return None
