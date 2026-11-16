import json
import math
from dataclasses import replace

import numpy as np
import pytest

from llm import bench
from llm.__main__ import main
from llm.bench import Row, Session
from llm.client import Choice, LLMError
from llm.config import GateParams, load_settings
from llm.gate import threshold

G = GateParams()


def write_session(folder, name, labels, t0=1000.0):
    steps = [{"observation": {"time": str(t0 + 10.0 * i), "event": f"{name} event {i}"},
              "agent_response": {"candidate_task": []}, "task_status": y} for i, y in enumerate(labels)]
    (folder / f"{name}.json").write_text(json.dumps(steps), encoding="utf-8")


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    write_session(d, "code_1", [False, True, False, True, False, False])
    write_session(d, "writing_2", [True, False, False, True, False])
    write_session(d, "writing_3", [False, False, True, False])
    (d / "splits.json").write_text(json.dumps({"train": ["x"]}), encoding="utf-8")
    (d / "notes.json").write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    return d


class Scripted:
    """Answers by question; p depends on the step number in the newest event so rows vary. Can fail on a marker."""

    def __init__(self, fail=None, fail_times=0):
        self.calls, self.fail, self.fail_times = 0, fail, fail_times

    def complete(self, messages, *, max_tokens=1, temperature=0.0, logprobs=False, top_logprobs=10, n=1):
        self.calls += 1
        text = messages[-1]["content"]
        if self.fail and self.fail in text and self.fail_times > 0:
            self.fail_times -= 1
            raise LLMError("boom")
        newest = int(text.split("\n\n")[0].splitlines()[-1].split(" event ")[1])
        base = 0.2 + 0.1 * (newest % 5)
        p = base if "need help" in text else base + 0.1 if "accept" in text else base - 0.1
        return [Choice("Yes", {"Yes": math.log(p), "No": math.log(1 - p)})]


def rows_for(sessions_labels):
    rng = np.random.default_rng(3)
    out = []
    for name, ys in sessions_labels.items():
        for i, y in enumerate(ys):
            out.append(Row(name, "code", i, y, float(rng.uniform(0.05, 0.95)), float(rng.uniform(0.05, 0.95)),
                           float(rng.uniform(0, 1))))
    return out


LABELS = {"a": [True, False, True, False, False, True], "b": [False, True, False, False, True],
          "c": [True, True, False, False, False, False], "d": [False, False, True, False, True]}


def test_load_sessions(data):
    sessions = bench.load_sessions(data)
    assert [s.name for s in sessions] == ["code_1", "writing_2", "writing_3"]
    assert [s.domain for s in sessions] == ["code", "writing", "writing"]
    assert [y for _, _, y in sessions[0].steps] == [False, True, False, True, False, False]
    assert sessions[0].steps[1] == (1010.0, "code_1 event 1", True)


def test_step_context_has_no_future_and_newest_last():
    s = Session("code_1", "code", [(100.0 + 10 * i, f"event {i}", i == 3) for i in range(8)])
    ctx = bench.step_context(s, 3)
    lines = ctx.splitlines()
    assert lines[0] == "Activity (oldest first):"
    assert lines[1:] == ["- 30s ago: event 0", "- 20s ago: event 1", "- 10s ago: event 2", "- 0s ago: event 3"]
    assert "event 4" not in ctx and "True" not in ctx
    short = bench.step_context(s, 6, history=3).splitlines()
    assert short[1:] == ["- 20s ago: event 4", "- 10s ago: event 5", "- 0s ago: event 6"]


def test_run_probes_three_per_step_and_cache(data, tmp_path):
    sessions = bench.load_sessions(data)
    cache = tmp_path / "c" / "probes.jsonl"
    client, seen = Scripted(), []
    rows = bench.run_probes(client, sessions, cache, model="m", progress=lambda d, t: seen.append((d, t)))
    assert len(rows) == 15 and client.calls == 45 and seen[-1] == (15, 15)
    again = Scripted()
    rows2 = bench.run_probes(again, sessions, cache, model="m")
    assert again.calls == 0 and rows2 == rows
    assert all(json.loads(line)["model"] == "m" for line in cache.read_text().splitlines())


