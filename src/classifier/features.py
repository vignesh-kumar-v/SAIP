"""Turn raw event streams into per-tick features, using only what is known at each tick.

A tick happens every 5 s, when the input watcher reports. Every feature is causal: an app switch
counts only once it has lasted 2 s, a command only once it has finished, and AFK only once the
watcher could know it. That matters because AFK events in an export are backdated to the last
input, which a live system cannot see until the timeout passes.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .categories import AI_TOOLS, WORK_SURFACES, category, window_key
from .loader import Trace

TICK_S = 5.0
DEFAULT_WINDOW_S = 60.0      # the plan's lookback; the thresholds in weights.json assume it. Circling has its own, longer one
MIN_DWELL_S = 2.0            # shorter visits are cmd-tab flicks and notifications, not a change of attention
REVISIT_MEMORY_S = 300.0     # coming back to a window seen this recently counts as a revisit
ERROR_MEMORY_S = 600.0       # the same error again within this long counts as a repeat
FAILURE_MIN_WINDOW_S = 180.0  # commands can be minutes apart; look back at least this far
PROGRESS_WINDOW_S = 300.0    # a commit is a sign of progress for this long
LOOP_WINDOW_S = 300.0        # circling only shows over minutes: anyone can return to a window twice in a row
NEW_WINDOW_AFTER_S = 1800.0  # a window not seen for this long counts as somewhere new
AFK_TIMEOUT_S = 180.0        # aw-watcher-afk default
WINDOW_GAP_S = 2.0           # tolerance before a missing window event means the machine stopped reporting

# Rates that count as "a lot" for each feature, per minute.
FULL_SWITCHES_PER_MIN = 6.0
FULL_REVISITS_PER_MIN = 3.0
FULL_FAILURES_PER_MIN = 1.0
FULL_REPEATS_PER_MIN = 0.5
FULL_RETURNS = 10.0          # visits beyond the distinct windows in the loop window
WIDE_WINDOWS = 10.0          # this many distinct windows in the loop window isn't a loop
FULL_NEW_WINDOWS = 6.0       # new windows in the loop window; real rapid coding reaches about this many

SCORING, PAUSED_AFK, PAUSED_LOCKED, PAUSED_MEETING, PAUSED_NO_WINDOW, PAUSED_NO_INPUT = range(6)
PAUSE_REASONS = {PAUSED_AFK: "afk", PAUSED_LOCKED: "locked", PAUSED_MEETING: "meeting",
                 PAUSED_NO_WINDOW: "no_window", PAUSED_NO_INPUT: "no_input"}

FEATURES = ("toggling", "revisits", "stalling", "typing", "ai_share", "circling", "reaching", "failures", "repeats",
            "progress")


@dataclass
class FeatureSeries:
    times: np.ndarray                # epoch seconds of each tick
    values: dict                     # feature name -> array in [0, 1]
    pause: np.ndarray                # SCORING, or the reason this tick is paused
    hooks: bool


def _loop_features(visits: list, times: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Going in circles versus getting somewhere, over the loop window.

    Rapid coding and being stuck both switch constantly. The difference is that rapid coding keeps
    reaching new files and pages, while circling keeps returning to the same few windows.
    """
    circling, reaching = np.zeros(len(times)), np.zeros(len(times))
    if not visits:
        return circling, reaching
    ats = np.array([at for at, _ in visits])
    last_seen: dict = {}
    new = np.zeros(len(visits), dtype=bool)
    for i, (at, key) in enumerate(visits):
        new[i] = at - last_seen.get(key, -np.inf) > NEW_WINDOW_AFTER_S
        last_seen[key] = at
    hi = np.searchsorted(ats, times, "right")
    lo = np.searchsorted(ats, times - LOOP_WINDOW_S, "right")
    keys = [key for _, key in visits]
    new_sum = np.concatenate(([0], np.cumsum(new)))
    for j in np.flatnonzero(hi > lo):
        distinct = len(set(keys[lo[j]:hi[j]]))
        returns = hi[j] - lo[j] - distinct
        circling[j] = min(returns / FULL_RETURNS, 1.0) * max(1.0 - distinct / WIDE_WINDOWS, 0.0)
        reaching[j] = min((new_sum[hi[j]] - new_sum[lo[j]]) / FULL_NEW_WINDOWS, 1.0)
    return circling, reaching


