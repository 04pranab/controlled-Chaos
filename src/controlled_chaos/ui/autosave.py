"""Debounced autosave for text editors.

Why: saving only when a field loses focus made notes and journal entries depend on the order of
blur / tap events (on touch screens the keyboard closing can move the Save button away from the finger).
Now every edit schedules a save after a short pause, and Save / navigation / blur flush it immediately.
The saved text is always read from the fields at save time, so a late timer can never write older text.
"""
from __future__ import annotations

import asyncio
from typing import Callable


class Autosave:
    def __init__(self, sh, save: Callable[[], bool], status, delay: float = 0.6):
        self.sh, self.save, self.status, self.delay = sh, save, status, delay
        self._token = 0
        self.dirty = False

    def attach(self, *fields) -> None:
        for f in fields:
            f.on_change = self.poke
            prev = f.on_blur
            f.on_blur = (lambda e, prev=prev: (prev(e) if prev else None, self.flush()))

    def _say(self, text: str) -> None:
        if getattr(self.status, "value", None) == text:      # no UI update (and no round trip) when nothing changed
            return
        try:
            self.status.value = text
            self.status.update()
        except Exception:        # the view may already have been replaced
            pass

    def poke(self, e=None) -> None:
        self.dirty = True
        self._token += 1
        token = self._token
        self._say("Typing...")
        run = getattr(self.sh.page, "run_task", None)
        if run is None:
            return

        async def later():
            await asyncio.sleep(self.delay)
            if token == self._token:
                self.flush()
        try:
            run(later)
        except Exception:
            pass

    def flush(self) -> None:
        """Save now if there is anything unsaved."""
        if not self.dirty:
            return
        self.dirty = False
        self._token += 1
        ok = self.save()
        if ok is False:
            self.dirty = True
            self._say("Not saved - see the message above")
        else:
            self._say("Saved")
