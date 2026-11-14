"""llm.compare: PRISM's gate and SAIP's gate on the same ticks, in PRISM's metrics."""
import json

from llm import compare
from llm.config import GateParams
from llm.replay import MockClient, Row, run_replay  # noqa: F401  (Row gained `theta`)

TEMPS = {"need": 1.0, "accept": 1.0}


def row(state, s, p_need, p_accept, theta=0.19, user=1, t=0.0):
    return {"user": user, "t": t, "state": state, "stuck_score": s, "p_need_raw": p_need, "p_accept_raw": p_accept,
            "action": "quiet", "tau": 0.5, "resampled": False, "calls": 2, "theta": theta}


def test_metrics_use_proactivebench_definitions():
    rows = [row("stuck", 0.8, 0.9, 0.9), row("stuck", 0.8, 0.9, 0.9), row("stuck", 0.8, 0.1, 0.1),
            row("flow", 0.1, 0.9, 0.9), row("neutral", 0.1, 0.1, 0.1)]
    t = compare.Ticks(rows, TEMPS)
    spoke = t.p_accept >= 0.5                                   # TP 2, FP 1, FN 1, TN 1
    m = compare.metrics(spoke, t)
    assert m["speak"] == 3
    assert abs(m["recall"] - 2 / 3) < 1e-6 and abs(m["precision"] - 2 / 3) < 1e-6
    assert abs(m["accuracy"] - 3 / 5) < 1e-6
    assert abs(m["false_alarm"] - 1 / 3) < 1e-6                 # FP / (TP + FP): 1 - precision, not the FPR
    assert abs(m["fpr"] - 1 / 2) < 1e-6
    assert abs(m["flow_interrupted"] - 1.0) < 1e-6              # the one flow tick was spoken to


def test_prism_rows_use_every_tick_and_saip_rows_need_the_trigger():
    quiet_but_keen = row("stuck", 0.05, 0.95, 0.95)             # below the trigger: SAIP never asks the model
    t = compare.Ticks([quiet_but_keen, row("stuck", 0.9, 0.95, 0.95)], TEMPS)
    s = compare.systems(t, GateParams())
    assert s["PRISM gate, every tick"].tolist() == [True, True]
    assert s["SAIP: trigger + state-aware gate"].tolist() == [False, True]
    assert s["  state-aware gate, no trigger"].tolist() == [True, True]
    assert s["always speak"].all()


def test_zero_slopes_make_the_two_gates_identical():
    rows = [row("stuck" if i % 3 == 0 else "flow", i / 20, 0.3 + i / 40, 0.2 + i / 30, theta=0.0) for i in range(20)]
    t = compare.Ticks(rows, TEMPS)
    s = compare.systems(t, GateParams(alpha=0.0, beta=0.0))
    assert (s["PRISM gate, every tick"] == s["SAIP: trigger + state-aware gate"]).all()


def test_a_higher_miss_cost_never_lowers_recall_and_matching_finds_it():
    rows = [row("stuck" if i % 2 else "flow", 0.5, 0.5, i / 40, theta=0.0) for i in range(40)]
    t = compare.Ticks(rows, TEMPS)
    g = GateParams()
    name = "PRISM gate, every tick"
    recalls = [compare.metrics(compare.systems(t, GateParams(c0=1.0, m0=m0))[name], t)["recall"] for m0 in (0.5, 2, 8, 32)]
    assert recalls == sorted(recalls)
    m0, m = compare.at_recall(name, t, g, 0.8)
    assert m["recall"] >= 0.8
    assert compare.at_recall("SAIP: trigger + state-aware gate", compare.Ticks([row("stuck", 0.0, 0.9, 0.9)], TEMPS), g, 0.5) is None


def test_bootstrap_resamples_whole_days_and_the_report_runs(tmp_path):
    rows = [row("stuck" if i % 4 == 0 else "flow", 0.1 + (i % 10) / 12, 0.4 + (i % 5) / 10, (i % 7) / 7,
                user=1 + i % 3, t=86400.0 * (i % 6) + i) for i in range(120)]
    t = compare.Ticks(rows, TEMPS)
    assert len(set(t.day)) == 6                             # 3 users x 2 days each: the resampling units
    s = compare.systems(t, GateParams())
    ci = compare.bootstrap(s["PRISM gate, every tick"], s["SAIP: trigger + state-aware gate"], t, n=200)
    assert all(lo <= hi for lo, hi in ci.values())
    path = tmp_path / "rows.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    text = compare.report(compare.load_rows(path), GateParams(), TEMPS, n_boot=100)
    assert "PRISM gate, every tick" in text and "matched recall" in text and "WARNING" not in text
    only_triggered = [r for r in rows if r["stuck_score"] >= r["theta"]]
    assert "WARNING" in compare.report(only_triggered, GateParams(), TEMPS, n_boot=0)
    assert compare.main([str(path), "--bootstrap", "0", "--calibration", str(tmp_path / "none.json")]) == 0


def test_rows_written_before_theta_existed_still_load():
    old = row("stuck", 0.5, 0.9, 0.9)
    del old["theta"]
    assert compare.Ticks([old], TEMPS).triggered.all()


def test_the_systems_behind_the_trigger_use_their_own_calibration(tmp_path):
    rows = [row("stuck", 0.9, 0.3, 0.3), row("flow", 0.9, 0.3, 0.3)]          # both past the trigger
    flat, raised = {"need": 1.0, "accept": 1.0}, {"need": 4.0, "accept": 4.0}  # T > 1 pulls 0.3 towards 0.5
    one = compare.systems(compare.Ticks(rows, flat), GateParams())
    two = compare.systems(compare.Ticks(rows, flat, raised), GateParams())
    assert not one["SAIP: trigger + state-aware gate"].any()
    assert two["SAIP: trigger + state-aware gate"].all()
    assert (one["PRISM gate, every tick"] == two["PRISM gate, every tick"]).all()      # untouched
    assert (one["  state-aware gate, no trigger"] == two["  state-aware gate, no trigger"]).all()
    path, cal = tmp_path / "rows.jsonl", tmp_path / "saip.json"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    cal.write_text(json.dumps(raised), encoding="utf-8")
    assert "note: one calibration" in compare.report(rows, GateParams(), flat, n_boot=0)
    assert "note: one calibration" not in compare.report(rows, GateParams(), flat, n_boot=0, saip_temps=raised)
    assert compare.main([str(path), "--bootstrap", "0", "--calibration", str(tmp_path / "none.json"),
                         "--saip-calibration", str(cal)]) == 0
