"""Run SAIP's two yes/no questions and the gate on ProactiveBench: real recorded user sessions we did not make.

    git clone https://github.com/thunlp/ProactiveAgent                      # once (Apache-2.0)
    python -m llm.proactivebench run --pa PATH/TO/ProactiveAgent --out pb_rows.jsonl     # needs the model
    python -m llm.proactivebench report pb_rows.jsonl                                    # no model needed

ProactiveBench is the benchmark PRISM (Fu et al., ICLR 2026) reports on. Two label sets from it are used:

* "sessions": dataset/test_data, 12 recorded sessions (6 coding, 6 writing), 233 events in order. Each event is
  scored against its `task_status` field. ProactiveBench does not document that field; it is true at 87 events,
  almost always where candidate tasks exist, and reads as "a proposal here was acceptable". Treat it with care.
* "reward": dataset/reward_data/test_data.jsonl, 120 moments, most of them from the same sessions, each with its
  own event history and an explicit `help_needed` label from three annotators. Sampled to 30 per category, so
  its base rate is not natural: compare against always-speak on the same set, not across sets.

The model sees the event sentences up to and including the moment being judged, and is asked SAIP's two
questions (llm/prompts.py). The sentences carry no stuck score, so SAIP's state-aware costs cannot apply: the gate
here is PRISM's fixed-cost gate (alpha = beta = 0). So this run tests SAIP's model, prompts and calibration on
data from real people, not SAIP's state signal.

Metrics follow ProactiveBench's definitions (eval/calculate_agent_performance.py), with the label in place of the
reward model it uses (the reward model is not released): False-Alarm is FP / (TP + FP). Calibration is fitted
leave-one-group-out (a session, or a fold of the reward set), so no moment is scored with temperatures fitted on
it. The run appends each answer to the output file as it goes and skips moments already there, so a dropped
connection loses nothing: run the same command again to continue.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from . import prompts
from .calibration import auroc, calibrate, ece, fit_temperature
from .client import LLMError, OpenAIChatClient
from .compare import EPS, RATIOS, metrics
from .confidence import probe
from .config import GateParams, load_settings
from .gate import SPEAK, decide

MAX_EVENTS = 15                  # the same cap as SAIP's own timeline (llm/context.py)
REWARD_FOLDS = 6
PRISM_QWEN3_8B_F1 = 73.34        # PRISM's Table 1, Qwen3-8B not fine-tuned, judge labels: a reference, not a target


# ---------------------------------------------------------------------------------------------------- data
def _ago(seconds: float) -> str:
    s = int(round(max(0.0, seconds)))
    return f"{s // 60}m {s % 60:02d}s ago" if s >= 60 else f"{s}s ago"


def _epoch(t) -> float | None:
    try:
        return float(t)
    except (TypeError, ValueError):
        return None                                  # generated scenes use times like "Day 1, 10:05 PM"


def build_context(events: list[dict]) -> str:
    """The event sentences so far, oldest first, timed relative to the last one (the moment being judged)."""
    shown = events[-MAX_EVENTS:]
    times = [_epoch(e.get("time")) for e in shown]
    timed = all(t is not None for t in times)
    lines = [f"Recent activity (oldest first, {len(shown)} of {len(events)} events):"]
    for e, t in zip(shown, times):
        when = f"{_ago(times[-1] - t)}: " if timed else ""
        lines.append(f"- {when}{str(e['event']).strip()}")
    return "\n".join(lines)


def load_sessions(pa: Path) -> list[dict]:
    """One item per event of each recorded session, with the history up to and including it."""
    folder = pa / "dataset" / "test_data"
    files = sorted(f for f in glob.glob(str(folder / "*.json")) if not f.endswith("splits.json"))
    if not files:
        raise FileNotFoundError(f"no session files in {folder}: is --pa the ProactiveAgent folder?")
    items = []
    for f in files:
        name = Path(f).stem
        events = json.loads(Path(f).read_text(encoding="utf-8"))
        history = []
        for i, e in enumerate(events):
            history.append(e["observation"])
            items.append({"set": "sessions", "id": f"{name}#{i}", "group": name, "origin": "recorded",
                          "category": "coding" if name.startswith("code") else "writing",
                          "label": bool(e["task_status"]), "context": build_context(history)})
    return items


def load_reward(pa: Path) -> list[dict]:
    path = pa / "dataset" / "reward_data" / "test_data.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found: is --pa the ProactiveAgent folder?")
    recorded = set()
    for f in glob.glob(str(pa / "dataset" / "test_data" / "*.json")):
        if not f.endswith("splits.json"):
            recorded |= {str(e["observation"]["event"]) for e in json.loads(Path(f).read_text(encoding="utf-8"))}
    items = []
    for i, line in enumerate(x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()):
        r = json.loads(line)
        origin = "recorded" if str(r["obs"][-1]["event"]) in recorded else "generated"
        items.append({"set": "reward", "id": f"reward#{i}", "group": f"fold{i % REWARD_FOLDS}", "origin": origin,
                      "category": r["category"], "label": bool(r["help_needed"]), "context": build_context(r["obs"])})
    return items


def load_items(pa: Path, which: str) -> list[dict]:
    return (load_sessions(pa) if which in ("sessions", "both") else []) + \
           (load_reward(pa) if which in ("reward", "both") else [])


# ---------------------------------------------------------------------------------------------------- run
def done_ids(out: Path) -> set:
    if not out.is_file():
        return set()
    return {json.loads(x)["id"] for x in out.read_text(encoding="utf-8").splitlines() if x.strip()}


def run(client, items: list[dict], out: Path, progress=print) -> int:
    """Ask both questions for every item not yet in `out`, appending one row per item. Returns how many were new."""
    skip = done_ids(out)
    todo = [it for it in items if it["id"] not in skip]
    if skip:
        progress(f"{len(skip)} already done in {out}, {len(todo)} to go")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        for n, it in enumerate(todo, 1):
            p_need = probe(client, prompts.need_messages(it["context"]))
            p_accept = probe(client, prompts.accept_messages(it["context"]))
            row = {k: it[k] for k in ("set", "id", "group", "origin", "category", "label")}
            row.update(p_need_raw=p_need, p_accept_raw=p_accept)
            f.write(json.dumps(row) + "\n")
            f.flush()
            if n % 25 == 0 or n == len(todo):
                progress(f"  {n} of {len(todo)}")
    return len(todo)


# ---------------------------------------------------------------------------------------------------- report
def cross_fitted(rows: list[dict]) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """Calibrated p_need and p_accept, each group scored with temperatures fitted on all the other groups."""
    groups = sorted({r["group"] for r in rows})
    need, accept, temps = np.zeros(len(rows)), np.zeros(len(rows)), []
    for g in groups:
        train = [r for r in rows if r["group"] != g]
        y = [r["label"] for r in train]
        try:
            t = {"need": fit_temperature([r["p_need_raw"] for r in train], y),
                 "accept": fit_temperature([r["p_accept_raw"] for r in train], y)}
        except ValueError:                           # the other groups hold only one kind of label: leave as is
            t = {"need": 1.0, "accept": 1.0}
        temps.append(t)
        for i, r in enumerate(rows):
            if r["group"] == g:
                need[i], accept[i] = calibrate(r["p_need_raw"], t["need"]), calibrate(r["p_accept_raw"], t["accept"])
    return need, accept, temps


def gate(need: np.ndarray, accept: np.ndarray, g: GateParams) -> np.ndarray:
    """PRISM's fixed-cost gate. The stuck score is irrelevant once alpha = beta = 0."""
    fixed = replace(g, alpha=0.0, beta=0.0)
    return np.array([decide(0.5, need[i], accept[i], fixed).action == SPEAK for i in range(len(need))], dtype=bool)


