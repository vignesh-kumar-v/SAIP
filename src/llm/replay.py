"""Replay synthetic days through the full Phase 3 pipeline and report how it behaves.

For every sampled tick where the cheap classifier would wake the LLM (score above its trigger, not
paused), build the context, run both probes and the gate, and compare with the generator's labels.
Labels are read only here, never by the pipeline. They come from our own generator, so these numbers
test the machinery and are not evidence about real users.

`--mock` swaps the endpoint for a deterministic stand-in that reads the same summary text. It checks
that the pipeline runs end to end and says nothing about any real model.
"""
from __future__ import annotations

import json
import math
import re
import zlib
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from classifier.evaluate import DEV_USERS, TEST_USERS, load_day
from classifier.features import SCORING
from classifier.loader import load_export
from classifier.scorer import load_weights

from .assess import Assessment, assess
from .calibration import auroc, brier, calibrate, ece, fit_temperature, load_temperatures, save_temperatures
from .client import Choice, ChatClient
from .config import GateParams, Settings
from .context import build_context
from .gate import QUIET, SPEAK, decide


class MockClient:
    """Reads the summary text and answers from it. Pipeline check only."""

    def complete(self, messages, *, max_tokens=1, temperature=0.0, logprobs=False, top_logprobs=10, n=1):
        text = messages[-1]["content"]
        returns = int((re.search(r"\((\d+) returns", text) or [0, 0])[1])
        repeat = 1.0 if "same failure occurred" in text else 0.0
        z = -2.0 + 0.6 * returns + 2.0 * repeat
        if "accept" in messages[-1]["content"].split("\n")[-1]:
            z -= 0.5
        p = 1.0 / (1.0 + math.exp(-z))
        if n > 1:
            rng = np.random.default_rng(zlib.crc32(text.encode()))
            return [Choice("Yes" if rng.random() < p else "No") for _ in range(n)]
        return [Choice("Yes" if p >= 0.5 else "No",
                       {"Yes": math.log(p), "No": math.log(1 - p)} if logprobs else None)]

    def chat(self, messages, *, tools=None, max_tokens=420, temperature=0.4):
        return {"content": "This is the mock model: it cannot use tools or read your work.", "tool_calls": [],
                "finish_reason": "stop"}


@dataclass
class Row:
    user: int
    t: float
    state: str
    stuck_score: float
    p_need_raw: float
    p_accept_raw: float
    action: str
    tau: float
    resampled: bool
    calls: int
    theta: float = 0.0           # the classifier's trigger for this day's profile (what `llm.compare` gates on)


def sample_ticks(data: Path, users, use_hooks: bool, stride_s: float, max_assess: int, all_ticks: bool = False):
    """Yield (day, trace, tick index, trigger threshold) for ticks the classifier would hand to the LLM.

    With all_ticks, every scored tick on the stride is yielded, triggered or not. That is what a comparison with
    PRISM needs: PRISM has no classifier in front of it and judges every event (see docs/PRISM_Comparison.md).
    """
    index = json.loads((data / "index.json").read_text(encoding="utf-8"))
    picks = []
    for rec in index:
        if rec["user"] not in users:
            continue
        day, _ = load_day(data / rec["path"], rec["user"], use_hooks, 60.0)
        theta = load_weights("with_hooks" if day.series.hooks and use_hooks else "window_input_afk").threshold
        step = max(1, int(stride_s / 5.0))
        idx = [i for i in range(0, len(day.score), step)
               if day.series.pause[i] == SCORING and (all_ticks or day.score[i] >= theta)]
        picks.append((rec, day, theta, idx))
    flat = [(k, i) for k, (_, _, _, idx) in enumerate(picks) for i in idx]
    if len(flat) > max_assess:
        keep = np.linspace(0, len(flat) - 1, max_assess).astype(int)
        flat = [flat[j] for j in keep]
    for k, i in flat:
        rec, day, theta, _ = picks[k]
        yield day, load_export(data / rec["path"] / "aw-export.json"), i, theta


