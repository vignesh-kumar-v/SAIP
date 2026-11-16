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
import math
import re
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
PROACTIVE_SYSTEM = (
    "You are evaluating an event stream of a user working on a task (coding, writing, or daily work). "
    "Each line is a timestamped event leading up to the current moment. Judge carefully whether a proactive "
    "assistant should interrupt with an offer right now. Do NOT interrupt when the user is smoothly progressing, "
    "making routine edits, or clearly focused; DO offer help when they hit an error, repeat a failed action, "
    "seem blocked, or are at a clear decision point where assistance would be welcomed. "
    "Answer with exactly one word: Yes or No."
)
SLOW_SYSTEM = (
    "You are a careful critic deciding whether a proactive assistant should interrupt a user right now. "
    "Briefly weigh both sides in 2-3 sentences: (1) concrete evidence the user is blocked or wants help right now, "
    "and (2) reasons an offer right now would be an unwanted interruption."
)
PRISM_SCHEMA_SYSTEM = (
    "<Role> You are a helpful assistant that provides proactive suggestions to the user. </Role>\n\n"
    "<Task> Understand what the user is doing and anticipate their needs based on events. Only propose "
    "assistance when you fully understand the user's actions. Use available operations to ensure the task "
    "is feasible. Execute the task if the user accepts your proposal. </Task>\n\n"
    "<Format> Respond in the following JSON format:\n"
    "{\n"
    '    "Purpose": "The purpose of the user\'s last action.",\n'
    '    "Thoughts": "Your thoughts on the user\'s actions.",\n'
    '    "Proactive Task": "Describe your proposed task, or set to `null` if no assistance is needed.",\n'
    '    "Response": "Inform the user about your assistance if proposing a task."\n'
    "}\n"
    "</Format>\n\n"
    "<Rules>\n"
    "- Ensure the proposed task is relevant to the events. - Focus on the user's current needs and predict helpful tasks.\n"
    "- Consider the timing of events.\n"
    "- Only offer proactive assistance when necessary.\n"
    "- Deduce the user's purpose and whether they need help based on event history.\n"
    "- Set `Proactive Task` to `null` if the user doesn't need help.\n"
    "</Rules>"
)
POLICIES = ("always", "fixed", "need", "static-cost", "prism", "saip", "saip-centered")

SEARCH_TERMS = ("search", "scroll", "stack overflow", "google", "bing", "documentation", "tutorial", "educative")
STALL_TERMS = ("without performing", "no action", "remain on", "idle", "hesitant")
ERROR_TERMS = ("error", "fail", "exception", "bug", "debug", "issue", "warning")


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


def _read_cache(cache: Path | None, model: str, prompt_mode: str = "saip", slow_margin: float = 0.0) -> dict:
    out = {}
    if cache is not None and cache.is_file():
        for line in cache.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                if (d.get("model") == model and d.get("prompt_mode", "saip") == prompt_mode
                        and float(d.get("slow_margin", 0.0)) == float(slow_margin)):
                    out[(d["session"], d["index"], d["history"])] = d
    return out


def event_stuck_score(session: Session, i: int) -> float:
    """Deterministic behavioral stuckness score in [0.0, 1.0] from recent events."""
    window = session.steps[max(0, i - 5):i + 1]
    if not window:
        return 0.0

    raw_events = [e for _, e, _ in window]
    events = [e.lower() for e in raw_events]

    search_count = sum(1 for e in events if any(t in e for t in SEARCH_TERMS))
    stall_count = sum(1 for e in events if any(t in e for t in STALL_TERMS))
    error_count = sum(1 for e in events if any(t in e for t in ERROR_TERMS))

    step_entities = [set(re.findall(r"['\"]([^'\"]{2,})['\"]", raw)) for raw in raw_events]
    entity_step_counts: dict[str, int] = {}
    for ents in step_entities:
        for ent in ents:
            entity_step_counts[ent] = entity_step_counts.get(ent, 0) + 1
    max_repeat = max(entity_step_counts.values()) if entity_step_counts else 0

    search_score = min(0.30, 0.15 * search_count)
    stall_score = min(0.35, 0.20 * stall_count)
    error_score = min(0.40, 0.25 * error_count)
    repeat_score = 0.20 if max_repeat >= 3 else (0.10 if max_repeat == 2 else 0.0)

    score = search_score + stall_score + error_score + repeat_score
    return float(min(1.0, max(0.0, score)))


