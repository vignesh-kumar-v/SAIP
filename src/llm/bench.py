"""Run SAIP's gate on ProactiveBench, the benchmark the PRISM paper (Fu et al., ICLR 2026) is scored on.

Each file is one working session: a list of steps in time order, each with a short event description and a
gold label `task_status` (at this step a proactive offer was wanted). For every step the model sees only the
events so far, and is asked three yes/no questions: does the user need help, would they accept an offer, are
they stuck. Each answer becomes a probability (llm.confidence.probe). Then several policies decide "speak or
stay quiet" from those probabilities and are scored against the label: always speak, a fixed 0.5 cut, PRISM's
static and dynamic thresholds, and SAIP's state-aware gate.

Two honest-evaluation choices. Calibration is cross-fitted: the temperatures used on a session are fitted on
all the other sessions, so no session helps calibrate its own predictions. There is no separate "accepted"
label in the data, so both p_need and p_accept are fitted against the same label y. Intervals come from
resampling whole sessions, because steps inside one session are not independent and there are only a dozen.

Probe results are cached line by line, so an interrupted run picks up where it stopped.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from . import prompts
from .calibration import auroc, brier, calibrate, ece, fit_temperature
from .client import ChatClient, LLMError
from .config import GateParams, Settings
from .confidence import probe
from .gate import SPEAK, WAIT, clamp, decide, threshold

STUCK = ("Is the user stuck right now (going in circles, repeating a failure, or making no progress), as opposed to "
         "working smoothly? Answer yes or no.")
POLICIES = ("always", "fixed", "need", "static-cost", "prism", "saip")


@dataclass(frozen=True)
class Session:
    name: str
    domain: str
    steps: list          # [(time, event, y)]


@dataclass(frozen=True)
class Row:
    session: str
    domain: str
    index: int
    y: bool
    p_need: float
    p_accept: float
    s: float


def load_sessions(data: Path) -> list[Session]:
    sessions = []
    for f in sorted(Path(data).glob("*.json")):
        if f.name == "splits.json":
            continue
        raw = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            continue
        steps = [(float(r["observation"]["time"]), r["observation"]["event"], bool(r["task_status"])) for r in raw]
        sessions.append(Session(f.stem, f.stem.split("_")[0], steps))
    return sessions


def take(sessions: list[Session], n: int) -> list[Session]:
    """The first n steps overall, for a quick trial."""
    out = []
    for s in sessions:
        if n <= 0:
            break
        out.append(Session(s.name, s.domain, s.steps[:n]))
        n -= len(s.steps)
    return out


def step_context(session: Session, i: int, history: int = 15) -> str:
    """What the model sees at step i: events up to and including i, oldest first. No future, no labels."""
    window = session.steps[max(0, i - history + 1):i + 1]
    now = session.steps[i][0]
    return "Activity (oldest first):\n" + "\n".join(f"- {now - t:.0f}s ago: {e}" for t, e, _ in window)


def _read_cache(cache: Path | None, model: str) -> dict:
    out = {}
    if cache is not None and cache.is_file():
        for line in cache.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                if d.get("model") == model:
                    out[(d["session"], d["index"], d["history"])] = d
    return out


def run_probes(client: ChatClient, sessions: list[Session], cache: Path | None, history: int = 15, progress=None,
               model: str = "", skipped: list | None = None) -> list[Row]:
    """Three probes per step. `skipped` collects (session, index, error) for steps that failed twice."""
    done = _read_cache(cache, model)
    total, count = sum(len(s.steps) for s in sessions), 0
    rows: list[Row] = []
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
    for s in sessions:
        for i, (_, _, y) in enumerate(s.steps):
            count += 1
            hit = done.get((s.name, i, history))
            if hit is None:
                ctx = step_context(s, i, history)
                for attempt in (0, 1):
                    try:
                        vals = [probe(client, prompts.need_messages(ctx)), probe(client, prompts.accept_messages(ctx)),
                                probe(client, prompts.messages(ctx, STUCK))]
                        break
                    except LLMError as e:
                        vals, err = None, str(e)
                if vals is None:
                    if skipped is not None:
                        skipped.append((s.name, i, err))
                    if progress:
                        progress(count, total)
                    continue
                hit = dict(session=s.name, index=i, history=history, model=model,
                           p_need=vals[0], p_accept=vals[1], s=vals[2])
                if cache is not None:
                    with cache.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(hit) + "\n")
            rows.append(Row(s.name, s.domain, i, y, hit["p_need"], hit["p_accept"], hit["s"]))
            if progress:
                progress(count, total)
    return rows


def _fit(rows: list[Row], field: str) -> float:
    try:
        return fit_temperature([getattr(r, field) for r in rows], [r.y for r in rows])
    except ValueError:
        return 1.0                                  # one class only among the other sessions: leave it as it is


def cross_fit(rows: list[Row]) -> list[Row]:
    """Calibrated need and accept for every row, with temperatures fitted on the other sessions only."""
    temps = {}
    for name in {r.session for r in rows}:
        rest = [r for r in rows if r.session != name]
        temps[name] = (_fit(rest, "p_need"), _fit(rest, "p_accept")) if rest else (1.0, 1.0)
    return [replace(r, p_need=calibrate(r.p_need, temps[r.session][0]),
                    p_accept=calibrate(r.p_accept, temps[r.session][1])) for r in rows]


def _prism_gate(g: GateParams) -> GateParams:
    return replace(g, alpha=0.0, beta=0.0)


def saip_action(r: Row, g: GateParams) -> str:
    return decide(r.s, r.p_need, r.p_accept, g).action


def policy(name: str, r: Row, g: GateParams) -> bool:
    if name == "always":
        return True
    if name == "fixed":
        return r.p_accept >= 0.5
    if name == "need":
        return r.p_need >= 0.5
    if name == "static-cost":
        return r.p_accept >= g.c0 / (g.c0 + g.m0)
    if name == "prism":
        return clamp(r.p_accept, g.eps) >= threshold(r.s, r.p_need, _prism_gate(g))
    if name == "saip":
        return saip_action(r, g) == SPEAK
    raise ValueError(f"unknown policy {name}")


def metrics(y, spoke) -> dict:
    y, spoke = np.asarray(y, bool), np.asarray(spoke, bool)
    tp, fp = int((y & spoke).sum()), int((~y & spoke).sum())
    fn, tn = int((y & ~spoke).sum()), int((~y & ~spoke).sum())
    div = lambda a, b: a / b if b else 0.0
    precision, recall = div(tp, tp + fp), div(tp, tp + fn)
    return dict(tp=tp, fp=fp, fn=fn, tn=tn, recall=recall, precision=precision, accuracy=div(tp + tn, len(y)),
                false_alarm=div(fp, tp + fp), f1=div(2 * precision * recall, precision + recall),
                speak_rate=div(tp + fp, len(y)))


def evaluate(rows: list[Row], name: str, g: GateParams) -> dict:
    spoke = [policy(name, r, g) for r in rows]
    m = metrics([r.y for r in rows], spoke)
    if name == "saip":
        m["wait"] = sum(saip_action(r, g) == WAIT for r in rows)
    return m


def bootstrap(rows: list[Row], name: str, g: GateParams, n: int = 2000, seed: int = 0) -> dict:
    """95% intervals for F1 and false-alarm, resampling whole sessions."""
    names = sorted({r.session for r in rows})
    counts = np.array([[sum(1 for r in rows if r.session == s and r.y and policy(name, r, g)),
                        sum(1 for r in rows if r.session == s and not r.y and policy(name, r, g)),
                        sum(1 for r in rows if r.session == s and r.y and not policy(name, r, g))] for s in names])
    rng = np.random.default_rng(seed)
    f1, fa = [], []
    for _ in range(n):
        tp, fp, fn = counts[rng.integers(0, len(names), len(names))].sum(axis=0)
        f1.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
        fa.append(fp / (tp + fp) if tp + fp else 0.0)
    q = lambda v: (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)))
    return dict(f1=q(f1), false_alarm=q(fa))


def quality(rows: list[Row], field: str) -> dict:
    p, y = np.array([getattr(r, field) for r in rows]), np.array([r.y for r in rows])
    return dict(auroc=auroc(p, y), ece=ece(p, y), brier=brier(p, y))


def _ci(lo_hi) -> str:
    return f"[{lo_hi[0]:.2f}, {lo_hi[1]:.2f}]"


CAVEATS = """Read this before quoting numbers
- `s` here is the model's own yes/no answer about being stuck, not SAIP's classifier on desktop telemetry, which
  this benchmark does not have. So this tests the shape of the gate, not the full system.
