"""Compare SAIP's state-aware gate with PRISM's fixed-cost gate on the same ticks, in PRISM's own metrics.

    python -m llm replay --split test --all-ticks --out rows.jsonl      # once: the model's answers for every tick
    python -m llm.compare rows.jsonl                                    # then: no model needed

Why this shape (the reasoning is in docs/PRISM_Comparison.md):

* PRISM (Fu et al., ICLR 2026) is a gate, tau = C_FA / (C_FA + p_need * C_FN), with costs fixed for the session.
  SAIP's gate is the same formula with costs that move with the stuck score, so alpha = beta = 0 IS PRISM's gate.
  Both are run here on the same stored probabilities, so any difference comes from the gate and nothing else.
* PRISM has no classifier in front of the model: it judges every event. So the PRISM rows use every tick, and the
  SAIP rows speak only where the classifier's trigger (score >= theta) let the model be asked at all.
* The metrics are the ones PRISM reports, with ProactiveBench's definitions (eval/calculate_agent_performance.py
  in thunlp/ProactiveAgent). Note that their "False-Alarm" is FP / (TP + FP), the share of offers that were
  unwanted, which is 1 - precision; it is not the false-positive rate. Both are printed.

A tick counts as positive when the generator labelled it stuck. PRISM's label is need AND accept, set by LLM
judges; the generator has no acceptance label. The labels come from our own generator, so these numbers compare
two gates on our data. They are not comparable row for row with the numbers in PRISM's paper.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

from .calibration import calibrate, load_temperatures
from .config import GateParams, load_settings, user_config_dir
from .gate import SPEAK, decide

RATIOS = ((1.0, 4.0), (1.0, 2.0), (1.0, 1.0), (1.2, 1.0))        # C_FA : C_FN, the sweep in PRISM's Table 11
TARGET_RECALLS = (0.80, 0.90)
EPS = 1e-8


def load_rows(path: str | Path) -> list[dict]:
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


class Ticks:
    """The stored ticks as arrays: label, stuck score, trigger, calibrated probabilities, and the day each is from."""

    def __init__(self, rows: list[dict], temps: dict, saip_temps: dict | None = None):
        self.n = len(rows)
        self.stuck = np.array([r["state"] == "stuck" for r in rows], dtype=bool)
        self.flow = np.array([r["state"] == "flow" for r in rows], dtype=bool)
        self.s = np.array([r["stuck_score"] for r in rows], dtype=float)
        self.theta = np.array([r.get("theta", 0.0) for r in rows], dtype=float)
        self.p_need = np.array([calibrate(r["p_need_raw"], temps["need"]) for r in rows], dtype=float)
        self.p_accept = np.array([calibrate(r["p_accept_raw"], temps["accept"]) for r in rows], dtype=float)
        # SAIP only asks the model about ticks past its trigger, so in use its calibration is fitted on those ticks
        # (replay without --all-ticks). Each system is scored with the calibration fitted on what it sees.
        st = saip_temps or temps
        self.saip_temps = st
        self.p_need_saip = np.array([calibrate(r["p_need_raw"], st["need"]) for r in rows], dtype=float)
        self.p_accept_saip = np.array([calibrate(r["p_accept_raw"], st["accept"]) for r in rows], dtype=float)
        self.triggered = self.s >= self.theta
        self.day = np.array([f'{r["user"]}:{int(r["t"] // 86400)}' for r in rows])     # resampling unit

    @property
    def has_untriggered(self) -> bool:
        return bool((~self.triggered).any())


def gate_speaks(t: Ticks, g: GateParams, saip: bool = False) -> np.ndarray:
    need, accept = (t.p_need_saip, t.p_accept_saip) if saip else (t.p_need, t.p_accept)
    return np.array([decide(t.s[i], need[i], accept[i], g).action == SPEAK for i in range(t.n)], dtype=bool)


def systems(t: Ticks, g: GateParams) -> dict:
    """Each system's speak / stay-silent choice at every tick, under base costs g.c0 : g.m0."""
    fixed_g = replace(g, alpha=0.0, beta=0.0)
    fixed, aware = gate_speaks(t, fixed_g), gate_speaks(t, g)                 # calibrated on every tick
    return {
        "PRISM gate, every tick": fixed,
        "SAIP: trigger + state-aware gate": gate_speaks(t, g, saip=True) & t.triggered,
        "  state-aware gate, no trigger": aware,
        "  PRISM gate behind the trigger": gate_speaks(t, fixed_g, saip=True) & t.triggered,
        "fixed cut p_accept >= 0.5": t.p_accept >= 0.5,
        "always speak": np.ones(t.n, dtype=bool),
    }