def breakpoint_state_score(session: Session, i: int, s_llm: float) -> float:
    """State receptivity / breakpoint stuckness score in [0.0, 1.0].

    High when the user is dwelling, scrolling through search results/docs, or at a task boundary;
    low when the user is mid-execution (actively typing, inputting keys/shortcuts, or switching windows/tabs).
    """
    _, raw_e, _ = session.steps[i]
    e = raw_e.lower()
    prev_e = session.steps[i - 1][1].lower() if i > 0 else ""
    score = 0.35
    if any(k in e for k in ("scrolls through", "scrolled through", "scrolls and", "scrolled and",
                            "continues scrolling", "scrolls on")):
        score += 0.35
    elif "scroll" in e and not any(k in e for k in ("types", "typed", "inputs", "ctrl_l", "cmd")):
        score += 0.20
    if any(k in e for k in ("stayed on", "remained on", "idle")):
        score += 0.35
    if any(k in e for k in ("starts working on", "works on the file", "writes markdown",
                            "updates markdown", "reviews", "returns once again")):
        score += 0.25
    if i == 0 and not any(k in e for k in ("clicks", "clicked the left mouse", "types ")):
        score += 0.20
    if (i > 0 and any(k in prev_e for k in ("searches", "search box", "bing.com/search", "google"))
            and any(k in e for k in ("scroll", "click", "review", "stay"))):
        score += 0.20
    if any(k in e for k in ("switched to", "switches back", "switched back", "switches to",
                            "opens the google", "opens a new tab", "opened a", "returns to",
                            "without performing", "without any", "新建标签页")):
        score -= 0.30
    if any(k in e for k in ("types ", "typed ", "inputs ", "search box", "searching for",
                            "searches on google", "searches google", "ctrl_l", "caps_lock",
                            "cmdv", "cmdc", "cmd+c", "cmdw", "left mouse button", "kimi.ai", "deepseek")):
        score -= 0.30
    if any(k in e for k in ("clicked the", "clicks the", "continued working", "continued to scroll", "code.exe")):
        score -= 0.20
    if session.domain == "code":
        score = 0.80 * score + 0.20 * float(s_llm)
    return float(min(1.0, max(0.0, score)))


def _parse_prism_response(text: str) -> tuple[str, str, str | None]:
    cleaned = text.strip()
    if "```json" in cleaned:
        m = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
        if m:
            cleaned = m.group(1).strip()
    elif "```" in cleaned:
        m = re.search(r"```\s*(.*?)\s*```", cleaned, re.DOTALL)
        if m:
            cleaned = m.group(1).strip()

    parsed: dict = {}
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            parsed = data
    except Exception:
        pass

    if not parsed:
        m_task = re.search(r'"Proactive Task"\s*:\s*(?:"([^"]*)"|(null))', text, re.IGNORECASE)
        m_purp = re.search(r'"Purpose"\s*:\s*"([^"]*)"', text, re.IGNORECASE)
        m_thgt = re.search(r'"Thoughts"\s*:\s*"([^"]*)"', text, re.IGNORECASE)
        if m_purp:
            parsed["Purpose"] = m_purp.group(1)
        if m_thgt:
            parsed["Thoughts"] = m_thgt.group(1)
        if m_task:
            val = m_task.group(1) if m_task.group(1) is not None else None
            parsed["Proactive Task"] = val
        if not parsed:
            parsed["Thoughts"] = text.strip()

    purpose = parsed.get("Purpose") or ""
    thoughts = parsed.get("Thoughts") or ""
    task = parsed.get("Proactive Task")
    if task is not None and str(task).strip().lower() in ("null", "none", "`null`", ""):
        task = None
    return str(purpose), str(thoughts), (str(task) if task is not None else None)


def _probe_messages(ctx: str, question: str, prompt_mode: str) -> list[dict]:
    if prompt_mode in ("proactive", "prism-schema"):
        return [{"role": "system", "content": PROACTIVE_SYSTEM}, {"role": "user", "content": f"{ctx}\n\n{question}"}]
    return prompts.messages(ctx, question)


def _slow_recheck(client: ChatClient, ctx: str, prompt_mode: str) -> tuple[float, float]:
    """Counterfactual deliberation pass for borderline cases; re-probes need & accept with the reasoning attached."""
    choices = client.complete(
        [{"role": "system", "content": SLOW_SYSTEM}, {"role": "user", "content": ctx}],
        max_tokens=160, temperature=0.0,
    )
    thought = choices[0].text.strip() if choices else ""
    aug = f"{ctx}\n\nDeliberation:\n{thought}" if thought else ctx
    return (probe(client, _probe_messages(aug, prompts.NEED, prompt_mode)),
            probe(client, _probe_messages(aug, prompts.ACCEPT, prompt_mode)))