def test_partial_cache_probes_only_missing(data, tmp_path):
    sessions = bench.load_sessions(data)
    cache = tmp_path / "probes.jsonl"
    bench.run_probes(Scripted(), bench.take(sessions, 4), cache, model="m")
    client = Scripted()
    rows = bench.run_probes(client, sessions, cache, model="m")
    assert len(rows) == 15 and client.calls == 3 * 11
    assert bench.run_probes(Scripted(), sessions, cache, model="other")[0].session == "code_1"


def test_llm_error_retried_once_then_skipped(data, tmp_path):
    sessions = bench.load_sessions(data)
    once = Scripted(fail="code_1 event 2", fail_times=1)
    skipped: list = []
    assert len(bench.run_probes(once, sessions, None, skipped=skipped)) == 15 and skipped == []
    twice = Scripted(fail="code_1 event 2", fail_times=2)
    rows = bench.run_probes(twice, sessions, tmp_path / "p.jsonl", skipped=skipped)
    assert len(rows) == 14 and [(n, i) for n, i, _ in skipped] == [("code_1", 2)]
    assert "boom" in skipped[0][2]


def test_cross_fit_has_no_leakage():
    rows = rows_for(LABELS)
    base = bench.cross_fit(rows)

    def flip(session):
        return [replace(r, y=not r.y) if r.session == session else r for r in rows]

    own = bench.cross_fit(flip("a"))
    assert [(r.p_need, r.p_accept) for r in own if r.session == "a"] == [(r.p_need, r.p_accept) for r in base
                                                                          if r.session == "a"]
    other = bench.cross_fit(flip("b"))
    assert [r.p_need for r in other if r.session == "a"] != [r.p_need for r in base if r.session == "a"]
    assert [r.s for r in base] == [r.s for r in rows]


def test_prism_matches_gate_threshold_and_saip_reduces_to_it():
    flat = replace(G, alpha=0.0, beta=0.0)
    flat_nodelta = replace(flat, delta=0.0)
    grid = [0.01, 0.1, 0.3, 0.5, 0.7, 0.9, 0.99]
    for s in (0.0, 0.4, 1.0):
        for pn in grid:
            for pa in grid:
                r = Row("x", "code", 0, True, pn, pa, s)
                assert bench.policy("prism", r, G) == (pa >= threshold(s, pn, flat))
                assert bench.policy("prism", r, G) == bench.policy("saip", r, flat_nodelta)
                assert bench.policy("static-cost", r, G) == (pa >= G.c0 / (G.c0 + G.m0))
    assert bench.policy("always", Row("x", "code", 0, False, 0.0, 0.0, 0.0), G)


def test_metrics_hand_made_confusion():
    y = [True] * 6 + [False] * 4
    spoke = [True, True, True, True, False, False, True, True, False, False]      # tp 4, fn 2, fp 2, tn 2
    m = bench.metrics(y, spoke)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (4, 2, 2, 2)
    assert m["recall"] == pytest.approx(4 / 6) and m["precision"] == pytest.approx(4 / 6)
    assert m["false_alarm"] == pytest.approx(1 - m["precision"]) and m["f1"] == pytest.approx(4 / 6)
    assert m["accuracy"] == pytest.approx(0.6) and m["speak_rate"] == pytest.approx(0.6)
    never = bench.metrics(y, [False] * 10)
    assert never["precision"] == never["false_alarm"] == never["f1"] == never["recall"] == never["speak_rate"] == 0.0


def test_bootstrap_is_deterministic_and_contains_the_estimate():
    rows = bench.cross_fit(rows_for(LABELS))
    a, b = bench.bootstrap(rows, "fixed", G, n=300, seed=5), bench.bootstrap(rows, "fixed", G, n=300, seed=5)
    assert a == b
    point = bench.evaluate(rows, "always", G)
    ci = bench.bootstrap(rows, "always", G, n=300)
    assert ci["f1"][0] <= point["f1"] <= ci["f1"][1]
    assert ci["false_alarm"][0] <= point["false_alarm"] <= ci["false_alarm"][1]


