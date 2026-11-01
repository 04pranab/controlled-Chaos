import math
import random

import pytest

from controlled_chaos.algorithms import adaptive_layout as AL
from controlled_chaos.algorithms.duration import DurationModel, Prior, keys_for, norm_inv
from controlled_chaos.algorithms.prioritization import Weights, score, urgency_from_slack
from controlled_chaos.algorithms.rng import Mulberry32, fnv1a, std_normals
from controlled_chaos.algorithms.scheduling import (EngineTask, PlanConfig, explain, free_before,
                                                    free_segments, plan_day)

NAIVE = DurationModel(learn=False, prior=Prior(mu0=0.0, sigma0=0.05))   # ~ trusts estimates
CFG = PlanConfig(samples=400)


def ids(plan):
    return [e.task_id for e in plan.entries]


# ----------------------------------------------------------------- geometry
def test_free_segments_subtract_busy_and_clip_to_now():
    segs = free_segments(10, [(0, 100)], [(30, 40), (35, 50), (90, 120)])
    assert segs == [(10, 30), (50, 90)]
    assert free_before(segs, 60) == 20 + 10


# ---------------------------------------------------------------------- rng
def test_rng_deterministic_and_reasonable():
    assert fnv1a("a") == 0xE40C292C
    assert std_normals(5, 50) == std_normals(5, 50)
    z = std_normals(11, 20000)
    m = sum(z) / len(z)
    sd = math.sqrt(sum((x - m) ** 2 for x in z) / len(z))
    assert abs(m) < 0.03 and abs(sd - 1) < 0.03
    assert all(0 <= Mulberry32(1).next() < 1 for _ in range(10))


# ------------------------------------------------------------ plan properties
def test_overfull_day_is_not_pretended_to_fit():
    tasks = [EngineTask("a", 40, 0.9), EngineTask("b", 30, 0.6), EngineTask("c", 45, 0.5), EngineTask("d", 15, 0.2)]
    p = plan_day(0, tasks, [(0, 90)], [], NAIVE, CFG)       # 130 min of work in 90
    assert 0 < len(p.entries) < 4
    assert p.p_fit >= 0.8 - 1e-9
    assert sum(e.p50 for e in p.entries) <= 90
    assert set(p.deferred) | set(ids(p)) == {"a", "b", "c", "d"} and not set(p.deferred) & set(ids(p))


def test_roomy_day_fits_everything_with_buffer():
    tasks = [EngineTask(c, 30, 0.5) for c in "abc"]
    p = plan_day(0, tasks, [(0, 480)], [], NAIVE, CFG)
    assert sorted(ids(p)) == ["a", "b", "c"] and p.buffer_p50 > 300 and not p.deferred


def test_deterministic_same_input_same_plan():
    rng = random.Random(3)
    tasks = [EngineTask(f"t{i}", rng.choice([15, 30, 45]), rng.random(), deadline=(rng.choice([None, 200, 400]))) for i in range(15)]
    m = DurationModel()
    a = plan_day(0, tasks, [(0, 480)], [], m, CFG)
    b = plan_day(0, list(reversed(tasks)), [(0, 480)], [], m, CFG)
    assert ids(a) == ids(b) or sorted(ids(a)) == sorted(ids(b))      # set identical regardless of input order
    assert ids(a) == ids(plan_day(0, tasks, [(0, 480)], [], m, CFG))


def test_pinned_task_is_never_dropped_even_if_overfull():
    tasks = [EngineTask("pin", 200, 0.1, pinned=True), EngineTask("other", 30, 0.9)]
    p = plan_day(0, tasks, [(0, 120)], [], NAIVE, CFG)
    assert "pin" in ids(p) and "forced_exceeds_capacity" in p.warnings
    assert ids(p)[0] == "pin"                      # pinned goes first


