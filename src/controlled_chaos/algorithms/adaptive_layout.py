"""Adaptive Desk: decides which information deserves *visual prominence* now.

Deliberately separate from the Daily Balance Engine (that is a scheduling
problem; this is an interface problem).

Invariants (enforced by tests):
* The same set of panels is present in every context - nothing ever disappears.
* The primary column order (priorities, tasks, scratchpad) never changes.
* Adaptation only changes (a) emphasis of right-column panels, (b) their order
  within the right column, and (c) which dismissible banners are shown.
* Fully deterministic; with adaptation disabled the stable default is returned.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

PRIMARY_PANELS: Tuple[str, ...] = ("priorities", "tasks", "scratchpad")
SECONDARY_DEFAULT: Tuple[str, ...] = ("focus", "calendar", "recent", "progress")
ALL_PANELS = frozenset(PRIMARY_PANELS + SECONDARY_DEFAULT)

BUSY_DAY_EVENTS = 4
OVERDUE_REVIEW_MIN = 3
INBOX_HINT_MIN = 5


@dataclass(frozen=True)
class DeskContext:
    focus_active: bool = False
    overdue_count: int = 0
    events_today: int = 0
    inbox_new: int = 0
    local_hour: int = 12
    closure_hour: int = 19
    closure_done_today: bool = False
    plan_available: bool = False


@dataclass(frozen=True)
class DeskLayout:
    primary: Tuple[str, ...]
    secondary: Tuple[str, ...]
    emphasis: Dict[str, int]          # panel -> 0 normal, 1 raised, 2 prominent
    banners: Tuple[str, ...]          # dismissible hints, most important first


def stable_layout() -> DeskLayout:
    return DeskLayout(PRIMARY_PANELS, SECONDARY_DEFAULT, {p: 0 for p in ALL_PANELS}, ())


def compute_layout(ctx: DeskContext, adaptive: bool = True) -> DeskLayout:
    if not adaptive:
        return stable_layout()
    emphasis = {p: 0 for p in ALL_PANELS}
    sec: List[str] = list(SECONDARY_DEFAULT)
    banners: List[str] = []

    def lift(panel: str, level: int) -> None:
        emphasis[panel] = max(emphasis[panel], level)
        if panel in sec:
            sec.remove(panel)
            sec.insert(0, panel)

    if ctx.events_today >= BUSY_DAY_EVENTS:
        lift("calendar", 1)
    if ctx.focus_active:
        lift("focus", 2)          # applied last among lifts => wins first position
    if ctx.overdue_count >= OVERDUE_REVIEW_MIN:
        banners.append("review_overdue")
    if ctx.inbox_new >= INBOX_HINT_MIN:
        banners.append("process_inbox")
    elif ctx.inbox_new > 0:
        emphasis["recent"] = max(emphasis["recent"], 1)
    if ctx.local_hour >= ctx.closure_hour and not ctx.closure_done_today:
        banners.append("day_closure")
    if ctx.plan_available:
        banners.append("plan_ready")
    return DeskLayout(PRIMARY_PANELS, tuple(sec), emphasis, tuple(banners))