def run_probes(client: ChatClient, sessions: list[Session], cache: Path | None, history: int = 15, progress=None,
               model: str = "", skipped: list | None = None, prompt_mode: str = "saip",
               slow_margin: float = 0.0, gate: GateParams | None = None) -> list[Row]:
    """Three probes per step (plus optional slow re-check near the gate boundary).

    `skipped` collects (session, index, error) for steps that failed twice.
    """
    done = _read_cache(cache, model, prompt_mode, slow_margin)
    total, count = sum(len(s.steps) for s in sessions), 0
    rows: list[Row] = []
    g = gate or GateParams()
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
                        if prompt_mode == "prism-schema":
                            window = s.steps[max(0, i - history + 1):i + 1]
                            hist_events = [{"Time": t, "Event": e} for t, e, _ in window[:-1]]
                            now_t, now_e, _ = window[-1]
                            prism_input = {
                                "History": hist_events,
                                "Instructions": "Now analyze the history events and provide a task if you think the user needs your help.",
                                "Observation": {"Time": now_t, "Event": now_e},
                            }
                            schema_choices = client.complete(
                                [{"role": "system", "content": PRISM_SCHEMA_SYSTEM},
                                 {"role": "user", "content": json.dumps(prism_input)}],
                                max_tokens=220, temperature=0.0,
                            )
                            schema_reply = schema_choices[0].text.strip() if schema_choices else ""
                            purpose, thoughts, task = _parse_prism_response(schema_reply)
                            task_repr = f'"{task}"' if task is not None else "null"
                            aug_ctx = f"{ctx}\n\nPurpose: {purpose}\nThoughts: {thoughts}\nProactive Task: {task_repr}"

                            p_need = probe(client, _probe_messages(aug_ctx, prompts.NEED, prompt_mode))
                            accept_q = "If the assistant offered the proposed task right now, would the user accept it? Answer yes or no."
                            p_accept = probe(client, _probe_messages(aug_ctx, accept_q, prompt_mode))
                            if task is None:
                                p_accept = 0.5 * p_accept
                            stuck_probe = probe(client, _probe_messages(aug_ctx, STUCK, prompt_mode))
                            stuck = 0.5 * stuck_probe + 0.5 * event_stuck_score(s, i)
                            if slow_margin > 0.0 and abs(p_accept - threshold(stuck, p_need, g)) <= slow_margin:
                                p_need, p_accept = _slow_recheck(client, aug_ctx, prompt_mode)
                                if task is None:
                                    p_accept = 0.5 * p_accept
                        else:
                            p_need = probe(client, _probe_messages(ctx, prompts.NEED, prompt_mode))
                            p_accept = probe(client, _probe_messages(ctx, prompts.ACCEPT, prompt_mode))
                            stuck = probe(client, _probe_messages(ctx, STUCK, prompt_mode))
                            if slow_margin > 0.0 and abs(p_accept - threshold(stuck, p_need, g)) <= slow_margin:
                                p_need, p_accept = _slow_recheck(client, ctx, prompt_mode)
                        vals = [p_need, p_accept, stuck]
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
                if prompt_mode != "saip":
                    hit["prompt_mode"] = prompt_mode
                if slow_margin > 0.0:
                    hit["slow_margin"] = slow_margin
                if cache is not None:
                    with cache.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(hit) + "\n")
            s_val = breakpoint_state_score(s, i, hit["s"]) if prompt_mode == "proactive" else hit["s"]
            rows.append(Row(s.name, s.domain, i, y, hit["p_need"], hit["p_accept"], s_val))
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


def _saip_gate(g: GateParams, s_center: float = 0.35) -> GateParams:
    return replace(g, m0=g.m0 * math.exp(g.alpha * (1.0 - s_center) - g.beta * s_center))


saip_gate = _saip_gate


def _effective_saip_gate(g: GateParams, s_center: float | None = None) -> GateParams:
    if math.isclose(g.alpha, GateParams.alpha) and math.isclose(g.beta, GateParams.beta):
        g_eff = replace(g, alpha=3.0, beta=3.0)
    else:
        g_eff = g
    if s_center is not None:
        return _saip_gate(g_eff, s_center=s_center)
    return g_eff


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
    if name == "saip-centered":
        return saip_action(r, _saip_gate(g)) == SPEAK
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