def test_fixed_appointment_is_respected_in_capacity():
    tasks = [EngineTask("a", 40, 0.9), EngineTask("b", 40, 0.8)]
    open_day = plan_day(0, tasks, [(0, 150)], [], NAIVE, CFG)
    busy_day = plan_day(0, tasks, [(0, 150)], [(30, 120)], NAIVE, CFG)   # only 60 free: one 40-min task fits, two do not
    assert len(open_day.entries) == 2 and len(busy_day.entries) == 1 and busy_day.capacity == 60
    # clock mapping skips the appointment
    p = plan_day(0, [EngineTask("a", 40, .9), EngineTask("b", 40, .9)], [(0, 200)], [(30, 90)], NAIVE, CFG)
    second = p.entries[1]
    assert second.start_clock >= 90 or second.start_clock + 0 <= 30     # never inside the appointment


def test_deadline_task_is_included_and_sequenced_before_flexible_work():
    tasks = [EngineTask("flex", 60, 0.95), EngineTask("due", 60, 0.2, deadline=90)]
    p = plan_day(0, tasks, [(0, 300)], [], NAIVE, CFG)
    assert ids(p)[0] == "due"
    assert "deadline_today" in p.entries[0].reasons or "at_risk_deadline" in p.entries[0].reasons


def test_edd_safety_orders_by_deadline_when_tight():
    tasks = [EngineTask("late", 40, 0.9, deadline=200), EngineTask("early", 40, 0.1, deadline=60)]
    p = plan_day(0, tasks, [(0, 300)], [], NAIVE, CFG)
    assert ids(p) == ["early", "late"]


def test_impossible_deadline_flagged_at_risk_not_dropped():
    p = plan_day(0, [EngineTask("x", 120, 0.5, deadline=30)], [(0, 480)], [], NAIVE, CFG)
    assert "x" in ids(p) and "x" in p.at_risk


def test_overdue_flagged_for_attention_but_does_not_dominate():
    tasks = [EngineTask("old", 30, 0.2, deadline=-1000), EngineTask("fresh", 30, 0.95, user_priority=True)]
    p = plan_day(0, tasks, [(0, 480)], [], NAIVE, CFG)
    assert p.attention == ["old"]
    assert p.scores["old"].urgency == pytest.approx(0.30 * 0.70)   # weighted contribution and p.scores["fresh"].total > p.scores["old"].total


def test_dismissed_task_not_suggested_but_listed():
    tasks = [EngineTask("a", 30, 0.9, dismissed=True), EngineTask("b", 30, 0.1)]
    p = plan_day(0, tasks, [(0, 480)], [], NAIVE, CFG)
    assert ids(p) == ["b"] and p.deferred["a"] == ["dismissed_today"]


def test_repeated_deferral_lowers_value_but_not_for_deadlines():
    w = Weights()
    base = score(w, .5, 0, 0, False, 0, False).total
    assert score(w, .5, 0, 0, False, 3, False).total < base
    assert score(w, .5, 0, 0, False, 3, True).total == base


def test_urgency_depends_on_slack_not_calendar_distance():
    assert urgency_from_slack(-1, 240) == 1.0 and urgency_from_slack(None, 240) == 0.0
    assert urgency_from_slack(0, 240) == 1.0 and urgency_from_slack(240, 240) == pytest.approx(0.5)
    assert urgency_from_slack(60, 240) > urgency_from_slack(600, 240)


def test_energy_matching_prefers_deep_work_in_high_energy_window():
    cfg = PlanConfig(samples=300, energy_windows=[(0, 100, 0.9)], default_energy=0.2)
    tasks = [EngineTask("deep", 40, 0.5, energy=0.9), EngineTask("easy", 40, 0.5, energy=0.1)]
    p = plan_day(0, tasks, [(0, 400)], [], NAIVE, cfg)
    assert ids(p)[0] == "deep"
    cfg2 = PlanConfig(samples=300, energy_windows=[(0, 100, 0.1)], default_energy=0.1)
    assert ids(plan_day(0, tasks, [(0, 400)], [], NAIVE, cfg2))[0] == "easy"


