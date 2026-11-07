import json
import math
from datetime import date

import numpy as np
import pytest

import classifier.features as features_module
from classifier.categories import category, window_key
from classifier.evaluate import average_precision, episode_metrics, load_day, tick_metrics
from classifier.features import (FEATURES, PAUSED_AFK, PAUSED_LOCKED, PAUSED_MEETING, PAUSED_NO_WINDOW, SCORING,
                                 TICK_S, compute_features)
from classifier.loader import Trace, load_export
from classifier.scorer import RESET_AFTER_S, StuckScorer, Weights, load_weights, profile_for, raw_score, score_series
from synth.categories import category as synth_category
from synth.generator import generate_day
from synth.writer import write_day

T0 = 1_800_000_000.0                 # a multiple of 5 s, like real input bins
REAL_AFK_TIMEOUT_S = 180.0           # fixed here, so patching the classifier's own value can't change what is hidden
IDLE = dict(presses=0, clicks=0, deltaX=0.0, deltaY=0.0, scrollX=0, scrollY=0)
EDITOR = {"app": "Code", "title": "main.py — proj"}
BROWSER = {"app": "Safari", "title": "Docs", "url": "https://docs.example.com"}


def ev(t, duration, data):
    return dict(t=T0 + t, duration=float(duration), data=data)


def trace(window, length=600.0, afk=None, terminal=(), fs=(), hooks=False, typing=False):
    inp = [ev(t, TICK_S, {**IDLE, "presses": 5 if typing else 0})
           for t in np.arange(0.0, length, TICK_S).tolist()]
    return Trace(dict(window=window, input=inp, afk=afk or [ev(0, length, {"status": "not-afk"})],
                      terminal=list(terminal), fs=list(fs)), hooks)


def at(series, seconds):
    return int(round(seconds / TICK_S)) - 1


@pytest.fixture(scope="module")
def days(tmp_path_factory):
    root = tmp_path_factory.mktemp("days")
    out = {}
    for seed in (2, 3, 4):
        result = generate_day(seed, date(2026, 11, 4), user=seed)
        out[seed] = (write_day(result, root / f"d{seed}"), result)
    return out


# ------------------------------------------------------------------ loader
def test_loader_merges_hostnames_sorts_and_ignores_web(tmp_path):
    def bucket(host, btype, events):
        return dict(id=f"{btype}_{host}", type=btype, client="x", hostname=host, events=events)

    win = lambda ts: dict(timestamp=ts, duration=1.0, data=EDITOR)
    export = dict(buckets={
        "a": bucket("old", "currentwindow", [win("2026-11-04T10:00:10+00:00"), win("2026-11-04T10:00:00Z")]),
        "b": bucket("new", "currentwindow", [win("2026-11-04T10:00:05+00:00")]),
        "c": bucket("old", "afkstatus", [dict(timestamp="2026-11-04T10:00:00+00:00", duration=9.0, data={"status": "not-afk"})]),
        "d": bucket("old", "os.hid.input", [dict(timestamp="2026-11-04T10:00:00+00:00", duration=5.0, data=IDLE)]),
        "e": bucket("old", "web.tab.current", [dict(timestamp="2026-11-04T10:00:00+00:00", duration=5.0, data={})]),
    })
    path = tmp_path / "export.json"
    path.write_text(json.dumps(export), encoding="utf-8")
    t = load_export(path)
    times = [e["t"] for e in t.streams["window"]]
    assert times == sorted(times) and len(times) == 3
    assert not t.hooks and "web" not in t.streams


def test_loader_rejects_an_export_missing_a_required_stream(tmp_path):
    path = tmp_path / "export.json"
    path.write_text(json.dumps(dict(buckets={})), encoding="utf-8")
    with pytest.raises(ValueError, match="window"):
        load_export(path)


def test_scoring_needs_only_the_export_and_never_the_labels(days, tmp_path):
    day_dir, _ = days[2]
    alone = tmp_path / "aw-export.json"
    alone.write_bytes((day_dir / "aw-export.json").read_bytes())
    series = compute_features(load_export(alone))
    assert len(score_series(series, load_weights(profile_for(series)))) == len(series.times)


# ------------------------------------------------------------------ categories
def test_categories_agree_with_the_generator_on_what_the_score_uses(days):
    shared = {"editor", "terminal", "terminal_editor", "agent", "chat", "meeting", "office", "web_work", "browser", "lock"}
    for _, result in days.values():
        for e in result["events"]["window"]:
            theirs = synth_category(e["data"])
            if theirs in shared:
                assert category(e["data"]) == theirs, e["data"]


