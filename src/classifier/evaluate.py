"""Score the synthetic dataset against its labels. Labels are read here only, never by the classifier.

People are split, not days: weights and thresholds may be tuned on the dev people, and the test
people stay unseen until a final check. The stuck label comes from our own generator, so these
numbers show whether the pipeline works. They are not evidence of real-world accuracy.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from .features import FEATURES, SCORING, TICK_S, FeatureSeries, compute_features
from .loader import load_export
from .scorer import Weights, load_weights, profile_for, score_series

DEV_USERS = range(1, 15)
TEST_USERS = range(15, 21)
FALSE_ALARM_GRACE_S = 120.0  # a score still high just after an episode ended isn't a new false alarm


@dataclass
class Day:
    user: int
    series: FeatureSeries
    score: np.ndarray
    state: np.ndarray            # label at each tick
    subtype: np.ndarray
    ai_loop: np.ndarray          # tick is inside a stuck episode spent going round in circles with an agent
    episodes: list               # (start, end) of each stuck episode


def _epoch(ts: str) -> float:
    return datetime.fromisoformat(ts).timestamp()


def load_day(day_dir: Path, user: int, use_hooks: bool, window_s: float) -> tuple[Day, str]:
    series = compute_features(load_export(day_dir / "aw-export.json"), window_s)
    profile = profile_for(series, use_hooks)
    labels = [json.loads(x) for x in (day_dir / "labels.jsonl").read_text().splitlines()]
    n = len(series.times)
    state, subtype = np.full(n, "none", dtype=object), np.full(n, "none", dtype=object)
    ai_loop = np.zeros(n, dtype=bool)
    episodes = []
    for lab in labels:
        a, b = _epoch(lab["start"]), _epoch(lab["end"])
        i, j = np.searchsorted(series.times, a, "left"), np.searchsorted(series.times, b, "left")
        state[i:j], subtype[i:j] = lab["state"], lab["subtype"]
        if lab["state"] == "stuck":
            ai_loop[i:j] = bool(lab["meta"].get("ai_loop"))
            if episodes and episodes[-1][1] == a:             # back-to-back stuck segments are one episode
                episodes[-1] = (episodes[-1][0], b)
            else:
                episodes.append((a, b))
    return Day(user, series, score_series(series, load_weights(profile)), state, subtype, ai_loop, episodes), profile


def load_days(data: Path, users, use_hooks: bool, window_s: float) -> tuple[list, str]:
    days, profile = [], ""
    for rec in json.loads((data / "index.json").read_text()):
        if rec["user"] in users:
            day, profile = load_day(data / rec["path"], rec["user"], use_hooks, window_s)
            days.append(day)
    if not days:
        raise ValueError(f"no days for users {list(users)} in {data}")
    return days, profile


def average_precision(score: np.ndarray, truth: np.ndarray) -> float:
    order = np.argsort(-score, kind="stable")
    hits = truth[order]
    if not hits.any():
        return float("nan")
    precision = np.cumsum(hits) / np.arange(1, len(hits) + 1)
    return float(precision[hits].mean())


def tick_metrics(score: np.ndarray, truth: np.ndarray, threshold: float) -> dict:
    flag = score >= threshold
    tp = int((flag & truth).sum())
    precision = tp / flag.sum() if flag.any() else 0.0
    recall = tp / truth.sum() if truth.any() else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return dict(precision=precision, recall=recall, f1=f1, flagged=float(flag.mean()))


def episode_metrics(days: list, threshold: float) -> dict:
    """How it behaves as an interruption trigger: was each episode caught, how late, and at what false-alarm cost."""
    total = caught = 0
    delays, false_alarms = [], 0
    scoring_s = 0.0
    calm_ticks = calm_flagged = 0
    for d in days:
        live = d.series.pause == SCORING
        times, flag = d.series.times, (d.score >= threshold) & live
        scoring_s += live.sum() * TICK_S
        calm = live & (d.state != "stuck")
        calm_ticks += int(calm.sum())
        calm_flagged += int((flag & calm).sum())
        for a, b in d.episodes:
            total += 1
            hit = np.flatnonzero(flag & (times >= a) & (times < b))
            if len(hit):
                caught += 1
                delays.append(times[hit[0]] - a)
        rising = np.flatnonzero(flag & ~np.concatenate(([False], flag[:-1])))
        for i in rising:
            if not any(a <= times[i] < b + FALSE_ALARM_GRACE_S for a, b in d.episodes):
                false_alarms += 1
    return dict(episodes=total, caught=caught, recall=caught / total if total else 0.0,
                median_delay_s=float(np.median(delays)) if delays else float("nan"),
                false_alarms_per_hour=false_alarms / max(scoring_s / 3600, 1e-9),
                # an alarm that never turns off makes few false alarms, so this has to be read alongside them
                calm_time_flagged=calm_flagged / max(calm_ticks, 1))


def report(days: list, profile: str, threshold: float | None = None) -> dict:
    live = np.concatenate([d.series.pause == SCORING for d in days])
    score = np.concatenate([d.score for d in days])[live]
    state = np.concatenate([d.state for d in days])[live]
    subtype = np.concatenate([d.subtype for d in days])[live]
    ai_loop = np.concatenate([d.ai_loop for d in days])[live]
    truth = state == "stuck"
    w: Weights = load_weights(profile)
    threshold = w.threshold if threshold is None else threshold

    print(f"profile: {profile}  days: {len(days)}  people: {len({d.user for d in days})}  threshold: {threshold:.3f}")
    total = sum(len(d.score) for d in days)
    print(f"scored ticks: {len(score)} of {total} ({100 * len(score) / total:.1f}%); stuck share of scored: {truth.mean():.3f}")
    print(f"average precision: {average_precision(score, truth):.3f}  (a useless score gets {truth.mean():.3f})")

    m = tick_metrics(score, truth, threshold)
    print(f"ticks:    precision {m['precision']:.3f}  recall {m['recall']:.3f}  f1 {m['f1']:.3f}  flagged {m['flagged']:.3f}")
    e = episode_metrics(days, threshold)
    print(f"episodes: caught {e['caught']}/{e['episodes']} ({e['recall']:.3f})  median delay {e['median_delay_s']:.0f} s"
          f"  false alarms/hour {e['false_alarms_per_hour']:.2f}  non-stuck time flagged {e['calm_time_flagged']:.3f}")

    print("\nthreshold sweep (ticks | episodes):")
    for th in np.round(np.arange(0.10, 0.71, 0.05), 2):
        x, y = tick_metrics(score, truth, th), episode_metrics(days, th)
        print(f"  {th:.2f}  p {x['precision']:.3f} r {x['recall']:.3f} f1 {x['f1']:.3f} | "
              f"caught {y['recall']:.3f} delay {y['median_delay_s']:5.0f}s false/h {y['false_alarms_per_hour']:.2f} "
              f"calm flagged {y['calm_time_flagged']:.3f}")

    print("\nscore by label:")
    groups = {"stuck": truth, "  stuck, looping with an agent": truth & ai_loop, "flow": state == "flow",
              "  flow, working with AI": subtype == "ai_collab", "  flow, reading/review": subtype == "review",
              "neutral": state == "neutral", "  neutral, waiting": subtype == "waiting",
              "distracted, not yet AFK": state == "distracted"}
    for name, mask in groups.items():
        if mask.any():
            print(f"  {name:32s} mean {score[mask].mean():.3f}  flagged {(score[mask] >= threshold).mean():.3f}  ticks {mask.sum()}")

    print("\nfeature means by state (scored ticks):")
    states = ("stuck", "flow", "neutral", "distracted")
    print(f"  {'feature':10s}" + "".join(f"{s:>12s}" for s in states))
    for f in FEATURES:
        v = np.concatenate([d.series.values[f] for d in days])[live]
        print(f"  {f:10s}" + "".join(f"{v[state == s].mean():12.3f}" for s in states))

    per_user = defaultdict(list)
    for d in days:
        per_user[d.user].append(d)
    print("\nper person (ticks p/r, episodes caught, false alarms/hour):")
    for u, group in sorted(per_user.items()):
        lv = np.concatenate([d.series.pause == SCORING for d in group])
        s, st = np.concatenate([d.score for d in group])[lv], np.concatenate([d.state for d in group])[lv]
        x, y = tick_metrics(s, st == "stuck", threshold), episode_metrics(group, threshold)
        print(f"  user{u:02d}  p {x['precision']:.2f} r {x['recall']:.2f}  caught {y['caught']}/{y['episodes']}"
              f"  false/h {y['false_alarms_per_hour']:.2f}")
    return dict(ticks=m, episodes=e)