def _m(spoke: np.ndarray, y: np.ndarray, idx=None) -> dict:
    return metrics(spoke, SimpleNamespace(stuck=y, flow=np.zeros(len(y), dtype=bool)), idx)


def bootstrap_f1(spoke: np.ndarray, y: np.ndarray, groups: np.ndarray, n: int = 2000, seed: int = 0) -> tuple:
    """95% interval for F1, resampling whole groups (sessions), since moments in one session are related."""
    names = np.unique(groups)
    members = {g: np.flatnonzero(groups == g) for g in names}
    rng = np.random.default_rng(seed)
    vals = [_m(spoke, y, np.concatenate([members[g] for g in rng.choice(names, len(names))]))["f1"] for _ in range(n)]
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def _table(rows: list[tuple[str, dict]]) -> list[str]:
    out = [f"  {'system':36s}{'speak':>6s}{'recall':>8s}{'precis.':>9s}{'accur.':>8s}{'false-alarm':>13s}{'F1':>7s}"]
    for name, m in rows:
        out.append(f"  {name[:36]:36s}{m['speak']:>6d}{m['recall']:>8.1%}{m['precision']:>9.1%}{m['accuracy']:>8.1%}"
                   f"{m['false_alarm']:>13.1%}{m['f1']:>7.1%}")
    return out


