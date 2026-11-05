"""CLI, run from classifier/.

  python -m classifier score --export ../aw-buckets-export.json [--out scores.jsonl]
  python -m classifier evaluate --split dev
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .evaluate import DEV_USERS, TEST_USERS, load_days, report
from .features import DEFAULT_WINDOW_S, PAUSE_REASONS, SCORING, compute_features
from .loader import load_export
from .scorer import load_weights, profile_for, score_series


def cmd_score(args) -> None:
    series = compute_features(load_export(args.export), args.window)
    profile = profile_for(series, not args.no_hooks)
    weights = load_weights(profile)
    score = score_series(series, weights)
    live = series.pause == SCORING
    print(f"profile: {profile}  ticks: {len(score)}  scored: {int(live.sum())} ({live.sum() * 5 / 3600:.1f} h)")
    for code, reason in PAUSE_REASONS.items():
        print(f"  paused, {reason}: {(series.pause == code).sum() * 5 / 3600:.1f} h")
    if live.any():
        s = score[live]
        print(f"score p10/p50/p90/max: {np.percentile(s, 10):.3f} {np.percentile(s, 50):.3f} "
              f"{np.percentile(s, 90):.3f} {s.max():.3f}")
        print(f"above threshold {weights.threshold:.2f}: {(s >= weights.threshold).mean():.3f} of scored time")
    if args.out:
        with open(args.out, "w") as f:
            for i, t in enumerate(series.times.tolist()):
                paused = series.pause[i] != SCORING
                f.write(json.dumps(dict(
                    timestamp=datetime.fromtimestamp(t, timezone.utc).isoformat(),
                    stuck_score=round(float(score[i]), 4),
                    status="paused" if paused else "scoring",
                    reason=PAUSE_REASONS.get(int(series.pause[i])))) + "\n")
        print(f"wrote {args.out}")


def cmd_evaluate(args) -> None:
    users = {"dev": DEV_USERS, "test": TEST_USERS}[args.split]
    days, profile = load_days(Path(args.data), users, not args.no_hooks, args.window)
    report(days, profile, args.threshold)


def main() -> None:
    ap = argparse.ArgumentParser(description="Score activity for how stuck someone looks, from 0 to 1.")
    sub = ap.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--window", type=float, default=DEFAULT_WINDOW_S, help="lookback in seconds")
    common.add_argument("--no-hooks", action="store_true", help="ignore terminal and file streams, as with stock ActivityWatch")

    s = sub.add_parser("score", parents=[common], help="score an ActivityWatch export")
    s.add_argument("--export", required=True)
    s.add_argument("--out", help="write one JSON line per tick")
    s.set_defaults(func=cmd_score)

    e = sub.add_parser("evaluate", parents=[common], help="check scores against the synthetic labels")
    e.add_argument("--data", default="../data/synthetic")
    e.add_argument("--split", choices=["dev", "test"], default="dev")
    e.add_argument("--threshold", type=float, default=None)
    e.set_defaults(func=cmd_evaluate)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
