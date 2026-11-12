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


def test_report_counts_wait(data):
    rows = rows_for(LABELS)
    text = bench.report(rows, load_settings(None), skipped=2, model="x")
    assert "skipped: 2" in text and "model: x" in text and "answered WAIT" in text