def report_set(rows: list[dict], g: GateParams, n_boot: int) -> list[str]:
    y = np.array([r["label"] for r in rows], dtype=bool)
    pn = np.array([r["p_need_raw"] for r in rows])
    pa = np.array([r["p_accept_raw"] for r in rows])
    groups = np.array([r["group"] for r in rows])
    need, accept, temps = cross_fitted(rows)
    tn = [t["need"] for t in temps]
    ta = [t["accept"] for t in temps]
    lines = [f"moments: {len(rows)}   labelled positive: {y.sum()} ({y.mean():.1%})   groups: {len(np.unique(groups))}",
             "",
             "ranking (AUROC against the label; 0.5 = chance):",
             f"  p_need     {auroc(pn, y):.3f}",
             f"  p_accept   {auroc(pa, y):.3f}",
             "",
             f"calibration, fitted leave-one-group-out: T_need {min(tn):.2f} to {max(tn):.2f}, "
             f"T_accept {min(ta):.2f} to {max(ta):.2f}",
             f"  p_need     ECE raw {ece(pn, y):.3f}   calibrated {ece(need, y):.3f}",
             f"  p_accept   ECE raw {ece(pa, y):.3f}   calibrated {ece(accept, y):.3f}",
             "",
             f"1. Decisions at the configured base costs (C_FA : C_FN = {g.c0:g} : {g.m0:g})"]
    systems = {
        "PRISM gate, calibrated": gate(need, accept, g),
        "PRISM gate, raw probabilities": gate(pn, pa, g),
        "fixed cut p_accept >= 0.5, calibrated": accept >= 0.5,
        "always speak": np.ones(len(rows), dtype=bool),
    }
    lines += _table([(k, _m(v, y)) for k, v in systems.items()])
    if n_boot:
        lo, hi = bootstrap_f1(systems["PRISM gate, calibrated"], y, groups, n_boot)
        lines.append(f"   F1 of the calibrated gate, 95% interval over resampled groups: {lo:.1%} to {hi:.1%}")
    lines += ["", "2. Cost-ratio sweep, calibrated (the ratios in PRISM's Table 11)"]
    lines += _table([(f"C_FA : C_FN = {c0:g} : {m0:g}", _m(gate(need, accept, replace(g, c0=c0, m0=m0)), y))
                     for c0, m0 in RATIOS])
    spoke = systems["PRISM gate, calibrated"]
    for n, key, title in ((3, "category", "By category"), (4, "origin", "By origin (recorded sessions or generated scenes)")):
        vals = sorted({str(r.get(key, "recorded")) for r in rows})
        if len(vals) > 1:
            lines += ["", f"{n}. {title}, calibrated gate at the configured costs"]
            for v in vals:
                idx = np.flatnonzero([str(r.get(key, "recorded")) == v for r in rows])
                lines += _table([(f"{v} (positive {y[idx].mean():.0%})", _m(spoke, y, idx)),
                                 (f"  always speak", _m(np.ones(len(rows), dtype=bool), y, idx))])[(0 if v == vals[0] else 1):]
    return lines