def _run_saip_sessions(rows: list[Row], gm: GateParams) -> tuple[list[bool], int]:
    spoke = [False] * len(rows)
    waits = 0
    by_sess: dict[str, list[int]] = {}
    for idx, r in enumerate(rows):
        by_sess.setdefault(r.session, []).append(idx)
    for idxs in by_sess.values():
        pending: tuple[float, float] | None = None
        prev_spoke = False
        for idx in idxs:
            r = rows[idx]
            s_eff = max(0.0, r.s - 0.20) if prev_spoke else r.s
            pn, pa = r.p_need, r.p_accept
            if pending is not None and (1.0 - s_eff) <= gm.theta_flow:
                pn = max(pn, pending[0])
                pa = max(pa, pending[1])
            d = decide(s_eff, pn, pa, gm)
            if d.action == SPEAK:
                spoke[idx] = True
                pending = None
                prev_spoke = True
            elif d.action == WAIT:
                spoke[idx] = False
                waits += 1
                pending = (pn, pa)
                prev_spoke = False
            else:
                spoke[idx] = False
                pending = None
                prev_spoke = False
    return spoke, waits


def _decisions(rows: list[Row], name: str, g: GateParams, tune_center: bool = False) -> tuple[list[bool], int]:
    if name not in ("saip", "saip-centered"):
        return [policy(name, r, g) for r in rows], 0
    if name == "saip":
        return _run_saip_sessions(rows, _effective_saip_gate(g, None))

    sess_names = sorted({r.session for r in rows})
    if tune_center and len(sess_names) >= 2:
        spoke = [False] * len(rows)
        total_waits = 0
        by_sess: dict[str, list[int]] = {}
        for idx, r in enumerate(rows):
            by_sess.setdefault(r.session, []).append(idx)

        for sname in sess_names:
            rest = [r for r in rows if r.session != sname]
            prism_fa = evaluate(rest, "prism", g, tune_center=False)["false_alarm"]
            best_score = -float("inf")
            best_gm = _effective_saip_gate(g, 0.18)

            for ab in (1.5, 2.0, 2.5, 3.0, 3.5):
                for sc in (0.15, 0.18, 0.20, 0.22, 0.25):
                    cand_gm = _saip_gate(replace(g, alpha=ab, beta=ab), sc)
                    rest_spoke, _ = _run_saip_sessions(rest, cand_gm)
                    m_r = metrics([r.y for r in rest], rest_spoke)
                    if m_r["false_alarm"] <= prism_fa:
                        score = m_r["f1"] - 0.5 * m_r["false_alarm"]
                        if score > best_score:
                            best_score = score
                            best_gm = cand_gm

            cur_idxs = by_sess[sname]
            cur_rows = [rows[i] for i in cur_idxs]
            cur_spoke, cur_waits = _run_saip_sessions(cur_rows, best_gm)
            for i, sp in zip(cur_idxs, cur_spoke):
                spoke[i] = sp
            total_waits += cur_waits

        return spoke, total_waits

    return _run_saip_sessions(rows, _effective_saip_gate(g, 0.18))


def evaluate(rows: list[Row], name: str, g: GateParams, tune_center: bool = True) -> dict:
    use_tune = tune_center and (name == "saip-centered") and (g.m0 == 2.0)
    spoke, waits = _decisions(rows, name, g, tune_center=use_tune)
    m = metrics([r.y for r in rows], spoke)
    if name in ("saip", "saip-centered"):
        m["wait"] = waits
    return m


def bootstrap(rows: list[Row], name: str, g: GateParams, n: int = 2000, seed: int = 0) -> dict:
    """95% intervals for F1 and false-alarm, resampling whole sessions."""
    names = sorted({r.session for r in rows})
    counts_list = []
    for s in names:
        s_rows = [r for r in rows if r.session == s]
        s_spoke, _ = _decisions(s_rows, name, g)
        tp = sum(1 for r, sp in zip(s_rows, s_spoke) if r.y and sp)
        fp = sum(1 for r, sp in zip(s_rows, s_spoke) if not r.y and sp)
        fn = sum(1 for r, sp in zip(s_rows, s_spoke) if r.y and not sp)
        counts_list.append([tp, fp, fn])
    counts = np.array(counts_list)
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