def metrics(spoke: np.ndarray, t: Ticks, idx: np.ndarray | None = None) -> dict:
    """PRISM's metrics with ProactiveBench's definitions, plus two that say what an interruption cost."""
    stuck, flow = (t.stuck, t.flow) if idx is None else (t.stuck[idx], t.flow[idx])
    spoke = spoke if idx is None else spoke[idx]
    tp, fp = int((spoke & stuck).sum()), int((spoke & ~stuck).sum())
    fn, tn = int((~spoke & stuck).sum()), int((~spoke & ~stuck).sum())
    recall, precision = tp / (tp + fn + EPS), tp / (tp + fp + EPS)
    return {"speak": tp + fp, "recall": recall, "precision": precision, "accuracy": (tp + tn) / (len(stuck) + EPS),
            "false_alarm": fp / (tp + fp + EPS),                 # ProactiveBench / PRISM: unwanted share of offers
            "f1": 2 * precision * recall / (precision + recall + EPS),
            "fpr": fp / (fp + tn + EPS),                         # the usual false-positive rate
            "flow_interrupted": float((spoke & flow).sum() / (flow.sum() + EPS))}


def bootstrap(spoke_a: np.ndarray, spoke_b: np.ndarray, t: Ticks, n: int = 2000, seed: int = 0) -> dict:
    """95% intervals for (b - a) on three metrics, resampling whole days: ticks within a day are not independent."""
    days = np.unique(t.day)
    members = {d: np.flatnonzero(t.day == d) for d in days}
    rng = np.random.default_rng(seed)
    keys = ("f1", "false_alarm", "flow_interrupted")
    diffs = {k: [] for k in keys}
    for _ in range(n):
        idx = np.concatenate([members[d] for d in rng.choice(days, len(days))])
        a, b = metrics(spoke_a, t, idx), metrics(spoke_b, t, idx)
        for k in keys:
            diffs[k].append(b[k] - a[k])
    return {k: (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))) for k, v in diffs.items()}


def at_recall(name: str, t: Ticks, g: GateParams, target: float) -> tuple[float, dict] | None:
    """The mildest miss cost m0 (c0 = 1) at which this system reaches the target recall, and its metrics there.

    Without matching recall a gate that simply speaks less looks better on false alarms and worse on recall, and
    the comparison says nothing (docs/State_Aware_Interruption_Formulas.md, section 7).
    """
    for m0 in np.geomspace(0.05, 200.0, 97):
        m = metrics(systems(t, replace(g, c0=1.0, m0=float(m0)))[name], t)
        if m["recall"] >= target:
            return float(m0), m
    return None


def _table(rows: list[tuple[str, dict]]) -> list[str]:
    head = f"  {'system':34s}{'speak':>6s}{'recall':>8s}{'precis.':>9s}{'accur.':>8s}{'false-alarm':>13s}{'F1':>7s}{'FPR':>7s}{'flow hit':>10s}"
    out = [head]
    for name, m in rows:
        out.append(f"  {name:34s}{m['speak']:>6d}{m['recall']:>8.1%}{m['precision']:>9.1%}{m['accuracy']:>8.1%}"
                   f"{m['false_alarm']:>13.1%}{m['f1']:>7.1%}{m['fpr']:>7.1%}{m['flow_interrupted']:>10.1%}")
    return out