def run_replay(client: ChatClient, settings: Settings, temps: dict, data: Path, users, use_hooks=True,
               stride_s=120.0, max_assess=200, all_ticks=False) -> list[Row]:
    rows: list[Row] = []
    traces: dict = {}
    for day, trace, i, theta in sample_ticks(data, users, use_hooks, stride_s, max_assess, all_ticks):
        t = float(day.series.times[i])
        ctx = build_context(trace, t, settings.llm.lookback_s, settings.llm.include_titles, settings.llm.private_apps)
        a: Assessment = assess(client, settings, temps, ctx, float(day.score[i]))
        rows.append(Row(day.user, t, str(day.state[i]), float(day.score[i]), a.p_need_raw, a.p_accept_raw,
                        a.decision.action, a.decision.tau, a.resampled, a.calls, float(theta)))
    return rows


def _decisions(rows: list[Row], settings: Settings, temps: dict, gate: GateParams) -> dict:
    """Re-run the gate on stored probabilities under another parameter set (no new LLM calls)."""
    spoke = np.array([decide(r.stuck_score, calibrate(r.p_need_raw, temps["need"]),
                             calibrate(r.p_accept_raw, temps["accept"]), gate).action == SPEAK for r in rows])
    return summarise(rows, spoke)


def summarise(rows: list[Row], spoke: np.ndarray) -> dict:
    stuck = np.array([r.state == "stuck" for r in rows])
    flow = np.array([r.state == "flow" for r in rows])
    tp = int((spoke & stuck).sum())
    return dict(speak=int(spoke.sum()), precision=tp / spoke.sum() if spoke.any() else float("nan"),
                recall=tp / stuck.sum() if stuck.any() else float("nan"),
                flow_false_alarm=float((spoke & flow).sum() / flow.sum()) if flow.any() else float("nan"))


def fit_temps(rows: list[Row]) -> dict:
    y = [r.state == "stuck" for r in rows]
    return {"need": fit_temperature([r.p_need_raw for r in rows], y),
            "accept": fit_temperature([r.p_accept_raw for r in rows], y)}


def report(rows: list[Row], settings: Settings, temps: dict) -> str:
    if not rows:
        return "No ticks reached the LLM trigger: nothing to report."
    y = np.array([r.state == "stuck" for r in rows])
    pn = np.array([r.p_need_raw for r in rows])
    pn_cal = np.array([calibrate(p, temps["need"]) for p in pn])
    s = np.array([r.stuck_score for r in rows])
    lines = [f"ticks sent to the LLM: {len(rows)}   LLM calls: {sum(r.calls for r in rows)}   "
             f"re-checked near the bar: {sum(r.resampled for r in rows)}",
             f"stuck share of these ticks: {y.mean():.1%}",
             "",
             "ranking (AUROC for 'stuck' among triggered ticks):",
             f"  classifier score alone      {auroc(s, y):.3f}",
             f"  LLM p_need (raw)            {auroc(pn, y):.3f}",
             "",
             f"calibration of p_need vs the stuck label (T = {temps['need']:.2f}):",
             f"  raw          ECE {ece(pn, y):.3f}   Brier {brier(pn, y):.3f}",
             f"  calibrated   ECE {ece(pn_cal, y):.3f}   Brier {brier(pn_cal, y):.3f}",
             "",
             "decisions (no matching of recall, so compare with care):"]
    g = settings.gate
    systems = {
        "state-aware gate": _decisions(rows, settings, temps, g),
        "fixed costs (alpha = beta = 0)": _decisions(rows, settings, temps, replace(g, alpha=0.0, beta=0.0)),
    }
    fixed_cut = summarise(rows, np.array([calibrate(r.p_accept_raw, temps["accept"]) >= 0.5 for r in rows]))
    systems["fixed cut p_accept >= 0.5"] = fixed_cut
    lines.append(f"  {'system':32s}{'speak':>7s}{'precision':>11s}{'recall':>8s}{'flow false alarms':>19s}")
    for name, m in systems.items():
        lines.append(f"  {name:32s}{m['speak']:>7d}{m['precision']:>11.2f}{m['recall']:>8.2f}"
                     f"{m['flow_false_alarm']:>19.1%}")
    return "\n".join(lines)


def write_rows(path: Path, rows: list[Row]) -> None:
    path.write_text("".join(json.dumps(r.__dict__) + "\n" for r in rows), encoding="utf-8")


__all__ = ["MockClient", "run_replay", "report", "fit_temps", "write_rows", "DEV_USERS", "TEST_USERS",
           "load_temperatures", "save_temperatures", "QUIET"]
