"""Temperature scaling and the metrics that say whether the probabilities are honest.

    p_cal = sigmoid(logit(p) / T)        T < 1 sharpens, T > 1 pulls toward 0.5, T = 1 changes nothing

T is chosen by minimising the negative log-likelihood on held-out labels, one T per probe.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .confidence import clip

T_MIN, T_MAX = 0.05, 20.0


def logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def calibrate(p: float, T: float = 1.0, eps: float = 1e-3) -> float:
    return 1.0 / (1.0 + math.exp(-logit(clip(p, eps)) / T))


def _nll(z: np.ndarray, y: np.ndarray, T: float) -> float:
    q = np.clip(1.0 / (1.0 + np.exp(-z / T)), 1e-12, 1 - 1e-12)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def fit_temperature(p, y, eps: float = 1e-3) -> float:
    """Golden-section search over log T. Needs both classes present, or there is nothing to fit."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    if len(p) == 0 or y.min() == y.max():
        raise ValueError("need labelled examples of both classes to fit a temperature")
    z = np.log(np.clip(p, eps, 1 - eps) / (1 - np.clip(p, eps, 1 - eps)))
    lo, hi = math.log(T_MIN), math.log(T_MAX)
    g = (math.sqrt(5) - 1) / 2
    a, b = hi - g * (hi - lo), lo + g * (hi - lo)
    fa, fb = _nll(z, y, math.exp(a)), _nll(z, y, math.exp(b))
    for _ in range(60):
        if fa < fb:
            hi, b, fb = b, a, fa
            a = hi - g * (hi - lo)
            fa = _nll(z, y, math.exp(a))
        else:
            lo, a, fa = a, b, fb
            b = lo + g * (hi - lo)
            fb = _nll(z, y, math.exp(b))
    return math.exp((lo + hi) / 2)


def ece(p, y, bins: int = 10) -> float:
    """Expected calibration error: gap between mean confidence and hit rate, weighted by bin size."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    idx = np.minimum((p * bins).astype(int), bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.mean() * abs(y[m].mean() - p[m].mean())
    return float(total)


def brier(p, y) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def auroc(p, y) -> float:
    """Probability a random positive scores above a random negative (ties count half)."""
    p, y = np.asarray(p, float), np.asarray(y, bool)
    pos, neg = p[y], p[~y]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(np.concatenate([pos, neg]), kind="stable")
    ranks = np.empty(len(order))
    vals = np.concatenate([pos, neg])[order]
    i = 0
    while i < len(vals):                       # average ranks over ties
        j = i
        while j + 1 < len(vals) and vals[j + 1] == vals[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def load_temperatures(path: str | Path) -> dict:
    path = Path(path)
    if not path.is_file():
        return {"need": 1.0, "accept": 1.0}
    data = json.loads(path.read_text())
    return {"need": float(data.get("need", 1.0)), "accept": float(data.get("accept", 1.0))}


def save_temperatures(path: str | Path, temps: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(temps, indent=2))