def _attention_events(window: list) -> tuple[list, list, list]:
    """Times of sustained app switches and of returns to a recently used window, and every sustained visit."""
    switches, revisits, visits = [], [], []
    last_seen: dict[tuple, float] = {}
    run_key = run_app = pending_key = pending_app = None
    run_end = pending_start = pending_end = 0.0

    def confirm(key, app, start, end):
        """A visit has lasted long enough to count: compare it with the visit before."""
        nonlocal run_key, run_app, run_end
        at = start + MIN_DWELL_S
        if key == run_key:
            run_end = end
            return
        if run_key is not None:
            last_seen[run_key] = run_end
            if app != run_app:
                switches.append(at)
        seen = last_seen.get(key)
        if seen is not None and at - seen <= REVISIT_MEMORY_S:
            revisits.append(at)
        run_key, run_app, run_end = key, app, end
        visits.append((at, key))

    for e in window:
        key = window_key(e["data"])
        if key == pending_key and e["t"] - pending_end <= WINDOW_GAP_S:
            pending_end = e["t"] + e["duration"]
            continue
        if pending_key is not None and pending_end - pending_start >= MIN_DWELL_S:
            confirm(pending_key, pending_app, pending_start, pending_end)
        pending_key, pending_app = key, key[0]
        pending_start, pending_end = e["t"], e["t"] + e["duration"]
    if pending_key is not None and pending_end - pending_start >= MIN_DWELL_S:
        confirm(pending_key, pending_app, pending_start, pending_end)
    return sorted(switches), sorted(revisits), sorted(visits, key=lambda v: v[0])


def _terminal_events(terminal: list) -> tuple[list, list]:
    """When commands finished failing, and when one failed the same way as a recent one. Ctrl-C isn't a failure."""
    failures, repeats = [], []
    last_error: dict[str, float] = {}
    for e in sorted(terminal, key=lambda x: x["t"] + x["duration"]):
        d, end = e["data"], e["t"] + e["duration"]
        if d.get("exit_code") in (0, 130, None):
            continue
        failures.append(end)
        sig = d.get("error_sig")
        if sig:
            if end - last_error.get(sig, -np.inf) <= ERROR_MEMORY_S:
                repeats.append(end)
            last_error[sig] = end
    return failures, repeats


def _per_min(event_times: list, times: np.ndarray, window_s: float) -> np.ndarray:
    if not event_times:
        return np.zeros(len(times))
    ev = np.asarray(event_times)
    n = np.searchsorted(ev, times, "right") - np.searchsorted(ev, times - window_s, "right")
    return n * 60.0 / window_s


def _rolling_mean(values: np.ndarray, covered: np.ndarray, bins: int) -> np.ndarray:
    """Mean over the last `bins` ticks, counting only ticks that have data."""
    v = np.concatenate(([0.0], np.cumsum(values * covered)))
    c = np.concatenate(([0.0], np.cumsum(covered)))
    i = np.arange(1, len(values) + 1)
    lo = np.maximum(i - bins, 0)
    return np.divide(v[i] - v[lo], c[i] - c[lo], out=np.zeros(len(values)), where=c[i] > c[lo])


