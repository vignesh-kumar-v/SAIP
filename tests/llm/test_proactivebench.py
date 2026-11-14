"""llm.proactivebench: SAIP's questions and PRISM's gate on ProactiveBench's recorded sessions."""
import json

import numpy as np

from llm import proactivebench as pb
from llm.config import GateParams
from llm.replay import MockClient


def fake_pa(tmp_path):
    """A tiny ProactiveAgent folder in the real file layout."""
    test = tmp_path / "dataset" / "test_data"
    test.mkdir(parents=True)
    (test / "splits.json").write_text("{}", encoding="utf-8")
    for name, flags in (("code_11", [True, False, True]), ("writing_11", [False, False, True, False])):
        events = [{"observation": {"time": str(1000.0 + 30 * i), "event": f"{name} event {i}"},
                   "agent_response": {"candidate_task": []}, "task_status": f} for i, f in enumerate(flags)]
        (test / f"{name}.json").write_text(json.dumps(events), encoding="utf-8")
    reward = tmp_path / "dataset" / "reward_data"
    reward.mkdir(parents=True)
    rows = [{"obs": [{"time": "1000.0", "event": "code_11 event 0"}], "help_needed": True, "category": "Correct-Detection (CD)"},
            {"obs": [{"time": "Day 1, 10:05 PM", "event": "something generated"}], "help_needed": False,
             "category": "Correct-Rejection (CR)"}]
    (reward / "test_data.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return tmp_path


def test_context_shows_events_so_far_with_relative_times():
    text = pb.build_context([{"time": "100", "event": "first"}, {"time": "190", "event": "second"}])
    assert text.splitlines() == ["Recent activity (oldest first, 2 of 2 events):",
                                 "- 1m 30s ago: first", "- 0s ago: second"]
    many = pb.build_context([{"time": str(i), "event": f"e{i}"} for i in range(40)])
    assert "15 of 40 events" in many and "e39" in many and "e24" not in many
    assert pb.build_context([{"time": "Day 1, 10:05 PM", "event": "x"}]).endswith("- x")      # no time when unparseable


def test_loading_keeps_order_history_labels_and_origin(tmp_path):
    items = pb.load_items(fake_pa(tmp_path), "both")
    sessions = [i for i in items if i["set"] == "sessions"]
    assert len(sessions) == 7 and [i["label"] for i in sessions[:3]] == [True, False, True]
    assert "event 1" in sessions[2]["context"] and "event 2" in sessions[2]["context"]       # history up to now
    assert "event 1" not in sessions[0]["context"]                                           # nothing from later
    assert {i["category"] for i in sessions} == {"coding", "writing"}
    reward = [i for i in items if i["set"] == "reward"]
    assert [i["origin"] for i in reward] == ["recorded", "generated"] and reward[0]["label"] is True


def test_run_appends_rows_and_resumes_without_repeating(tmp_path):
    items = pb.load_items(fake_pa(tmp_path), "sessions")
    out = tmp_path / "rows.jsonl"
    assert pb.run(MockClient(), items[:3], out, progress=lambda _m: None) == 3
    assert pb.run(MockClient(), items, out, progress=lambda _m: None) == 4                    # only the rest
    rows = pb.load_rows(out)
    assert [r["id"] for r in rows] == [i["id"] for i in items] and all(0 <= r["p_need_raw"] <= 1 for r in rows)


def test_calibration_never_sees_the_group_it_scores():
    rows = [{"group": g, "label": lab, "p_need_raw": p, "p_accept_raw": p}
            for g, lab, p in (("a", True, 0.9), ("a", False, 0.1), ("b", True, 0.6), ("b", False, 0.5))]
    need, accept, temps = pb.cross_fitted(rows)
    assert len(temps) == 2 and need.shape == (4,)
    from llm.calibration import calibrate, fit_temperature
    t_for_a = fit_temperature([0.6, 0.5], [True, False])                                       # fitted on b only
    assert abs(need[0] - calibrate(0.9, t_for_a)) < 1e-9


def test_gate_is_prisms_fixed_cost_gate():
    need, accept = np.array([0.9, 0.9, 0.1, 0.1]), np.array([0.9, 0.2, 0.9, 0.7])
    spoke = pb.gate(need, accept, GateParams())
    assert spoke.tolist() == [True, False, True, False]               # tau = 1 / (1 + 2 p_need): 0.36, 0.36, 0.83, 0.83


def test_report_and_command(tmp_path):
    pa = fake_pa(tmp_path)
    out = tmp_path / "rows.jsonl"
    assert pb.main(["run", "--pa", str(pa), "--out", str(out), "--mock"]) == 0
    text = pb.report(pb.load_rows(out), GateParams(), n_boot=50)
    assert "Recorded sessions" in text and "Reward-model test set" in text and "always speak" in text
    assert "By origin" in text and "By category" in text
    assert pb.main(["report", str(out), "--bootstrap", "0"]) == 0
    assert pb.main(["run", "--pa", str(tmp_path / "missing"), "--out", str(out), "--mock"]) == 2