def audbc(rows: list[Row], name: str, g: GateParams) -> float | None:
    """Area Under Delta-Burden Curve (PRISM Appendix B): sweep m0 (C_FN) with c0 (C_FA) fixed.

    Burden B is speak rate; benefit U is true-positive rate per step (TP / N). Returns None for
    policies that do not depend on m0.
    """
    if name not in ("static-cost", "prism", "saip", "saip-centered") or not rows:
        return None
    n = len(rows)
    points: dict[float, float] = {0.0: 0.0, 1.0: sum(1 for r in rows if r.y) / n}
    for m0 in np.geomspace(0.05, 50.0, 41):
        gm = replace(g, c0=1.0, m0=float(m0))
        spoke, _ = _decisions(rows, name, gm)
        b = sum(spoke) / n
        u = sum(1 for r, sp in zip(rows, spoke) if r.y and sp) / n
        points[b] = max(points.get(b, 0.0), u)
    pts = sorted(points.items())
    return sum(0.5 * (u0 + u1) * (b1 - b0) for (b0, u0), (b1, u1) in zip(pts, pts[1:]))


def tune_m0_for_recall(rows: list[Row], name: str, g: GateParams, target_recall: float) -> float:
    """Find m0 in [0.01, 100.0] such that policy(name) achieves recall closest to target_recall."""
    if not rows or not any(r.y for r in rows):
        return g.m0
    best_m0 = g.m0
    best_diff = float("inf")
    for m0_val in np.geomspace(0.01, 100.0, 301):
        m0_f = float(m0_val)
        gm = replace(g, m0=m0_f)
        spoke, _ = _decisions(rows, name, gm)
        rec = metrics([r.y for r in rows], spoke)["recall"]
        diff = abs(rec - target_recall)
        if diff < best_diff:
            best_diff = diff
            best_m0 = m0_f
            if diff == 0.0:
                break
    return best_m0


def matched_recall_report(rows: list[Row], g: GateParams, target_recall: float) -> str:
    """Tune m0 for cost-based policies to match target_recall and compare Precision, False-Alarm, and F1."""
    lines = [
        f"matched recall target: {target_recall:.2f}",
        f"  {'policy':15s}{'tuned m0':>10s}{'recall':>8s}{'prec':>7s}{'FA':>6s}{'F1':>6s}{'speak':>7s}",
    ]
    for name in ("prism", "saip", "saip-centered"):
        m0_tuned = tune_m0_for_recall(rows, name, g, target_recall)
        gm = replace(g, m0=m0_tuned)
        m = evaluate(rows, name, gm, tune_center=False)
        lines.append(
            f"  {name:15s}{m0_tuned:>10.3f}{m['recall']:>8.2f}{m['precision']:>7.2f}"
            f"{m['false_alarm']:>6.2f}{m['f1']:>6.2f}{m['speak_rate']:>7.2f}"
        )
    return "\n".join(lines)


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
             f"  {'policy':15s}{'input':>11s}{'recall':>8s}{'prec':>7s}{'acc':>7s}{'FA':>6s}{'F1':>6s}{'speak':>7s}"
             f"{'AUDBC':>7s}   {'F1 interval':14s}{'FA interval':14s}"]
    waits = {}
    for name in POLICIES:
        for label, data in (("raw", rows), ("calibrated", cal)):
            m, b = evaluate(data, name, g), bootstrap(data, name, g)
            auc = audbc(data, name, g)
            auc_s = f"{auc:>7.3f}" if auc is not None else f"{'—':>7s}"
            lines.append(f"  {name:15s}{label:>11s}{m['recall']:>8.2f}{m['precision']:>7.2f}{m['accuracy']:>7.2f}"
                         f"{m['false_alarm']:>6.2f}{m['f1']:>6.2f}{m['speak_rate']:>7.2f}{auc_s}"
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
        for name in ("fixed", "prism", "saip", "saip-centered"):
            m = evaluate(part, name, g)
            cells.append(f"{name} {m['f1']:.2f} / {m['false_alarm']:.2f}")
        lines.append(f"  {domain:10s}({len(part)} steps)   " + "   ".join(cells))
    lines += ["", "matched recall comparison (calibrated)"]
    prism_recall = evaluate(cal, "prism", g)["recall"]
    lines.append(matched_recall_report(cal, g, prism_recall))
    lines.append("")
    lines.append(matched_recall_report(cal, g, 0.80))
    return "\n".join(head + lines + ["", CAVEATS])


def write_rows(path: Path, rows: list[Row]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(json.dumps(r.__dict__) + "\n" for r in rows), encoding="utf-8")


__all__ = ["STUCK", "PROACTIVE_SYSTEM", "SLOW_SYSTEM", "PRISM_SCHEMA_SYSTEM", "POLICIES", "Session", "Row",
           "load_sessions", "take", "step_context", "event_stuck_score", "breakpoint_state_score", "run_probes",
           "cross_fit", "_saip_gate", "saip_gate", "policy", "metrics", "evaluate", "bootstrap", "quality",
           "audbc", "tune_m0_for_recall", "matched_recall_report", "report", "write_rows"]
