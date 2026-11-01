"""Task value: a transparent, configurable weighted score.

    value = w_i*importance + w_u*urgency + w_g*goal + w_p*explicit_priority

The weights are DESIGN ASSUMPTIONS (hypotheses to validate), not constants of nature.
What differs from a naive score is how *urgency* is measured:

    urgency = 1 / (1 + slack / tau),   slack = free working minutes before the
                                        deadline - the task's P90 duration

Urgency therefore reflects whether the task can still be done comfortably, using
the learned duration, rather than raw calendar distance.  A task whose P90 no
longer fits before its deadline has urgency 1 and is flagged "at risk".
Overdue tasks get a fixed, capped urgency and are flagged for attention so they
never dominate every future plan.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Weights:
    importance: float = 0.35
    urgency: float = 0.30
    goal: float = 0.20
    priority: float = 0.15
    urgency_tau: float = 240.0      # minutes of slack at which urgency = 0.5
    overdue_urgency: float = 0.70
    deferral_decay: float = 0.90    # multiplicative per recent deferral (max 5)


def urgency_from_slack(slack_min: Optional[float], tau: float) -> float:
    if slack_min is None:
        return 0.0
    if slack_min < 0:
        return 1.0
    return 1.0 / (1.0 + slack_min / tau)


@dataclass(frozen=True)
class ScoreBreakdown:
    """Weighted contributions to the score (weight x normalised input), not raw inputs."""
    importance: float
    urgency: float
    goal: float
    priority: float
    deferral_factor: float
    total: float


def score(w: Weights, importance: float, urgency: float, goal: float,
          user_priority: bool, deferrals: int, has_deadline: bool) -> ScoreBreakdown:
    parts = (w.importance * importance, w.urgency * urgency, w.goal * goal,
             w.priority * (1.0 if user_priority else 0.0))
    factor = 1.0
    if not has_deadline and deferrals > 0:     # never punish a task that has a real deadline
        factor = w.deferral_decay ** min(deferrals, 5)
    return ScoreBreakdown(*parts, factor, sum(parts) * factor)
