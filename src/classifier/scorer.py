"""The classifier: a hand-weighted logistic score, smoothed with an EMA and paused while the person is away.

    raw   = sigmoid(bias + sum(weight * feature))
    score = alpha * raw + (1 - alpha) * previous score

Weights live in weights.json, with one profile per set of available streams. Real ActivityWatch
data has no terminal stream, so that profile needs weights of its own rather than a zero in
place of the missing evidence.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .features import PAUSE_REASONS, SCORING, TICK_S, FeatureSeries

WEIGHTS_FILE = Path(__file__).with_name("weights.json")
ALPHA = 0.1                  # at one tick per 5 s, the score moves halfway to a steady reading in about 33 s
RESET_AFTER_S = 1800.0       # a coffee break shouldn't wipe the score; a long absence should


@dataclass(frozen=True)
class Weights:
    bias: float
    weights: dict
    threshold: float         # the baseline that would trigger the LLM check in Phase 4


def load_weights(profile: str) -> Weights:
    p = json.loads(WEIGHTS_FILE.read_text())[profile]
    return Weights(p["bias"], p["weights"], p["threshold"])


def profile_for(series: FeatureSeries, use_hooks: bool = True) -> str:
    return "with_hooks" if series.hooks and use_hooks else "window_input_afk"


def raw_score(w: Weights, features: dict) -> float:
    z = w.bias + sum(k * features[name] for name, k in w.weights.items())
    return 1.0 / (1.0 + math.exp(-z))


class StuckScorer:
    """Stateful scorer, fed one tick at a time. A live system would use this directly."""

    def __init__(self, weights: Weights):
        self.w = weights
        self.score = 1.0 / (1.0 + math.exp(-weights.bias))
        self.paused_s = 0.0

    def update(self, features: dict, pause: int = SCORING) -> dict:
        if pause != SCORING:
            self.paused_s += TICK_S
            if self.paused_s >= RESET_AFTER_S:
                self.score = 1.0 / (1.0 + math.exp(-self.w.bias))
            return dict(stuck_score=self.score, status="paused", reason=PAUSE_REASONS[pause])
        self.paused_s = 0.0
        self.score = ALPHA * raw_score(self.w, features) + (1 - ALPHA) * self.score
        return dict(stuck_score=self.score, status="scoring", reason=None)


def score_series(series: FeatureSeries, weights: Weights) -> np.ndarray:
    """Replay a whole trace through StuckScorer's rule, returning the score at every tick."""
    z = weights.bias + sum(k * series.values[name] for name, k in weights.weights.items())
    raw = 1.0 / (1.0 + np.exp(-z))
    start = 1.0 / (1.0 + math.exp(-weights.bias))
    out = np.empty(len(raw))
    score, paused_s = start, 0.0
    for i, (r, p) in enumerate(zip(raw.tolist(), series.pause.tolist())):
        if p != SCORING:
            paused_s += TICK_S
            if paused_s >= RESET_AFTER_S:
                score = start
        else:
            paused_s = 0.0
            score = ALPHA * r + (1 - ALPHA) * score
        out[i] = score
    return out
