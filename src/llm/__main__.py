"""python -m llm replay | gate | bench

  replay   run synthetic days through the probes and the gate and report (needs an endpoint, or --mock)
  gate     print the bar tau for a grid of stuck scores, to see what the costs do
  bench    run the policies, SAIP's gate included, on ProactiveBench sessions and report (endpoint, or --mock)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import bench
from .calibration import load_temperatures, save_temperatures
from .client import LLMError, OpenAIChatClient
from .config import load_settings, user_config_dir
from .gate import decide, threshold
from .replay import DEV_USERS, TEST_USERS, MockClient, fit_temps, report, run_replay, write_rows


def cmd_replay(a) -> int:
    settings = load_settings(a.config)
    users = {"dev": DEV_USERS, "test": TEST_USERS, "all": range(1, 21)}[a.split]
    cal_path = Path(a.calibration) if a.calibration else user_config_dir() / "calibration.json"
    temps = load_temperatures(cal_path)
    if a.mock:
        client, note = MockClient(), "MOCK backend: pipeline check only, says nothing about a real model"
    else:
        try:
            client, note = OpenAIChatClient(settings.llm), f"endpoint {settings.llm.base_url} model {settings.llm.model}"
        except ValueError as e:
            print(f"error: {e}\n(use --mock to run the pipeline without a model)", file=sys.stderr)
            return 2
    print(note)
    try:
        rows = run_replay(client, settings, temps, Path(a.data), users, not a.no_hooks, a.stride, a.max_assess)
    except LLMError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if a.fit:
        if a.split == "test":
            print("refusing to fit temperatures on the test people; use --split dev", file=sys.stderr)
            return 2
        temps = fit_temps(rows)
        save_temperatures(cal_path, temps)
        print(f"fitted T_need = {temps['need']:.2f}, T_accept = {temps['accept']:.2f}, saved to {cal_path}\n"
              "note: the generator has no acceptance label, so T_accept was fitted on the stuck label as a stand-in")
    print(report(rows, settings, temps))
    if a.out:
        write_rows(Path(a.out), rows)
        print(f"\nper-tick rows written to {a.out}")
    return 0


def cmd_gate(a) -> int:
    g = load_settings(a.config).gate
    print(f"p_need = {a.p_need}   c0:m0 = {g.c0}:{g.m0}   alpha = {g.alpha:.2f}   beta = {g.beta:.2f}")
    print(f"{'stuck s':>8s}{'tau':>8s}   action at p_accept = {a.p_accept}")
    for s in (0.0, 0.04, 0.1, 0.25, 0.45, 0.7, 1.0):
        d = decide(s, a.p_need, a.p_accept, g)
        print(f"{s:8.2f}{threshold(s, a.p_need, g):8.2f}   {d.action}")
    return 0


def cmd_bench(a) -> int:
    settings = load_settings(a.config)
    if a.mock:
        client, model = MockClient(), "mock"
    else:
        try:
            client, model = OpenAIChatClient(settings.llm), settings.llm.model
        except ValueError as e:
            print(f"error: {e}\n(use --mock to run the pipeline without a model)", file=sys.stderr)
            return 2
    sessions = bench.load_sessions(Path(a.data))
    if not sessions:
        print(f"error: no sessions found in {a.data}", file=sys.stderr)
        return 2
    if a.limit:
        sessions = bench.take(sessions, a.limit)
    t0 = time.time()

    def progress(done, total):
        if done % 10 == 0 or done == total:
            print(f"steps {done}/{total}  {time.time() - t0:.0f}s", file=sys.stderr)

    skipped: list = []
    rows = bench.run_probes(client, sessions, Path(a.cache), a.history, progress, model, skipped,
                            prompt_mode=a.prompt_mode, slow_margin=a.slow_margin, gate=settings.gate)
    for name, i, err in skipped:
        print(f"skipped {name} step {i}: {err}", file=sys.stderr)
    print(bench.report(rows, settings, len(skipped), model, a.mock))
    if a.out:
        bench.write_rows(Path(a.out), rows)
        print(f"\nper-step rows written to {a.out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m llm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay", help="replay synthetic days through the probes and the gate")
    r.add_argument("--data", default="data/synthetic")
    r.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    r.add_argument("--stride", type=float, default=120.0, help="seconds between sampled ticks")
    r.add_argument("--max-assess", type=int, default=200, help="cap on assessments (each is 2-3 LLM calls)")
    r.add_argument("--no-hooks", action="store_true", help="window, input and AFK only")
    r.add_argument("--fit", action="store_true", help="fit and save calibration temperatures (dev people only)")
    r.add_argument("--mock", action="store_true", help="deterministic stand-in for the model")
    r.add_argument("--calibration", help="where temperatures are read and saved")
    r.add_argument("--config", help="path to private.toml")
    r.add_argument("--out", help="write per-tick rows as jsonl")
    r.set_defaults(fn=cmd_replay)
    g = sub.add_parser("gate", help="show the bar for a grid of stuck scores")
    g.add_argument("--p-need", type=float, default=0.6)
    g.add_argument("--p-accept", type=float, default=0.5)
    g.add_argument("--config")
    g.set_defaults(fn=cmd_gate)
    b = sub.add_parser("bench", help="score the gate and other policies on ProactiveBench sessions")
    b.add_argument("--data", default="ProactiveAgent/dataset/test_data")
    b.add_argument("--mock", action="store_true", help="deterministic stand-in for the model")
    b.add_argument("--cache", default="data/bench/probes.jsonl", help="probe results, so a run can resume")
    b.add_argument("--history", type=int, default=15, help="how many recent events the model sees")
    b.add_argument("--out", default="data/bench/rows.jsonl", help="write per-step rows as jsonl")
    b.add_argument("--config", help="path to private.toml")
    b.add_argument("--limit", type=int, default=0, help="probe only the first N steps")
    b.add_argument("--prompt-mode", choices=["saip", "proactive", "prism-schema"], default="saip",
                   help="saip (default), proactive, or prism-schema (ProactiveBench-native schema)")
    b.add_argument("--slow-margin", type=float, default=0.0,
                   help="if > 0, run a counterfactual reasoning pass when |p_accept - tau| <= margin")
    b.set_defaults(fn=cmd_bench)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