- There is no "accepted" label, so p_accept is calibrated against the same label as p_need (task_status).
- There are only 12 sessions, so the intervals are wide; differences smaller than the intervals mean nothing.
- The PRISM paper's own numbers come from a fine-tuned model and an LLM judge. They are not directly comparable
  to a prompted model scored on task_status."""


def report(rows: list[Row], settings: Settings, skipped: int = 0, model: str | None = None, mock: bool = False) -> str:
    model = settings.llm.model if model is None else model
    g = settings.gate
    head = ["THESE NUMBERS ARE FROM A STAND-IN, NOT A REAL MODEL, AND MEAN NOTHING ABOUT ANY REAL MODEL.", ""] if mock else []
    if not rows:
        return "\n".join(head + ["No steps were probed: nothing to report."])
    cal = cross_fit(rows)
    head += [f"model: {model or '(unnamed)'}",
             f"sessions: {len({r.session for r in rows})}   steps: {len(rows)}   positives: {sum(r.y for r in rows)}   "
             f"skipped: {skipped}", ""]
    lines = ["policies (calibrated = temperatures cross-fitted across sessions; F1 and false-alarm with 95% interval "
             "over sessions)",
             f"  {'policy':12s}{'input':>11s}{'recall':>8s}{'prec':>7s}{'acc':>7s}{'FA':>6s}{'F1':>6s}{'speak':>7s}"
             f"   {'F1 interval':14s}{'FA interval':14s}"]
    waits = {}
    for name in POLICIES:
        for label, data in (("raw", rows), ("calibrated", cal)):
            m, b = evaluate(data, name, g), bootstrap(data, name, g)
            lines.append(f"  {name:12s}{label:>11s}{m['recall']:>8.2f}{m['precision']:>7.2f}{m['accuracy']:>7.2f}"
                         f"{m['false_alarm']:>6.2f}{m['f1']:>6.2f}{m['speak_rate']:>7.2f}"
                         f"   {_ci(b['f1']):14s}{_ci(b['false_alarm']):14s}")
            if name == "saip":
                waits[label] = m["wait"]
    lines += ["", f"saip answered WAIT (counted as not speaking) on {waits['raw']} steps raw, {waits['calibrated']} "
                  "calibrated.", "", "probability quality against the label",
              f"  {'probe':10s}{'input':>11s}{'AUROC':>8s}{'ECE':>7s}{'Brier':>7s}"]
    for field in ("p_need", "p_accept"):
        for label, data in (("raw", rows), ("calibrated", cal)):
            q = quality(data, field)
            lines.append(f"  {field:10s}{label:>11s}{q['auroc']:>8.3f}{q['ece']:>7.3f}{q['brier']:>7.3f}")
    lines += ["", "per domain (calibrated): F1 / false-alarm"]
    for domain in sorted({r.domain for r in rows}):
        part = [r for r in cal if r.domain == domain]
        cells = []
        for name in ("fixed", "prism", "saip"):
            m = evaluate(part, name, g)
            cells.append(f"{name} {m['f1']:.2f} / {m['false_alarm']:.2f}")
        lines.append(f"  {domain:10s}({len(part)} steps)   " + "   ".join(cells))
    return "\n".join(head + lines + ["", CAVEATS])


def write_rows(path: Path, rows: list[Row]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(json.dumps(r.__dict__) + "\n" for r in rows), encoding="utf-8")


__all__ = ["STUCK", "POLICIES", "Session", "Row", "load_sessions", "take", "step_context", "run_probes", "cross_fit",
           "policy", "metrics", "evaluate", "bootstrap", "quality", "report", "write_rows"]