def report(rows: list[dict], g: GateParams, n_boot: int = 2000) -> str:
    if not rows:
        return "No rows: run `python -m llm.proactivebench run --pa PATH --out FILE` first."
    lines = []
    for name, title in (("sessions", "Recorded sessions (dataset/test_data), label = task_status"),
                        ("reward", "Reward-model test set, label = help_needed (three annotators)")):
        part = [r for r in rows if r["set"] == name]
        if part:
            lines += ["", "=" * 96, title, "=" * 96] + report_set(part, g, n_boot)
    lines += ["",
              "The gate is PRISM's fixed-cost gate: event sentences carry no stuck score, so SAIP's state-aware costs",
              "cannot apply. False-Alarm = FP / (TP + FP), ProactiveBench's definition. Labels are ProactiveBench's",
              "own, not the LLM-judge votes PRISM scored against (unreleased), so PRISM's published numbers (e.g. F1",
              f"{PRISM_QWEN3_8B_F1} for Qwen3-8B, not fine-tuned) are a loose reference. Compare with always-speak on",
              "the same set first: the reward set is balanced by design, so its rates are not natural ones."]
    return "\n".join(lines).lstrip("\n")


def load_rows(path) -> list[dict]:
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


# ---------------------------------------------------------------------------------------------------- command
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m llm.proactivebench", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="ask the model about every ProactiveBench moment (resumes where it stopped)")
    r.add_argument("--pa", required=True, help="the ProactiveAgent folder (git clone https://github.com/thunlp/ProactiveAgent)")
    r.add_argument("--set", choices=["sessions", "reward", "both"], default="both")
    r.add_argument("--out", default="pb_rows.jsonl")
    r.add_argument("--mock", action="store_true", help="a stand-in model: checks the plumbing, says nothing")
    r.add_argument("--config", help="path to private.toml")
    p = sub.add_parser("report", help="score stored rows (no model needed)")
    p.add_argument("rows")
    p.add_argument("--config", help="path to private.toml (for the gate's costs)")
    p.add_argument("--bootstrap", type=int, default=2000, help="resamples for the F1 interval (0 to skip)")
    a = ap.parse_args(argv)
    if a.cmd == "report":
        try:
            rows = load_rows(a.rows)
        except OSError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(report(rows, load_settings(a.config).gate, a.bootstrap))
        return 0
    try:
        items = load_items(Path(a.pa), a.set)
    except (OSError, KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    settings = load_settings(a.config)
    if a.mock:
        from .replay import MockClient
        client, note = MockClient(), "MOCK backend: pipeline check only, says nothing about a real model"
    else:
        try:
            client, note = OpenAIChatClient(settings.llm), f"endpoint {settings.llm.base_url} model {settings.llm.model}"
        except ValueError as e:
            print(f"error: {e}\n(use --mock to run the pipeline without a model)", file=sys.stderr)
            return 2
    print(f"{note}\n{len(items)} moments, 2 model calls each")
    try:
        run(client, items, Path(a.out))
    except LLMError as e:
        print(f"error: {e}\nThe answers so far are saved; run the same command again to continue.", file=sys.stderr)
        return 1
    print()
    print(report(load_rows(a.out), settings.gate, 2000))
    return 0


if __name__ == "__main__":
    sys.exit(main())