def test_video_is_not_a_work_surface():
    assert category({"app": "Google Chrome", "title": "x - YouTube", "url": "https://www.youtube.com/watch?v=1"}) == "media"


def test_window_key_ignores_title_noise_only():
    plain = window_key({"app": "Code", "title": "a.py — p"})
    assert window_key({"app": "Code", "title": "● a.py — p"}) == plain
    assert window_key({"app": "Code", "title": "b.py — p"}) != plain
    assert window_key({"app": "Slack", "title": "(3) general"}) == window_key({"app": "Slack", "title": "general"})
    spin = window_key({"app": "Terminal", "title": "p — ◐ Fix tests — node ◂ claude"})
    assert spin == window_key({"app": "Terminal", "title": "p — ✳ Fix tests — node ◂ claude"})


# ------------------------------------------------------------------ features
def test_quick_flicks_are_not_app_switches():
    flicks = []
    for k in range(10):                               # 1 s away from the editor, every 20 s
        flicks += [ev(20 * k, 19, EDITOR), ev(20 * k + 19, 1, BROWSER)]
    series = compute_features(trace(flicks, length=200.0))
    assert series.values["toggling"].max() == 0 and series.values["revisits"].max() == 0


def rapid_coding(length, new_every=1):
    """Hopping every 10 s, reaching a new file on every `new_every`-th visit to the editor."""
    rows = []
    for k in range(int(length // 10)):
        if k % 2:
            rows.append(ev(10 * k, 10, BROWSER))
        else:
            rows.append(ev(10 * k, 10, {"app": "Code", "title": f"f{k // (2 * new_every)}.py \u2014 proj"}))
    return rows


def test_rapid_coding_that_keeps_reaching_new_windows_is_not_circling():
    series = compute_features(trace(rapid_coding(900), length=900.0))
    i = at(series, 800)
    assert series.values["toggling"][i] == 1.0            # just as busy as someone stuck
    assert series.values["reaching"][i] == 1.0
    assert series.values["circling"][i] < 0.05


def test_hopping_between_the_same_few_windows_is_circling():
    series = compute_features(trace(rapid_coding(900, new_every=1000), length=900.0))
    i = at(series, 800)
    assert series.values["toggling"][i] == 1.0
    assert series.values["reaching"][i] == 0.0
    assert series.values["circling"][i] > 0.7


def test_a_long_quiet_stretch_in_one_window_is_not_circling():
    series = compute_features(trace([ev(0, 900, EDITOR)], length=900.0))
    assert series.values["circling"].max() == 0


def test_sustained_toggling_and_returning_raise_their_features():
    toggles = [ev(10 * k, 10, EDITOR if k % 2 else BROWSER) for k in range(30)]
    series = compute_features(trace(toggles, length=300.0), window_s=60)
    i = at(series, 200)
    assert series.values["toggling"][i] == 1.0 and series.values["revisits"][i] == 1.0


def test_each_failure_counts_only_once_the_command_has_finished():
    fail = dict(exit_code=1, cmd_kind="pytest", error_sig="abcd1234", error_type="ValueError")
    series = compute_features(trace([ev(0, 600, EDITOR)], terminal=[ev(100, 50, fail)], hooks=True))
    assert series.values["failures"][at(series, 145)] == 0
    assert series.values["failures"][at(series, 155)] > 0


def test_ctrl_c_is_not_a_failure_and_a_repeated_error_is_a_repeat():
    sig = dict(exit_code=1, cmd_kind="pytest", error_sig="abcd1234", error_type="ValueError")
    ctrl_c = dict(exit_code=130, cmd_kind="shell", error_sig=None, error_type=None)
    series = compute_features(trace([ev(0, 600, EDITOR)], terminal=[ev(50, 1, ctrl_c)], hooks=True))
    assert series.values["failures"].max() == 0
    series = compute_features(trace([ev(0, 600, EDITOR)], terminal=[ev(50, 1, sig), ev(90, 1, sig)], hooks=True))
    assert series.values["repeats"][at(series, 80)] == 0 and series.values["repeats"][at(series, 100)] > 0


def test_a_commit_counts_as_progress():
    commit = ev(100, 0, dict(kind="commit", file_id=None))
    series = compute_features(trace([ev(0, 600, EDITOR)], fs=[commit], hooks=True))
    assert series.values["progress"][at(series, 95)] == 0 and series.values["progress"][at(series, 200)] == 1


def test_features_stay_between_0_and_1(days):
    for day_dir, _ in days.values():
        series = compute_features(load_export(day_dir / "aw-export.json"))
        for name in FEATURES:
            assert series.values[name].min() >= 0 and series.values[name].max() <= 1


def cut_trace(full, cut):
    """The trace as a live system would have it at time `cut`."""
    streams = {}
    for name, rows in full.streams.items():
        kept = []
        for e in rows:
            # a bin with counts arrives when it closes, but a run of zeros grows as a heartbeat
            active = name == "terminal" or (name == "input" and any(e["data"].values()))
            if active and e["t"] + e["duration"] > cut:
                continue
            # the watcher reports AFK only after its timeout, backdated to the last input
            if name == "afk" and e["data"]["status"] == "afk" and cut < e["t"] + REAL_AFK_TIMEOUT_S:
                continue
            if e["t"] < cut:
                kept.append(dict(e, duration=min(e["duration"], cut - e["t"])))
        streams[name] = kept
    return Trace(streams, full.hooks)


def future_leaks(full, cuts=60):
    """Names of features or pause states that change once later events are hidden."""
    series = compute_features(full)
    leaks = set()
    moments = [series.times[len(series.times) * k // (cuts + 1)] for k in range(1, cuts + 1)]
    # sparse events would otherwise rarely fall near a cut, so also cut just before each one lands
    moments += [e["t"] + e["duration"] - 15 for e in full.streams["terminal"]]
    moments += [e["t"] + 30 for e in full.streams["afk"] if e["data"]["status"] == "afk"]
    for cut in moments:
        if not series.times[20] < cut < series.times[-1]:
            continue
        partial = compute_features(cut_trace(full, cut))
        n = int(np.searchsorted(series.times, cut - 2 * TICK_S, "right"))
        assert partial.times[0] == series.times[0] and n <= len(partial.times)
        leaks |= {name for name in FEATURES if not np.allclose(partial.values[name][:n], series.values[name][:n])}
        if (partial.pause[:n] != series.pause[:n]).any():
            leaks.add("pause")
    return leaks


@pytest.fixture(scope="module")
def full_trace(days):
    """The day with the most failed commands, so the leak checks have something to catch."""
    traces = [load_export(d / "aw-export.json") for d, _ in days.values()]
    fails = lambda tr: sum(e["data"]["exit_code"] not in (0, 130) for e in tr.streams["terminal"])
    best = max(traces, key=fails)
    assert fails(best) >= 5
    return best


def test_features_never_use_the_future(full_trace):
    """Cutting a trace off at any moment must not change anything computed before that moment."""
    assert future_leaks(full_trace) == set()


def test_the_future_check_catches_real_leaks(full_trace, monkeypatch):
    """A check that can't fail proves nothing: put leaks in on purpose and make sure each is caught."""
    original = features_module._terminal_events

    def early_failures(terminal):
        failures, repeats = original(terminal)
        return [t - 60 for t in failures], repeats

    with monkeypatch.context() as m:
        m.setattr(features_module, "_terminal_events", early_failures)
        assert "failures" in future_leaks(full_trace)
    with monkeypatch.context() as m:
        m.setattr(features_module, "AFK_TIMEOUT_S", 0.0)          # trust the backdated AFK start
        assert "pause" in future_leaks(full_trace)
    with monkeypatch.context() as m:
        original_attention = features_module._attention_events

        def early_switches(window):
            switches, revisits, visits = original_attention(window)
            return [t - 30 for t in switches], revisits, visits

        m.setattr(features_module, "_attention_events", early_switches)
        assert "toggling" in future_leaks(full_trace)


# ------------------------------------------------------------------ pausing
def test_quiet_staring_is_scored_not_paused():
    series = compute_features(trace([ev(0, 170, EDITOR)], length=170.0))
    assert (series.pause == SCORING).all()
    assert series.values["stalling"][-1] == 1.0


def test_afk_pauses_only_once_the_watcher_could_know():
    afk = [ev(0, 100, {"status": "not-afk"}), ev(100, 500, {"status": "afk"})]
    series = compute_features(trace([ev(0, 600, EDITOR)], afk=afk))
    assert series.pause[at(series, 270)] == SCORING
    assert series.pause[at(series, 285)] == PAUSED_AFK


def test_locked_screen_and_meeting_pause_at_once():
    locked = compute_features(trace([ev(0, 100, EDITOR), ev(100, 500, {"app": "loginwindow", "title": ""})]))
    assert locked.pause[at(locked, 105)] == PAUSED_LOCKED
    meeting = compute_features(trace([ev(0, 100, EDITOR), ev(100, 500, {"app": "zoom.us", "title": "Zoom Meeting"})]))
    assert meeting.pause[at(meeting, 105)] == PAUSED_MEETING


def test_a_sleeping_laptop_pauses_scoring():
    series = compute_features(trace([ev(0, 100, EDITOR), ev(400, 200, EDITOR)]))
    assert series.pause[at(series, 250)] == PAUSED_NO_WINDOW
    assert series.pause[at(series, 450)] == SCORING


# ------------------------------------------------------------------ scorer
EXAMPLE = Weights(-3.0, dict(toggling=1.5, revisits=1.0, stalling=0.8, failures=2.0), 0.5)
STUCK_FEATURES = dict(toggling=4 / 6, revisits=2 / 3, stalling=0.75, failures=1.0)


def test_score_matches_the_worked_example():
    assert raw_score(EXAMPLE, STUCK_FEATURES) == pytest.approx(0.78, abs=0.005)
    assert raw_score(EXAMPLE, dict(toggling=0, revisits=0, stalling=0.2, failures=0)) == pytest.approx(0.055, abs=0.005)
    scorer = StuckScorer(EXAMPLE)
    scorer.score = 0.40
    assert scorer.update(STUCK_FEATURES)["stuck_score"] == pytest.approx(0.44, abs=0.005)


def test_one_odd_moment_cannot_spike_the_score_but_a_sustained_one_rises():
    scorer = StuckScorer(EXAMPLE)
    start = scorer.score
    assert scorer.update(dict(toggling=1, revisits=1, stalling=1, failures=1))["stuck_score"] < start + 0.1
    for _ in range(60):
        out = scorer.update(dict(toggling=1, revisits=1, stalling=1, failures=1))
    assert out["stuck_score"] > 0.8


def test_pause_holds_the_score_and_a_long_absence_resets_it():
    scorer = StuckScorer(EXAMPLE)
    for _ in range(40):
        scorer.update(STUCK_FEATURES)
    high = scorer.score
    out = scorer.update(STUCK_FEATURES, PAUSED_AFK)
    assert out == dict(stuck_score=high, status="paused", reason="afk")
    for _ in range(int(RESET_AFTER_S / TICK_S)):
        out = scorer.update(STUCK_FEATURES, PAUSED_AFK)
    assert out["stuck_score"] < 0.1


def test_replay_matches_the_live_scorer(days):
    day_dir, _ = days[2]
    series = compute_features(load_export(day_dir / "aw-export.json"))
    w = load_weights(profile_for(series))
    replay = score_series(series, w)
    scorer = StuckScorer(w)
    for i in range(0, len(replay)):
        feats = {k: float(v[i]) for k, v in series.values.items()}
        assert scorer.update(feats, int(series.pause[i]))["stuck_score"] == pytest.approx(replay[i], abs=1e-9)


def test_stock_activitywatch_gets_its_own_profile(days):
    day_dir, _ = days[2]
    with_hooks = compute_features(load_export(day_dir / "aw-export.json"))
    assert profile_for(with_hooks) == "with_hooks"
    assert profile_for(with_hooks, use_hooks=False) == "window_input_afk"
    full, stock = load_weights("with_hooks").weights, load_weights("window_input_afk").weights
    assert "failures" in full and not {"failures", "repeats", "progress"} & set(stock)
    assert full != stock


# ------------------------------------------------------------------ evaluation
def test_average_precision_rewards_ranking_stuck_first():
    truth = np.array([True, False, False, False])
    assert average_precision(np.array([0.9, 0.1, 0.2, 0.3]), truth) == 1.0
    assert average_precision(np.array([0.1, 0.9, 0.8, 0.7]), truth) == 0.25


def test_tick_metrics():
    m = tick_metrics(np.array([0.9, 0.9, 0.1, 0.1]), np.array([True, False, True, False]), 0.5)
    assert m["precision"] == 0.5 and m["recall"] == 0.5 and m["flagged"] == 0.5


def test_flagging_everything_cannot_look_good(days):
    """An alarm that never turns off raises few false alarms, so the time flagged has to expose it."""
    day_dir, _ = days[2]
    day, _ = load_day(day_dir, 2, True, 60.0)
    day.score = np.ones(len(day.score))
    m = episode_metrics([day], 0.5)
    assert m["false_alarms_per_hour"] < 5 and m["calm_time_flagged"] == 1.0


def test_episodes_are_measured_from_labels(days):
    day_dir, result = days[2]
    day, _ = load_day(day_dir, 2, True, 60.0)
    stuck = [l for l in result["labels"] if l["state"] == "stuck"]
    assert 0 < len(day.episodes) <= len(stuck)
    assert (day.state == "stuck").sum() * TICK_S == pytest.approx(sum(l["duration_s"] for l in stuck), abs=TICK_S * 2 * len(stuck))
    assert math.isclose(episode_metrics([day], 0.0)["recall"], 1.0) or day.series.pause.min() != SCORING