def test_cli_mock_runs_and_reports(data, tmp_path, capsys):
    out, cache = tmp_path / "rows.jsonl", tmp_path / "probes.jsonl"
    code = main(["bench", "--mock", "--data", str(data), "--cache", str(cache), "--out", str(out)])
    text = capsys.readouterr()
    assert code == 0
    assert text.out.splitlines()[0].startswith("THESE NUMBERS ARE FROM A STAND-IN")
    for name in bench.POLICIES:
        assert name in text.out
    assert "Read this before quoting numbers" in text.out and "sessions: 3   steps: 15" in text.out
    assert len(out.read_text().splitlines()) == 15 and "steps 10/15" in text.err
    assert main(["bench", "--mock", "--data", str(data), "--cache", str(cache), "--out", str(out), "--limit", "4"]) == 0
    assert len(out.read_text().splitlines()) == 4
    # Test CLI with --prompt-mode prism-schema
    out_prism, cache_prism = tmp_path / "rows_prism.jsonl", tmp_path / "probes_prism.jsonl"
    code_prism = main(["bench", "--mock", "--data", str(data), "--cache", str(cache_prism),
                       "--out", str(out_prism), "--prompt-mode", "prism-schema", "--limit", "3"])
    assert code_prism == 0
    assert len(out_prism.read_text().splitlines()) == 3


def test_report_counts_wait(data):
    rows = rows_for(LABELS)
    text = bench.report(rows, load_settings(None), skipped=2, model="x")
    assert "skipped: 2" in text and "model: x" in text and "answered WAIT" in text and "AUDBC" in text


def test_audbc_bounds_and_non_cost_policies():
    rows = rows_for(LABELS)
    assert bench.audbc(rows, "fixed", G) is None
    for name in ("static-cost", "prism", "saip"):
        score = bench.audbc(rows, name, G)
        assert score is not None and 0.0 <= score <= 1.0


def test_proactive_prompt_and_slow_margin_cache_separately(data, tmp_path):
    sessions = bench.take(bench.load_sessions(data), 3)
    cache = tmp_path / "probes.jsonl"
    c1 = Scripted()
    r1 = bench.run_probes(c1, sessions, cache, model="m", prompt_mode="proactive")
    assert len(r1) == 3 and c1.calls == 9
    c2 = Scripted()
    r2 = bench.run_probes(c2, sessions, cache, model="m", prompt_mode="proactive", slow_margin=1.0)
    assert len(r2) == 3 and c2.calls == 18   # 3 fast + (1 thought + 2 re-probes) per step when margin=1.0


def test_event_stuck_score():
    smooth_steps = [
        (100.0, "User types next line of code", False),
        (110.0, "User creates new function", False),
        (120.0, "User runs clean tests successfully", False),
    ]
    s_smooth = Session("test_smooth", "code", smooth_steps)
    assert bench.event_stuck_score(s_smooth, 2) == 0.0

    troubled_steps = [
        (100.0, "User switches to 'Code.exe' without performing any actions", False),
        (110.0, "The user remains on 'Code.exe' idle", False),
        (120.0, "User opens browser and searches stack overflow for tutorial", False),
        (130.0, "User encounters error and exception in terminal", True),
    ]
    s_troubled = Session("test_troubled", "code", troubled_steps)
    score = bench.event_stuck_score(s_troubled, 3)
    assert 0.0 < score <= 1.0
    assert score >= 0.4

    heavy_steps = [
        (100.0 + 10 * k, f"error fail exception bug debug issue on 'file.py' without performing idle search google {k}", True)
        for k in range(10)
    ]
    s_heavy = Session("test_heavy", "code", heavy_steps)
    assert bench.event_stuck_score(s_heavy, 9) == 1.0

    old_steps = [
        (100.0, "fatal error fail exception", True),
        (110.0, "without performing idle", True),
    ] + [(120.0 + 10 * k, f"smooth action {k}", False) for k in range(8)]
    s_old = Session("test_old", "code", old_steps)
    assert bench.event_stuck_score(s_old, 9) == 0.0


class PrismScriptedClient:
    def __init__(self, propose_task=True, malformed_json=False):
        self.calls = 0
        self.propose_task = propose_task
        self.malformed_json = malformed_json

    def complete(self, messages, *, max_tokens=1, temperature=0.0, logprobs=False, top_logprobs=10, n=1):
        self.calls += 1
        sys_content = messages[0]["content"] if messages else ""
        user_content = messages[-1]["content"] if messages else ""

        if sys_content == bench.PRISM_SCHEMA_SYSTEM or max_tokens > 10:
            if self.malformed_json:
                return [Choice("I think the user needs assistance with debugging.")]
            task = "Assist with fixing failing tests" if self.propose_task else None
            data = {
                "Purpose": "Fixing a bug in test",
                "Thoughts": "The user saw an error in terminal",
                "Proactive Task": task,
                "Response": "I can help fix the error",
            }
            return [Choice(f"```json\n{json.dumps(data)}\n```")]

        p = 0.8 if "need help" in user_content else 0.6 if "accept" in user_content else 0.4
        return [Choice("Yes", {"Yes": math.log(p), "No": math.log(1 - p)})]