def compute_features(trace: Trace, window_s: float = DEFAULT_WINDOW_S, typed_text: str | None = None) -> FeatureSeries:
    window, afk, inp = trace.streams["window"], trace.streams["afk"], trace.streams["input"]
    t0 = np.floor(min(window[0]["t"], inp[0]["t"]) / TICK_S) * TICK_S
    end = max(window[-1]["t"] + window[-1]["duration"], inp[-1]["t"] + inp[-1]["duration"])
    n = int(np.ceil((end - t0) / TICK_S))
    times = t0 + TICK_S * np.arange(1, n + 1)              # a bin's data is available when it closes

    # --- input: each event lands in the bins it covers; counts belong to its last bin
    covered = np.zeros(n, dtype=bool)
    typed, idle = np.zeros(n, dtype=bool), np.ones(n, dtype=bool)
    for e in inp:
        a = int(round((e["t"] - t0) / TICK_S))
        b = max(a + 1, int(round((e["t"] + e["duration"] - t0) / TICK_S)))
        covered[max(a, 0):min(b, n)] = True
        d = e["data"]
        last = min(b, n) - 1
        if d.get("presses", 0) > 0:
            typed[last] = True
        if d.get("presses", 0) or d.get("clicks", 0) or d.get("scrollX", 0) or d.get("scrollY", 0):
            idle[last] = False

    # --- what is in front at each tick
    starts = np.array([e["t"] for e in window])
    ends = np.array([e["t"] + e["duration"] for e in window])
    idx = np.searchsorted(starts, times, "right") - 1
    # For each tick, determine if a window covers it
    # A window covers a tick if the tick time falls within [window_start, window_end + WINDOW_GAP_S]
    has_window = np.zeros(len(times), dtype=bool)
    valid = idx >= 0
    for i in np.flatnonzero(valid):
        tick_idx = np.argmin(np.abs(times - times[i]))  # find closest tick index
        # Check if this tick falls within the window event at idx[i]
        window_start = starts[idx[i]]
        window_end = ends[idx[i]]
        has_window[i] = times[i] >= window_start - WINDOW_GAP_S and times[i] <= window_end + WINDOW_GAP_S
    # Remaining ticks without a matching window event have has_window=False
    # This handles the case where the watcher stopped reporting or switched windows
    cats = np.array([category(e["data"]) for e in window])
    front = np.where(has_window, cats[np.clip(idx, 0, None)], "none")
    on_work = np.isin(front, list(WORK_SURFACES))
    on_ai = np.isin(front, list(AI_TOOLS))

    # --- what the AFK watcher could know by each tick
    away = np.zeros(n, dtype=bool)
    for e in afk:
        if e["data"].get("status") == "afk":
            known_from = e["t"] + AFK_TIMEOUT_S            # its start is backdated to the last input
            lo = np.searchsorted(times, known_from, "left")
            hi = np.searchsorted(times, e["t"] + e["duration"], "right")
            away[lo:hi] = True

    # --- pause only when the person is clearly not at work: quiet staring must still be scored
    pause = np.full(n, SCORING, dtype=np.int8)
    pause[~covered] = PAUSED_NO_INPUT
    pause[~has_window] = PAUSED_NO_WINDOW
    pause[front == "meeting"] = PAUSED_MEETING
    pause[away] = PAUSED_AFK
    pause[front == "lock"] = PAUSED_LOCKED

    bins = max(1, int(round(window_s / TICK_S)))
    switches, revisits, visits = _attention_events(window)
    circling, reaching = _loop_features(visits, times)
    failures, repeats = _terminal_events(trace.streams["terminal"])
    commits = [e["t"] for e in trace.streams["fs"] if e["data"].get("kind") == "commit"]
    fail_window = max(window_s, FAILURE_MIN_WINDOW_S)

    values = dict(
        toggling=np.clip(_per_min(switches, times, window_s) / FULL_SWITCHES_PER_MIN, 0, 1),
        revisits=np.clip(_per_min(revisits, times, window_s) / FULL_REVISITS_PER_MIN, 0, 1),
        stalling=_rolling_mean((idle & on_work).astype(float), covered, bins),
        typing=_rolling_mean(typed.astype(float), covered, bins),
        ai_share=_rolling_mean(on_ai.astype(float), has_window, bins),
        circling=circling,
        reaching=reaching,
        failures=np.clip(_per_min(failures, times, fail_window) / FULL_FAILURES_PER_MIN, 0, 1),
        repeats=np.clip(_per_min(repeats, times, fail_window) / FULL_REPEATS_PER_MIN, 0, 1),
        progress=np.clip(_per_min(commits, times, PROGRESS_WINDOW_S) * PROGRESS_WINDOW_S / 60.0, 0, 1),
    )
    # Only add typed_text to values when it's a non-empty string
    if typed_text:
        values["typed_text"] = typed_text
    return FeatureSeries(times, values, pause, trace.hooks)
