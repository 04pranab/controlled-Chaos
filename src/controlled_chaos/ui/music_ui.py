"""Focus music: player (one looping audio service) and the small panel shown on the Focus card.

Nothing plays unless the person presses Play (or switches on 'with focus sessions').  Browsers such as Chrome refuse to
start sound without a click, so a play request that comes from a timer instead of a click can be blocked there;
the native apps do not have that limit.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import flet as ft

log = logging.getLogger("controlled_chaos.music")


class MusicPlayer:
    def __init__(self, sh):
        self.sh = sh
        self.audio = None
        self.picker = None
        self.playing = False
        self.auto_started = False
        self.available = True

    # ------------------------------------------------------------ plumbing
    def _audio(self):
        if self.audio is None:
            try:
                import flet_audio as fta
            except Exception:                               # package missing: the rest of the app keeps working
                self.available = False
                return None
            self.audio = fta.Audio(release_mode=fta.ReleaseMode.LOOP, volume=self._vol())
            self.sh.page.services.append(self.audio)
        return self.audio

    def _vol(self) -> float:
        return max(0.0, min(1.0, self.sh.app.settings.get_float("music_volume")))

    async def _play(self) -> None:
        a = self._audio()
        if a is None:
            self.sh.toast("Music needs the flet-audio package, which is not installed.")
            return
        res = self.sh.app.music.resolve(self.sh.app.settings.get("music_track"))
        if res is None:
            self.sh.toast("That track is missing. Pick another one.")
            return
        log.info("music: play requested (%s)", res[0])
        a.src = res[1]
        a.volume = self._vol()
        a.update()
        self.playing = True                       # the request is in; do not depend on the reply (see below)
        try:
            # On some platforms the reply to play() only arrives when playback ends, so do not wait for it forever.
            await asyncio.wait_for(a.play(), timeout=4)
            log.info("music: playing %s", self.sh.app.settings.get("music_track"))
        except asyncio.TimeoutError:
            log.info("music: play request sent, no confirmation (normal for looping audio)")
        except Exception as exc:
            self.playing = False
            log.warning("music play failed: %s", exc)
            self.sh.toast("The sound could not start. Press Play once to allow audio.")

    async def _stop(self) -> None:
        a = self.audio
        self.playing = False
        if a is not None:
            try:
                await asyncio.wait_for(a.pause(), timeout=3)
            except asyncio.TimeoutError:
                pass
            except Exception as exc:
                log.warning("music stop failed: %s", exc)

    def play(self, auto: bool = False) -> None:
        self.auto_started = auto
        self.sh.page.run_task(self._play)

    def stop(self) -> None:
        self.auto_started = False
        self.sh.page.run_task(self._stop)

    def set_volume(self, v: float) -> None:
        self.sh.app.settings.set("music_volume", round(v, 2))
        if self.audio is not None:
            self.audio.volume = max(0.0, min(1.0, v))
            self.audio.update()

    def sync(self) -> None:
        """Called once a second: follows the focus session when 'with focus sessions' is on."""
        app = self.sh.app
        if not app.settings.get_bool("music_auto"):
            return
        s = app.focus.active()
        want = s is not None and s.kind == "focus" and s.status == "running"
        if want and not self.playing:
            self.play(auto=True)
        elif not want and self.playing and self.auto_started:
            self.stop()


def music_block(sh) -> ft.Control:
    k, p, app, mp = sh.kit, sh.kit.p, sh.app, sh.music
    opts = app.music.options()
    cur = app.settings.get("music_track")
    if cur not in [o[0] for o in opts]:
        cur = "bundled:rain"
    dd = k.dropdown("Music", opts, cur, width=None)
    dd.expand = True
    dd.on_select = lambda e: (app.settings.set("music_track", e.control.value),
                              mp.play(mp.auto_started) if mp.playing else None)
    state = {"label": "Stop" if mp.playing else "Play"}

    def toggle(e):
        if mp.playing:
            mp.stop()
            e.control.content.value = "Play"
            e.control.icon = ft.Icons.PLAY_ARROW
        else:
            mp.play()
            e.control.content.value = "Stop"
            e.control.icon = ft.Icons.STOP
        e.control.update()

    play = k.button(state["label"], toggle, icon=ft.Icons.STOP if mp.playing else ft.Icons.PLAY_ARROW)
    vol = ft.Slider(min=0, max=1, divisions=20, value=mp._vol(), expand=True, active_color=p.accent,
                    on_change_end=lambda e: mp.set_volume(float(e.control.value)), tooltip="Volume")
    auto = ft.Switch(label="Play with focus sessions", value=app.settings.get_bool("music_auto"), active_color=p.accent,
                     on_change=lambda e: app.settings.set("music_auto", bool(e.control.value)))

    async def add(e):
        if mp.picker is None:
            mp.picker = ft.FilePicker()
            sh.page.services.append(mp.picker)
        try:
            files = await mp.picker.pick_files(dialog_title="Choose music", allow_multiple=True, with_data=True,
                                               file_type=ft.FilePickerFileType.AUDIO)
        except Exception as exc:
            sh.error(exc)
            return
        added, errors = 0, []
        for f in files or []:
            try:
                if f.path:
                    app.music.add_path(f.path)
                elif f.bytes:
                    app.music.add(f.name, bytes(f.bytes))
                else:
                    raise ValueError("no data")
                added += 1
            except Exception as exc:
                errors.append(str(exc))
        if added:
            sh.refresh()
        if added or errors:
            sh.toast((f"Added {added} track{'s' if added != 1 else ''}. " if added else "") + (errors[0] if errors else ""))

    own = k.link("Add my music", add, icon=ft.Icons.LIBRARY_MUSIC_OUTLINED)
    mine = app.music.user_tracks()
    manage = k.muted(f"{len(mine)} of your own track{'s' if len(mine) != 1 else ''}. Files are copied into the app's private folder.", 11.5)
    return ft.Column([ft.Row([dd, play], spacing=8), ft.Row([ft.Icon(ft.Icons.VOLUME_UP_OUTLINED, size=k.fs(16), color=p.muted), vol]),
                      auto, ft.Row([own], wrap=True), manage], spacing=6, tight=True)