def test_prism_schema_prompt_mode(data, tmp_path):
    sessions = bench.load_sessions(data)[:2]

    cache1 = tmp_path / "prism1.jsonl"
    c1 = PrismScriptedClient(propose_task=True)
    rows1 = bench.run_probes(c1, sessions, cache1, model="m", prompt_mode="prism-schema")
    assert len(rows1) == 11
    assert c1.calls == 11 * 4
    assert rows1[0].p_accept == pytest.approx(0.6)

    cache2 = tmp_path / "prism2.jsonl"
    c2 = PrismScriptedClient(propose_task=False)
    rows2 = bench.run_probes(c2, sessions, cache2, model="m", prompt_mode="prism-schema")
    assert len(rows2) == 11
    assert rows2[0].p_accept == pytest.approx(0.3)

    cache3 = tmp_path / "prism3.jsonl"
    c3 = PrismScriptedClient(malformed_json=True)
    rows3 = bench.run_probes(c3, sessions, cache3, model="m", prompt_mode="prism-schema")
    assert len(rows3) == 11


def test_saip_gate_centering_and_matched_recall_report():
    flat = replace(G, alpha=0.0, beta=0.0)
    assert bench._saip_gate(flat).m0 == pytest.approx(G.m0)

    centered = bench._saip_gate(G, s_center=0.35)
    expected_m0 = G.m0 * math.exp(G.alpha * (1.0 - 0.35) - G.beta * 0.35)
    assert centered.m0 == pytest.approx(expected_m0)

    rows = rows_for(LABELS)
    rep = bench.matched_recall_report(rows, G, target_recall=0.75)
    assert "matched recall target: 0.75" in rep
    assert "prism" in rep and "saip" in rep and "saip-centered" in rep

    full_report = bench.report(rows, load_settings(None))
    assert "saip-centered" in full_report
    assert "matched recall comparison (calibrated)" in full_report


def test_breakpoint_state_score_and_session_decisions():
    steps = [
        (100.0, "User scrolls through search results", False),
        (110.0, "The user remained on the page idle", False),
        (120.0, "The user switched to window without performing any action", False),
        (130.0, "User pressed ctrl_l and cmdv rapidly", False),
    ]
    s_text = Session("sess1", "writing", steps)
    sc0 = bench.breakpoint_state_score(s_text, 0, 0.5)
    assert sc0 == pytest.approx(0.90)

    sc1 = bench.breakpoint_state_score(s_text, 1, 0.5)
    assert sc1 >= 0.70

    # "without performing" / "switched to" lowers receptivity
    sc2 = bench.breakpoint_state_score(s_text, 2, 0.5)
    assert sc2 <= 0.05

    # raw keystroke bursts like ctrl_l and cmdv lower s
    sc3 = bench.breakpoint_state_score(s_text, 3, 0.5)
    assert sc3 < 0.20

    s_code = Session("sess_code", "code", steps)
    sc_code = bench.breakpoint_state_score(s_code, 0, 0.1)
    assert sc_code == pytest.approx(0.80 * 0.90 + 0.20 * 0.1)

    r0 = Row("sA", "code", 0, True, 0.60, 0.82, 0.10)
    r1 = Row("sA", "code", 1, True, 0.10, 0.10, 0.60)
    r2 = Row("sA", "code", 2, True, 0.60, 0.82, 0.40)
    rows = [r0, r1, r2]

    # Individually, r1 alone would stay quiet, r2 alone would speak
    assert bench.policy("saip", r1, G) is False
    assert bench.policy("saip", r2, G) is True

    # Under session-aware _decisions:
    # - r0 waits in flow
    # - r1 speaks at breakpoint due to carried-forward pending proposal
    # - r2 cools down post-SPEAK (s_eff = 0.15) and stays quiet / waits
    spoke, waits = bench._decisions(rows, "saip", G)
    assert spoke == [False, True, False]
    assert waits == 2