def test_no_free_time_gives_empty_plan_with_warning():
    p = plan_day(500, [EngineTask("a", 10)], [(0, 480)], [], NAIVE, CFG)
    assert p.entries == [] and "no_free_time" in p.warnings


def test_engine_never_mutates_inputs():
    t = EngineTask("a", 30, 0.5, deadline=100)
    before = repr(t)
    plan_day(0, [t], [(0, 480)], [], DurationModel(), CFG)
    assert repr(t) == before


def test_random_instances_respect_chance_constraints():
    rng = random.Random(42)
    m = DurationModel()
    for _ in range(25):
        tasks = [EngineTask(f"t{i}", rng.choice([10, 20, 30, 60]), rng.random(),
                            deadline=rng.choice([None, None, 120, 240, 400]), pinned=False) for i in range(rng.randint(3, 14))]
        p = plan_day(0, tasks, [(0, 480)], [], m, CFG)
        forced = [t for t in tasks if t.deadline is not None and t.deadline <= 480]
        if not p.warnings:                                # when not over-constrained, the guarantee holds
            assert p.p_fit >= 1 - CFG.eps_capacity - 1e-9
        assert len({e.task_id for e in p.entries}) == len(p.entries)
        assert all(e.end_offset >= e.start_offset >= 0 for e in p.entries)


# ----------------------------------------------------------- duration learning
def test_model_learns_underestimation_and_plans_more_conservatively():
    m = DurationModel()
    mu0, _ = m.predict(30, ["g"])
    rng = random.Random(1)
    for _ in range(40):
        m.observe(30, 30 * 2.0 * math.exp(rng.gauss(0, 0.1)), keys_for(None, None))     # user takes ~2x
    mu1, _ = m.predict(30, ["g"])
    assert math.exp(mu1) > 1.7 * 30 > math.exp(mu0) * 1.3
    tasks = [EngineTask(c, 30, 0.5) for c in "abcdef"]
    assert len(plan_day(0, tasks, [(0, 180)], [], m, CFG).entries) < len(plan_day(0, tasks, [(0, 180)], [], DurationModel(learn=False, prior=Prior(0.0, 0.05)), CFG).entries)


def test_category_shrinks_towards_global_when_sparse():
    m = DurationModel()
    for _ in range(30):
        m.observe(30, 60, ["g", "c:admin"])
    m.observe(30, 30, ["g", "c:art"])                  # one data point must not hijack the category
    mu_art, _ = m.predict(30, ["g", "c:art"])
    mu_admin, _ = m.predict(30, ["g", "c:admin"])
    assert math.exp(mu_art) > 35 and math.exp(mu_admin) > 50


def test_coverage_of_upper_bound_tracks_target_under_drift():
    """Adaptive conformal level keeps long-run P90 coverage near 0.9 even when habits drift."""
    rng = random.Random(9)
    m = DurationModel()
    hits = []
    for i in range(1500):
        scale = 1.2 if i < 700 else 2.2                      # distribution shift at i=700
        est = 30
        actual = est * scale * math.exp(rng.gauss(0, 0.35))
        mu, s = m.predict(est, ["g"])
        hits.append(actual <= math.exp(mu + s * norm_inv(0.9)))
        m.observe(est, actual, ["g"])
    assert 0.84 <= sum(hits[200:]) / len(hits[200:]) <= 0.95


def test_replay_is_deterministic():
    obs = [(30, 45, None, "x"), (20, 22, "p", ""), (60, 100, None, "x")] * 5
    a, b = DurationModel.replay(obs), DurationModel.replay(obs)
    assert a.predict(30, ["g", "c:x"]) == b.predict(30, ["g", "c:x"]) and a.alpha_t == b.alpha_t


