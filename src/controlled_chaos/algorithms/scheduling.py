"""Daily Balance Engine v2 - chance-constrained daily planning.

Problem
    Given today's free working time (after fixed appointments), a set of open
    tasks with *uncertain* durations, hard deadlines, pinned tasks, and soft
    preferences, choose which tasks to recommend and in what order.

    This is a stochastic knapsack with deadlines.  We use the chance-constrained
    formulation:  the chosen set S must satisfy
        P( sum_{i in S} D_i <= C )                      >= 1 - eps_capacity   (fits today)
        P( sum_{i in S, d_i <= d_j} D_i <= C(d_j) )     >= 1 - eps_deadline   for each deadline j
    where D_i is the (log-normal) duration from ``DurationModel`` and C(d) is free
    working time before deadline d.  Probabilities are estimated on a fixed set of
    common random numbers, which keeps plans deterministic and monotone.

    Selection: value-density greedy followed by single-swap improvement (each
    accepted swap strictly increases total value, so it terminates).  Dean,
    Goemans & Vondrak (2008, Math. of Operations Research) show that simple
    non-adaptive greedy policies are within a constant factor of the optimal
    adaptive policy for the stochastic knapsack, which is the theoretical
    motivation for using a cheap greedy + local search here; we make NO claim of
    optimality for the practical variant (constraints on deadlines, swaps).

    Sequencing: slack-aware list scheduling.  At each step, a task is "eligible"
    only if placing it next keeps every remaining deadline's chance constraint
    satisfied (the stochastic analogue of the earliest-due-date feasibility
    check; Moore 1968 / Hodgson showed EDD-based logic is optimal for minimising
    late jobs in the deterministic case).  Among eligible tasks the engine prefers
    pinned tasks, then higher value, weighted by how well the task's energy demand
    matches the user's energy window at the expected start time.

Safeguards (from the product spec)
    * never silently drops pinned or deadline-today tasks (they are kept and a
      warning is produced if they cannot fit at the requested confidence);
    * never completes tasks, edits appointments or deletes anything;
    * every inclusion/exclusion has a machine-readable reason and a plain-language
      explanation;
    * deterministic: same input -> same plan.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import prioritization as pz
from .duration import DurationModel, Z90, keys_for
from .rng import fnv1a, std_normals

Interval = Tuple[float, float]  # absolute minutes


@dataclass
class EngineTask:
    id: str
    estimate: float = 30.0
    importance: float = 0.5
    goal: float = 0.0
    user_priority: bool = False
    deadline: Optional[float] = None      # absolute minutes
    energy: float = 0.5                   # 0 easy .. 1 deep-focus
    pinned: bool = False
    project: Optional[str] = None
    category: Optional[str] = None
    dismissed: bool = False
    deferrals: int = 0


@dataclass
class PlanConfig:
    weights: pz.Weights = field(default_factory=pz.Weights)
    eps_capacity: float = 0.20            # plan fits with >= 80% confidence
    eps_deadline: float = 0.10            # each deadline met with >= 90% confidence
    samples: int = 500
    seed: int = 20261028
    energy_windows: List[Tuple[float, float, float]] = field(default_factory=list)  # (start,end,level) absolute
    default_energy: float = 0.5
    daily_capacity: float = 480.0         # working minutes/day, for deadlines beyond today
    max_swap_passes: int = 3
    max_candidates: int = 40


@dataclass
class Entry:
    task_id: str
    start_offset: float        # working minutes from now (expected)
    end_offset: float
    start_clock: float         # absolute minutes (expected, skipping busy time)
    end_clock: float
    p50: float
    p90: float
    reasons: List[str]
    score: float


@dataclass
class Plan:
    entries: List[Entry]
    deferred: Dict[str, List[str]]
    attention: List[str]       # overdue tasks needing a human decision
    at_risk: List[str]         # deadline tasks whose P90 may not fit
    warnings: List[str]
    capacity: float
    p_fit: float               # estimated probability the whole plan fits in capacity
    p50_total: float
    p90_total: float
    buffer_p50: float
    scores: Dict[str, pz.ScoreBreakdown] = field(default_factory=dict)


# -------------------------------------------------------------- time geometry

def free_segments(now: float, windows: Sequence[Interval], busy: Sequence[Interval]) -> List[Interval]:
    segs: List[Interval] = []
    for a, b in sorted(windows):
        a = max(a, now)
        if b > a:
            segs.append((a, b))
    for ba, bb in sorted(busy):
        out: List[Interval] = []
        for a, b in segs:
            if bb <= a or ba >= b:
                out.append((a, b))
            else:
                if ba > a:
                    out.append((a, ba))
                if bb < b:
                    out.append((bb, b))
        segs = out
    return sorted(segs)


def free_before(segs: Sequence[Interval], t: float) -> float:
    tot = 0.0
    for a, b in segs:
        if t <= a:
            break
        tot += min(b, t) - a
    return tot


def offset_to_clock(segs: Sequence[Interval], off: float, now: float) -> float:
    acc = 0.0
    for a, b in segs:
        if off <= acc + (b - a):
            return a + (off - acc)
        acc += b - a
    return segs[-1][1] if segs else now


def _energy_at(cfg: PlanConfig, clock: float) -> float:
    for a, b, lvl in cfg.energy_windows:
        if a <= clock < b:
            return lvl
    return cfg.default_energy


def _vsum(vectors: Sequence[List[float]], n: int) -> List[float]:
    if not vectors:
        return [0.0] * n
    return [sum(col) for col in zip(*vectors)]


def _frac_le(sums: List[float], cap: float) -> float:
    return sum(1 for x in sums if x <= cap) / len(sums)


def _pct(sorted_vals: List[float], q: float) -> float:
    k = min(len(sorted_vals) - 1, max(0, int(math.ceil(q * len(sorted_vals))) - 1))
    return sorted_vals[k]


# ----------------------------------------------------------------- the planner

def plan_day(now: float, tasks: Sequence[EngineTask], windows: Sequence[Interval],
             busy: Sequence[Interval], model: DurationModel,
             cfg: Optional[PlanConfig] = None) -> Plan:
    cfg = cfg or PlanConfig()
    w = cfg.weights
    segs = free_segments(now, windows, busy)
    C = sum(b - a for a, b in segs)
    horizon_end = segs[-1][1] if segs else now
    S = cfg.samples
    by_id = {t.id: t for t in tasks}

    warnings: List[str] = []
    if C <= 0:
        warnings.append("no_free_time")

    # --- duration distributions on common random numbers ------------------
    D: Dict[str, List[float]] = {}
    p50: Dict[str, float] = {}
    p90: Dict[str, float] = {}
    for t in tasks:
        m, s = model.predict(t.estimate, keys_for(t.project, t.category))
        z = std_normals((cfg.seed ^ fnv1a(t.id)) & 0xFFFFFFFF, S)
        D[t.id] = [math.exp(m + s * zi) for zi in z]
        p50[t.id] = math.exp(m)
        p90[t.id] = math.exp(m + s * Z90)

    # --- urgency, value ------------------------------------------------------
    breakdown: Dict[str, pz.ScoreBreakdown] = {}
    cap_dl: Dict[str, float] = {}          # deadline constraints that bind today
    attention: List[str] = []
    at_risk: set = set()
    forced: List[str] = []
    for t in tasks:
        u = 0.0
        if t.deadline is not None:
            if t.deadline < now:
                u = w.overdue_urgency
                attention.append(t.id)
            else:
                cap_j = free_before(segs, t.deadline)
                if t.deadline > horizon_end:    # deadline beyond today: add future working time
                    cap_j += (t.deadline - horizon_end) / 1440.0 * cfg.daily_capacity
                else:
                    cap_dl[t.id] = cap_j
                    if not t.dismissed:
                        forced.append(t.id)
                slack = cap_j - p90[t.id]
                if slack < 0:
                    at_risk.add(t.id)
                u = pz.urgency_from_slack(slack, w.urgency_tau)
        breakdown[t.id] = pz.score(w, t.importance, u, t.goal, t.user_priority,
                                   t.deferrals, t.deadline is not None)
    for t in tasks:
        if t.pinned and not t.dismissed and t.id not in forced:
            forced.append(t.id)
    value = {i: b.total for i, b in breakdown.items()}

    def feasible(ids: Sequence[str]) -> bool:
        if not ids:
            return True
        if _frac_le(_vsum([D[i] for i in ids], S), C) < 1.0 - cfg.eps_capacity:
            return False
        dl = sorted((i for i in ids if i in cap_dl), key=lambda i: (by_id[i].deadline, i))
        for j in dl:
            pref = [i for i in dl if (by_id[i].deadline, i) <= (by_id[j].deadline, j)]
            if _frac_le(_vsum([D[i] for i in pref], S), cap_dl[j]) < 1.0 - cfg.eps_deadline:
                return False
        return True

    # --- selection ---------------------------------------------------------------
    chosen = list(forced)
    if not feasible(chosen):
        warnings.append("forced_exceeds_capacity")
    cands = [t.id for t in tasks if t.id not in chosen and not t.dismissed]
    cands.sort(key=lambda i: (-value[i] / max(p50[i], 1.0), i))
    cands = cands[:cfg.max_candidates]
    for i in cands:
        if feasible(chosen + [i]):
            chosen.append(i)
    for _ in range(cfg.max_swap_passes):
        improved = False
        optional = [i for i in chosen if i not in forced]
        for a in optional:
            for b in sorted((i for i in cands if i not in chosen), key=lambda i: (-value[i], i)):
                if value[b] <= value[a] + 1e-12:
                    break
                trial = [i for i in chosen if i != a] + [b]
                if feasible(trial):
                    chosen = trial
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break

    # --- sequencing -----------------------------------------------------------
    remaining = list(chosen)
    entries: List[Entry] = []
    cursor = 0.0
    while remaining:
        def safe(c: str) -> bool:
            dl = sorted((i for i in remaining if i in cap_dl), key=lambda i: (by_id[i].deadline, i))
            for j in dl:
                pref = [i for i in dl if (by_id[i].deadline, i) <= (by_id[j].deadline, j)]
                ids = pref if c in pref else pref + [c]
                if _frac_le(_vsum([D[i] for i in ids], S), max(0.0, cap_dl[j] - cursor)) < 1.0 - cfg.eps_deadline:
                    return False
            return True

        elig = [c for c in remaining if safe(c)]
        if not elig:   # cannot satisfy everything: take the earliest deadline first
            dls = sorted((i for i in remaining if i in cap_dl), key=lambda i: (by_id[i].deadline, i))
            elig = [dls[0]] if dls else list(remaining)
        clock = offset_to_clock(segs, cursor, now)
        lvl = _energy_at(cfg, clock)

        def rank(c: str):
            fit = 1.0 - abs(by_id[c].energy - lvl)
            return (0 if by_id[c].pinned else 1, -value[c] * (0.5 + 0.5 * fit), c)

        pick = min(elig, key=rank)
        remaining.remove(pick)
        reasons: List[str] = []
        if by_id[pick].pinned:
            reasons.append("pinned")
        if pick in cap_dl:
            reasons.append("at_risk_deadline" if pick in at_risk else "deadline_today")
        if pick in attention:
            reasons.append("overdue")
        if not reasons:
            reasons.append("value_fit")
        end = cursor + p50[pick]
        entries.append(Entry(pick, cursor, end, offset_to_clock(segs, cursor, now),
                             offset_to_clock(segs, end, now), p50[pick], p90[pick], reasons, value[pick]))
        cursor = end

    deferred: Dict[str, List[str]] = {}
    for t in tasks:
        if t.id not in chosen:
            deferred[t.id] = ["dismissed_today"] if t.dismissed else ["capacity_confidence"]

    tot = sorted(_vsum([D[i] for i in chosen], S))
    return Plan(entries=entries, deferred=deferred, attention=attention, at_risk=sorted(at_risk),
                warnings=warnings, capacity=C, p_fit=_frac_le(tot, C),
                p50_total=_pct(tot, 0.5), p90_total=_pct(tot, 0.9),
                buffer_p50=C - _pct(tot, 0.5), scores=breakdown)


# ---------------------------------------------------------------- explanations

def explain(plan: Plan, titles: Dict[str, str], fmt_clock: Callable[[float], str],
            fmt_dur: Callable[[float], str], estimates: Optional[Dict[str, float]] = None,
            eps_capacity: float = 0.20) -> Dict[str, object]:
    """Plain-language reasons. Returns {'summary': str, 'tasks': {id: [str]}, 'warnings': [str]}."""
    est = estimates or {}
    msgs: Dict[str, List[str]] = {}
    for e in plan.entries:
        r: List[str] = []
        t = titles.get(e.task_id, e.task_id)
        if "pinned" in e.reasons:
            r.append("You pinned this task, so it stays in the plan.")
        if "at_risk_deadline" in e.reasons:
            r.append("Its deadline is tight - even a slow run may not fit, so it is scheduled early.")
        elif "deadline_today" in e.reasons:
            r.append("It has a deadline within today's working time.")
        if "overdue" in e.reasons:
            r.append("It is overdue; decide whether to do it, move it or drop it.")
        if "value_fit" in e.reasons:
            r.append("High value for the time it needs, and it fits your available time.")
        base = est.get(e.task_id)
        if base and abs(e.p50 - base) / max(base, 1) > 0.08:
            r.append(f"Your estimate was {fmt_dur(base)}; based on how long similar tasks take you, "
                     f"plan for about {fmt_dur(e.p50)} (up to {fmt_dur(e.p90)} on a slow day).")
        else:
            r.append(f"Expected about {fmt_dur(e.p50)} (up to {fmt_dur(e.p90)} on a slow day).")
        msgs[e.task_id] = r
    for tid, codes in plan.deferred.items():
        if "dismissed_today" in codes:
            msgs[tid] = ["You set this aside for today."]
        else:
            msgs[tid] = [f"Adding it would leave less than a {int((1 - eps_capacity) * 100)}% chance of "
                         "finishing everything in your available time, so it is suggested for later."]
    pct = int(round(plan.p_fit * 100))
    if not plan.entries:
        summary = "Nothing is recommended for the time you have left today."
    else:
        summary = (f"{len(plan.entries)} task(s), about {fmt_dur(plan.p50_total)} expected "
                   f"(about {fmt_dur(plan.p90_total)} if things run slow) in {fmt_dur(plan.capacity)} available - "
                   f"roughly a {pct}% chance everything fits.")
        if plan.buffer_p50 >= 0:
            summary += f" Expected buffer: {fmt_dur(plan.buffer_p50)}."
    w: List[str] = []
    if "no_free_time" in plan.warnings:
        w.append("There is no free working time left today. Adjust your working hours or move something.")
    if "forced_exceeds_capacity" in plan.warnings:
        w.append("Pinned and deadline tasks already need more time than you have at a comfortable "
                 "confidence level. Nothing was removed - consider moving a pinned task or a deadline.")
    if plan.attention:
        w.append(f"{len(plan.attention)} overdue task(s) need a decision.")
    return {"summary": summary, "tasks": msgs, "warnings": w}
