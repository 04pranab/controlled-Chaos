from __future__ import annotations

from ..config import DEFAULT_SETTINGS
from .base import AppError, Context


class SettingsService:
    def __init__(self, ctx: Context):
        self.ctx = ctx

    def get(self, key: str) -> str:
        return self.ctx.setting(key)

    def get_bool(self, key: str) -> bool:
        return self.get(key) in ("1", "true", "True")

    def get_float(self, key: str) -> float:
        try:
            return float(self.get(key))
        except ValueError:
            return float(DEFAULT_SETTINGS.get(key, "0") or 0)

    def get_int(self, key: str) -> int:
        return int(round(self.get_float(key)))

    def set(self, key: str, value) -> None:
        if key not in DEFAULT_SETTINGS and not key.startswith(("closure_done:", "metric:", "dismissed_banner:")):
            raise AppError(f"unknown setting: {key}")
        if isinstance(value, bool):
            value = "1" if value else "0"
        self.ctx.db.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)))

    def all(self) -> dict:
        out = dict(DEFAULT_SETTINGS)
        for r in self.ctx.db.query("SELECT key,value FROM settings"):
            out[r["key"]] = r["value"]
        return out

    def bump_metric(self, name: str) -> None:
        k = f"metric:{name}"
        self.set(k, int(self.get(k) or 0) + 1)