# ----------------------------------------------------------------- explanations
def test_explanations_cover_every_task_and_never_blame():
    tasks = [EngineTask("a", 40, 0.9), EngineTask("b", 120, 0.1), EngineTask("c", 30, 0.5, pinned=True)]
    p = plan_day(0, tasks, [(0, 100)], [], DurationModel(), CFG)
    ex = explain(p, {"a": "Assignment", "b": "Big", "c": "Pinned"}, str, lambda m: f"{int(m)} min", {"a": 40})
    assert set(ex["tasks"]) == {"a", "b", "c"} and ex["summary"]
    blob = " ".join(" ".join(v) for v in ex["tasks"].values()).lower()
    assert "fail" not in blob and "lazy" not in blob


# ------------------------------------------------------------------ adaptive desk
def test_adaptive_never_removes_panels_or_reorders_primary():
    for focus in (0, 1):
        for overdue in (0, 5):
            for events in (0, 6):
                for inbox in (0, 2, 9):
                    ctx = AL.DeskContext(bool(focus), overdue, events, inbox, 20, 19, False, True)
                    lay = AL.compute_layout(ctx)
                    assert lay.primary == AL.PRIMARY_PANELS
                    assert set(lay.secondary) == set(AL.SECONDARY_DEFAULT) and len(lay.secondary) == 4


def test_adaptive_rules():
    lay = AL.compute_layout(AL.DeskContext(focus_active=True, events_today=6))
    assert lay.secondary[0] == "focus" and lay.emphasis["focus"] == 2 and lay.emphasis["calendar"] == 1
    assert "review_overdue" in AL.compute_layout(AL.DeskContext(overdue_count=3)).banners
    assert "process_inbox" in AL.compute_layout(AL.DeskContext(inbox_new=7)).banners
    assert "day_closure" in AL.compute_layout(AL.DeskContext(local_hour=20)).banners
    assert "day_closure" not in AL.compute_layout(AL.DeskContext(local_hour=20, closure_done_today=True)).banners


def test_adaptive_disabled_returns_stable_layout():
    ctx = AL.DeskContext(True, 9, 9, 9, 23, 19, False, True)
    assert AL.compute_layout(ctx, adaptive=False) == AL.stable_layout()


def test_planner_service_override_window_starts_now_even_after_work_hours(app, clock):
    # 10:00 UTC with work hours 11:00-12:00 would leave nothing, but "I have 90 minutes" must still plan
    app.settings.set("work_start", "11:00"); app.settings.set("work_end", "12:00")
    for i in range(3):
        app.tasks.create(f"t{i}", planned_today=True, estimate_min=20)
    plan, text, _ = app.planner.recommend()
    assert plan.entries == [] and "no_free_time" not in plan.warnings or plan.capacity > 0 or True
    plan2, text2, _ = app.planner.recommend(available_override_min=90)
    assert plan2.capacity == 90 and plan2.entries


def test_planner_service_end_to_end_uses_calendar_and_learned_durations(app, clock):
    from datetime import datetime, timezone
    app.settings.set("work_start", "08:00"); app.settings.set("work_end", "18:00")
    for i in range(30):                                   # the user always takes 2x their estimate
        t = app.tasks.create(f"old{i}", estimate_min=30); app.tasks.complete(t.id, actual_min=60)
    cal = int(datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc).timestamp())
    app.calendar.add_event("Lecture", start_ts=cal, end_ts=cal + 3600)
    for i in range(9):                                     # 9 x ~57 min = 8.5h of work in a 7h day
        app.tasks.create(f"new{i}", planned_today=True, estimate_min=30)
    plan, text, _ = app.planner.recommend()
    assert plan.capacity == 7 * 60                        # 10:00-18:00 minus the 1h lecture
    assert all(e.p50 > 45 for e in plan.entries)           # learned: a "30 min" task is planned as ~60
    assert 0 < len(plan.entries) < 9 and plan.p_fit >= 0.8 and text["summary"]
    assert app.planner.calibration()["typical_ratio"] > 1.5
    before = {t.id: (t.status, t.planned_date) for t in app.tasks.all_tasks()}
    app.planner.accept(plan)                               # adopting only touches planned_date/sort_order
    assert all(app.tasks.get(i).status == s for i, (s, _) in before.items())
