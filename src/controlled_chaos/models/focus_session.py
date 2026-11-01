from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class FocusSession:
    id: str
    planned_sec: int
    status: str                       # running | paused | completed | stopped
    started_ts: int
    task_id: Optional[str] = None
    kind: str = "focus"
    accumulated_sec: int = 0
    last_resume_ts: Optional[int] = None
    ended_ts: Optional[int] = None
    created_at: int = 0

    def elapsed(self, now_ts: int) -> int:
        """Seconds of real focus so far, computed from stored timestamps only."""
        extra = 0
        if self.status == "running" and self.last_resume_ts is not None:
            extra = max(0, now_ts - self.last_resume_ts)
        return min(self.planned_sec, self.accumulated_sec + extra) if self.status in ("running", "paused") \
            else self.accumulated_sec

    def remaining(self, now_ts: int) -> int:
        return max(0, self.planned_sec - self.elapsed(now_ts))
