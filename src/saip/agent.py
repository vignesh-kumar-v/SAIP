"""The live loop: read ActivityWatcher, score, and decide when to speak.

Each tick re-reads the last `history_min` minutes, so the classifier code is reused unchanged: the EMA
settles within about a minute, far inside that history. The Orchestrator is a small pure state machine
(cooldown, deferred offers, re-check at natural breaks) so it can be tested without any clock or network.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from classifier.features import PAUSE_REASONS, SCORING, WINDOW_GAP_S, compute_features
from classifier.loader import REQUIRED, STREAMS, Trace, _epoch
from classifier.scorer import load_weights, profile_for, score_series
from llm.assess import Assessment
from llm.config import AgentConfig
from llm.gate import QUIET, SPEAK, WAIT, should_probe

from .aw import ActivityWatch

STALE_AFTER_S = 60.0         # if the newest tick is older than this, the machine is not reporting
# How the quiet period after an offer stretches with the answer, as in thunlp/ProactiveAgent: ignoring means the user
# is busy, dismissing means no, accepting means the offer was welcome. Capped so it can never go silent for good.
COOLDOWN_FACTOR = {"ignored": 1.5, "dismissed": 2.0}
MAX_COOLDOWN_MULT = 4.0


class NotReady(RuntimeError):
    pass


def fetch_trace(aw: ActivityWatch, now: float, history_s: float) -> Trace:
    streams: dict[str, list] = {name: [] for name in STREAMS.values()}
    hooks = False
    for bid, bucket in aw.buckets().items():
        name = STREAMS.get(bucket.get("type"))
        if name is None:
            continue
        hooks = hooks or name == "terminal"
        streams[name].extend(dict(t=_epoch(e["timestamp"]), duration=float(e["duration"]), data=e["data"])
                             for e in aw.events(bid, now - history_s, now))
    for rows in streams.values():
        rows.sort(key=lambda e: (e["t"], e["duration"]))
    missing = [n for n in REQUIRED if not streams[n]]
    if missing:
        raise NotReady("no recent " + ", ".join(missing) + " events from ActivityWatcher")
    return Trace(streams, hooks)


@dataclass
class Tick:
    time: float
    stuck_score: float
    paused: bool
    reason: str | None
    theta_trig: float
    idle_s: float | None         # seconds since the last key, click or scroll
    front: tuple | None          # identity of the window in front


def score_now(trace: Trace, now: float) -> Tick:
    """Score the newest tick for which every stream has reported.

    Watchers report a few seconds apart, so the very last tick can have input but no window yet, which would
    read as "no window" and pause scoring. Using the newest tick covered by the slowest stream avoids that.
    """
    series = compute_features(trace)
    weights = load_weights(profile_for(series))
    score = score_series(series, weights)
    sealed = min(max(e["t"] + e["duration"] for e in trace.streams["window"]),
                 max(e["t"] + e["duration"] for e in trace.streams["input"])) + WINDOW_GAP_S
    i = max(0, int(np.searchsorted(series.times, sealed, "right")) - 1)
    t = float(series.times[i])
    pause = int(series.pause[i])
    stale = now - t > STALE_AFTER_S
    inp = [e for e in trace.streams["input"]
           if any(e["data"].get(k, 0) for k in ("presses", "clicks", "scrollX", "scrollY"))]
    idle = now - max(e["t"] + e["duration"] for e in inp) if inp else None
    win = trace.streams["window"][-1]["data"]
    return Tick(t, float(score[i]), stale or pause != SCORING, "stale" if stale else PAUSE_REASONS.get(pause),
                weights.threshold, idle, (win.get("app"), win.get("title")))


@dataclass
class Event:
    kind: str                    # "offer", "deferred", "recheck-quiet", "quiet"
    assessment: Assessment | None = None


@dataclass
class Orchestrator:
    cfg: AgentConfig
    last_assess: float = -1e18
    last_offer: float = -1e18
    pending_since: float | None = None
    last_front: tuple | None = None
    cooldown_mult: float = 1.0                    # grows after ignored or dismissed offers, resets on an accepted one
    last_outcome: str | None = None               # the answer to the previous offer, until the next one uses it

    def record_outcome(self, outcome: str) -> None:
        """Learn from what the user did with the last offer."""
        if outcome == "accepted":
            self.cooldown_mult = 1.0
        elif outcome in COOLDOWN_FACTOR:
            self.cooldown_mult = min(self.cooldown_mult * COOLDOWN_FACTOR[outcome], MAX_COOLDOWN_MULT)
        else:
            return                                # "unanswered" (no dialog available) tells us nothing
        self.last_outcome = outcome

    def take_outcome(self) -> str | None:
        """The previous offer's outcome, handed to the proposal prompt once and then forgotten."""
        out, self.last_outcome = self.last_outcome, None
        return out

    def is_break(self, tick: Tick, previous_front: tuple | None) -> bool:
        """A natural break: typing stopped for a few seconds, or the window in front changed."""
        quiet_hands = tick.idle_s is not None and tick.idle_s >= self.cfg.break_idle_s
        return quiet_hands or (previous_front is not None and tick.front != previous_front)

    def step(self, now: float, tick: Tick, assess: Callable[[], Assessment]) -> Event | None:
        switched_from = self.last_front
        self.last_front = tick.front
        if tick.paused:
            return None
        if now - self.last_offer < self.cfg.cooldown_s * self.cooldown_mult:
            return None
        if self.pending_since is not None and now - self.pending_since > self.cfg.pending_expiry_s:
            self.pending_since = None
        recheck = self.pending_since is not None and self.is_break(tick, switched_from)
        due = now - self.last_assess >= self.cfg.assess_interval_s
        if not (recheck or (due and should_probe(tick.stuck_score, False, tick.theta_trig))):
            return None
        self.last_assess = now
        a = assess()
        if a.decision.action == SPEAK:
            self.last_offer, self.pending_since = now, None
            return Event("offer", a)
        if a.decision.action == WAIT:
            if self.pending_since is None:
                self.pending_since = now
            return Event("deferred", a)
        return Event("recheck-quiet" if recheck else "quiet", a)


def describe(now: float, tick: Tick, ev: Event) -> str:
    a = ev.assessment
    d = a.decision
    flag = " (re-checked near the bar)" if a.resampled else ""
    return (f"{time.strftime('%H:%M:%S', time.localtime(now))}  s={tick.stuck_score:.2f}  p_need={d.p_need:.2f}  "
            f"p_accept={d.p_accept:.2f}  bar={d.tau:.2f}  -> {ev.kind.upper()}{flag}")
