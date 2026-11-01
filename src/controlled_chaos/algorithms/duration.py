"""Duration model: learns how long *your* tasks really take.

Why this exists
    People systematically under-estimate task durations (the planning fallacy;
    Buehler, Griffin & Ross 1994: <50% of tasks finish within the predicted time).
    A planner that trusts nominal estimates therefore over-fills the day.

Method
    Work in log space: r = ln(actual / estimate).
    1. Hierarchical shrinkage.  Level chain  prior -> global -> project -> category.
       Each level updates a Normal / scaled-inverse-chi-square style posterior mean
       and variance from its own observations, shrunk towards its parent with
       ``n0`` pseudo-observations.  Sparse groups borrow strength from their parent.
    2. Tail calibration (split-conformal flavour).  Once enough history exists, the
       empirical (1-alpha) standardized-residual quantile replaces the Gaussian one,
       so the model's upper bound is calibrated to the user's actual residuals
       rather than to a Gaussian assumption (cf. Romano, Patterson & Candes 2019,
       Conformalized Quantile Regression, NeurIPS).
    3. Online level adaptation (Gibbs & Candes 2021, Adaptive Conformal Inference,
       NeurIPS): the working miscoverage level alpha_t is nudged after every
       observation so long-run coverage of the upper bound tracks the target even
       when the user's habits drift.

    The prior (median overrun 1.25x, log-sd 0.45) is an ASSUMPTION used only until
    data arrives; it is deliberately conservative and is user-adjustable.
    The coverage guarantees of ACI are for the long-run average error rate, not for
    any single day.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

_N = NormalDist()
Z90 = _N.inv_cdf(0.9)


def norm_inv(p: float) -> float:
    return _N.inv_cdf(min(max(p, 1e-9), 1 - 1e-9))


@dataclass(frozen=True)
class Prior:
    mu0: float = math.log(1.25)   # ASSUMPTION: median actual/estimate = 1.25
    sigma0: float = 0.45          # ASSUMPTION: log-sd of the ratio
    n0: float = 4.0               # pseudo-observations behind each shrinkage step


def keys_for(project_id: Optional[str], category: Optional[str]) -> List[str]:
    keys = ["g"]
    if project_id:
        keys.append("p:" + project_id)
    if category:
        keys.append("c:" + category)
    return keys


class DurationModel:
    def __init__(self, prior: Prior = Prior(), alpha: float = 0.10, gamma: float = 0.02,
                 window: int = 200, min_tail: int = 10, learn: bool = True):
        self.prior = prior
        self.alpha = alpha
        self.alpha_t = alpha
        self.gamma = gamma
        self.window = window
        self.min_tail = min_tail
        self.learn = learn
        self.groups: Dict[str, List[float]] = {}

    # ------------------------------------------------------------ internals
    def _shrink(self, pmu: float, pvar: float, xs: Sequence[float]) -> Tuple[float, float]:
        n = len(xs)
        if n == 0:
            return pmu, pvar
        n0 = self.prior.n0
        m = sum(xs) / n
        ss = sum((x - m) ** 2 for x in xs)
        mu = (n0 * pmu + n * m) / (n0 + n)
        var = (n0 * pvar + ss + (n * n0 / (n0 + n)) * (m - pmu) ** 2) / (n0 + n)
        return mu, var

    def _chain(self, keys: Sequence[str]) -> Tuple[float, float, float, float]:
        mu, var = self.prior.mu0, self.prior.sigma0 ** 2
        gmu, gvar = mu, var
        for i, k in enumerate(keys):
            mu, var = self._shrink(mu, var, self.groups.get(k, ()))
            if i == 0:
                gmu, gvar = mu, var
        return mu, var, gmu, gvar

    def _tail_scale(self, gmu: float, gvar: float) -> float:
        xs = self.groups.get("g", ())
        n = len(xs)
        if n < self.min_tail:
            return 1.0
        sd = math.sqrt(gvar)
        z = sorted((x - gmu) / sd for x in xs)
        level = 1.0 - self.alpha
        k = math.ceil((n + 1) * level)
        if k > n:
            return 2.0
        return min(2.5, max(0.7, z[k - 1] / norm_inv(level)))

    def _level_mult(self) -> float:
        return norm_inv(1.0 - self.alpha_t) / norm_inv(1.0 - self.alpha)

    # ---------------------------------------------------------------- public
    def predict(self, estimate: float, keys: Sequence[str]) -> Tuple[float, float]:
        """(mu, sigma) of ln(actual minutes). Median actual = exp(mu)."""
        estimate = max(float(estimate), 1.0)
        if not self.learn:
            return math.log(estimate) + self.prior.mu0, self.prior.sigma0
        mu, var, gmu, gvar = self._chain(keys)
        sigma = math.sqrt(var) * self._tail_scale(gmu, gvar) * self._level_mult()
        return math.log(estimate) + mu, sigma

    def observe(self, estimate: float, actual: float, keys: Sequence[str]) -> None:
        if not self.learn or estimate <= 0 or actual <= 0:
            return
        mu_log, sigma = self.predict(estimate, keys)
        ub = math.exp(mu_log + sigma * norm_inv(1.0 - self.alpha))
        err = 1.0 if actual > ub else 0.0
        self.alpha_t = min(0.5, max(0.01, self.alpha_t + self.gamma * (self.alpha - err)))
        r = max(-3.0, min(3.0, math.log(actual / estimate)))
        for k in dict.fromkeys(["g", *keys]):
            xs = self.groups.setdefault(k, [])
            xs.append(r)
            if len(xs) > self.window:
                del xs[0]

    def n_observations(self, keys: Sequence[str] = ("g",)) -> int:
        return max((len(self.groups.get(k, ())) for k in keys), default=0)

    @classmethod
    def replay(cls, observations: Iterable[Tuple[float, float, Optional[str], Optional[str]]], **kw) -> "DurationModel":
        """Rebuild the model deterministically from stored (estimate, actual, project, category)."""
        m = cls(**kw)
        for est, act, proj, cat in observations:
            m.observe(est, act, keys_for(proj or None, cat or None))
        return m
