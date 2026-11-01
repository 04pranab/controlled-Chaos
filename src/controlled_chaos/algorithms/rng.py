"""Small deterministic RNG so the same inputs always give the same plan.

Plans must be reproducible (testable, explainable, and stable between app
restarts); Python's global ``random`` state would make recommendations jitter.
"""
from __future__ import annotations

import math
from typing import List

_M = 0xFFFFFFFF


def fnv1a(s: str) -> int:
    h = 0x811C9DC5
    for ch in s.encode("utf-8"):
        h ^= ch
        h = (h * 0x01000193) & _M
    return h


class Mulberry32:
    def __init__(self, seed: int):
        self.a = seed & _M

    def next(self) -> float:
        self.a = (self.a + 0x6D2B79F5) & _M
        t = self.a
        t = ((t ^ (t >> 15)) * (t | 1)) & _M
        t ^= (t + (((t ^ (t >> 7)) * (t | 61)) & _M)) & _M
        return ((t ^ (t >> 14)) & _M) / 4294967296.0


def std_normals(seed: int, n: int) -> List[float]:
    """Box-Muller standard normals from a fixed seed (common random numbers)."""
    r = Mulberry32(seed)
    out: List[float] = []
    for _ in range(n):
        u1 = max(r.next(), 1e-12)
        u2 = r.next()
        out.append(math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2))
    return out
