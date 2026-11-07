"""Generate the synthetic dataset: several people, each with several working days, then check it from disk.

Run from the repo root: python scripts/generate_dataset.py
Defaults reproduce the shared dataset (20 people, 10 days each). A day's seed is user * 1000 + day.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # run without installing the package

from synth.generator import generate_day
from synth.writer import write_day


def workdays(start: date, n: int):
    d = start
    while n:
        if d.weekday() < 5:
            yield d
            n -= 1
        d += timedelta(days=1)


def rows(path: Path) -> list:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def generate(out: Path, users: int, days: int, start: date) -> list:
    index = []
    for u in range(1, users + 1):
        for k, day in enumerate(workdays(start, days), 1):
            seed = u * 1000 + k
            path = out / f"user{u:02d}" / f"day{k:02d}"
            write_day(generate_day(seed, day, user=u), path)
            index.append(dict(user=u, day=k, date=day.isoformat(), seed=seed, path=path.relative_to(out).as_posix()))
    (out / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    return index


def check(out: Path, index: list) -> list:
    """Read the written dataset back, print a summary and return any problems found."""
    t = lambda s: datetime.fromisoformat(s).timestamp()
    need = {"window", "afk", "input", "web", "terminal", "fs", "labels", "help_requests"}
    minutes, helps, problems = Counter(), Counter(), []
    styles = {}
    for rec in index:
        d = out / rec["path"]
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        missing = [n for n in need if not (d / f"{n}.jsonl").exists()]
        if missing or not (d / "aw-export.json").exists():
            problems.append((rec["path"], "missing files", missing))
        if m["user"] != rec["user"] or m["seed"] != rec["seed"] or m["date"] != rec["date"]:
            problems.append((rec["path"], "manifest mismatch"))
        styles.setdefault(rec["user"], set()).add(json.dumps(m["style"], sort_keys=True))
        if not rows(d / "window.jsonl") or not rows(d / "input.jsonl"):
            problems.append((rec["path"], "empty core stream"))
        labels = rows(d / "labels.jsonl")
        if abs(sum(x["duration_s"] for x in labels) - m["length_s"]) > 0.1:
            problems.append((rec["path"], "labels do not cover day"))
        for x in labels:
            minutes[x["state"]] += x["duration_s"] / 60
        for h in rows(d / "help_requests.jsonl"):
            helps[h["kind"]] += 1
        for b in json.loads((d / "aw-export.json").read_text(encoding="utf-8"))["buckets"].values():
            e = b["events"]
            if len(e) > 1 and t(e[0]["timestamp"]) < t(e[-1]["timestamp"]):
                problems.append((rec["path"], "export not newest first", b["id"]))
    for u, s in styles.items():
        if len(s) != 1:
            problems.append((f"user{u:02d}", "style changes across days"))
    total = sum(minutes.values())
    print(f"people: {len(styles)}  days: {len(index)}  hours: {total / 60:.1f}")
    print("state share:", {k: round(v / total, 3) for k, v in minutes.items()})
    print("chatbot opens by kind:", helps.most_common())
    print(f"problems: {len(problems)}", problems[:10] or "")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a multi-person synthetic ActivityWatch dataset.")
    ap.add_argument("--users", type=int, default=20)
    ap.add_argument("--days", type=int, default=10, help="working days per person")
    ap.add_argument("--start", type=date.fromisoformat, default=date(2026, 10, 19))
    ap.add_argument("--out", type=Path, default=Path("data/synthetic"))
    args = ap.parse_args()

    index = generate(args.out, args.users, args.days, args.start)
    if check(args.out, index):
        raise SystemExit(1)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