def report(rows: list[dict], g: GateParams, temps: dict, n_boot: int = 2000, saip_temps: dict | None = None) -> str:
    if not rows:
        return "No rows: run `python -m llm replay --all-ticks --out rows.jsonl` first."
    t = Ticks(rows, temps, saip_temps)
    lines = [f"ticks: {t.n}   stuck: {t.stuck.mean():.1%}   flow: {t.flow.mean():.1%}   "
             f"above the classifier's trigger: {t.triggered.mean():.1%}   user-days: {len(np.unique(t.day))}",
             f"calibration, every tick: T_need = {temps['need']:.2f}, T_accept = {temps['accept']:.2f}   "
             f"behind the trigger: T_need = {t.saip_temps['need']:.2f}, T_accept = {t.saip_temps['accept']:.2f}",
             f"state-aware costs: alpha = {g.alpha:.2f}, beta = {g.beta:.2f}"]
    if saip_temps is None:
        lines += ["note: one calibration for every system. SAIP only asks the model past its trigger, so pass the",
                  "      temperatures fitted on those ticks with --saip-calibration (replay --fit without --all-ticks)."]
    if not t.has_untriggered:
        lines += ["", "WARNING: every tick here is above the trigger, so this file was made without --all-ticks.",
                  "The 'every tick' rows are then PRISM's gate behind SAIP's classifier, not PRISM as published.",
                  "Re-run: python -m llm replay --all-ticks --out rows.jsonl"]
    sys_now = systems(t, g)
    lines += ["", f"1. At the configured base costs (C_FA : C_FN = {g.c0:g} : {g.m0:g})"]
    lines += _table([(name, metrics(spoke, t)) for name, spoke in sys_now.items()])
    a, b = "PRISM gate, every tick", "SAIP: trigger + state-aware gate"
    if n_boot:
        ci = bootstrap(sys_now[a], sys_now[b], t, n_boot)
        lines += ["", f"   SAIP minus PRISM, 95% interval over {len(np.unique(t.day))} resampled user-days ({n_boot} resamples):"]
        lines += [f"     {label:32s}{lo:+.1%} to {hi:+.1%}" for label, (lo, hi) in
                  (("F1", ci["f1"]), ("false-alarm (share of offers)", ci["false_alarm"]),
                   ("flow ticks interrupted", ci["flow_interrupted"]))]
    lines += ["", "2. Cost-ratio sweep (the ratios in PRISM's Table 11)"]
    for c0, m0 in RATIOS:
        s = systems(t, replace(g, c0=c0, m0=m0))
        lines += [f"   C_FA : C_FN = {c0:g} : {m0:g}"] + _table([(n, metrics(s[n], t)) for n in (a, b)])
    ceiling = float((t.triggered & t.stuck).sum() / (t.stuck.sum() + EPS))
    saip_now = metrics(sys_now[b], t)
    lines += ["", "3. At matched recall (a system's miss cost is tuned until it reaches the target)",
              f"   The trigger lets the model see {ceiling:.1%} of stuck ticks, so that is the most SAIP's recall can be."]
    lines.append(f"   PRISM tuned down to SAIP's recall at the configured costs ({saip_now['recall']:.1%})")
    hit = at_recall(a, t, g, saip_now["recall"])
    lines += _table([(b, saip_now)] + ([(f"{a} (m0={hit[0]:.2g})"[:34], hit[1])] if hit else []))
    for target in TARGET_RECALLS:
        lines.append(f"   target recall {target:.0%}")
        found, missed = [], []
        for name in (a, b):
            hit = at_recall(name, t, g, target)
            if hit is None:
                missed.append(f"  {name:34s} never reaches {target:.0%}")
            else:
                found.append((f"{name} (m0={hit[0]:.2g})"[:34], hit[1]))
        lines += (_table(found) if found else []) + missed
    lines += ["", "false-alarm = FP / (TP + FP), ProactiveBench's and PRISM's definition (1 - precision).",
              "flow hit = share of ticks labelled flow at which the system spoke.",
              "Positive = the generator's stuck label. Our own generator made both the data and the labels, so this",
              "compares two gates on our data; it is not comparable row for row with PRISM's published table."]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m llm.compare", description=__doc__.split("\n")[0])
    ap.add_argument("rows", help="per-tick rows from `python -m llm replay --all-ticks --out FILE`")
    ap.add_argument("--calibration", help="temperatures fitted on every tick (replay --all-ticks --fit); "
                                          "default: the one replay --fit saved")
    ap.add_argument("--saip-calibration", help="temperatures fitted on triggered ticks only (replay --fit without "
                                               "--all-ticks), used for the systems behind SAIP's trigger")
    ap.add_argument("--config", help="path to private.toml (for the gate's costs)")
    ap.add_argument("--bootstrap", type=int, default=2000, help="resamples for the intervals (0 to skip)")
    a = ap.parse_args(argv)
    cal = Path(a.calibration) if a.calibration else user_config_dir() / "calibration.json"
    try:
        rows = load_rows(a.rows)
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    saip = load_temperatures(a.saip_calibration) if a.saip_calibration else None
    print(report(rows, load_settings(a.config).gate, load_temperatures(cal), a.bootstrap, saip))
    return 0


if __name__ == "__main__":
    sys.exit(main())
