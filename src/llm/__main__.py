"""python -m llm replay | gate

  replay   run synthetic days through the probes and the gate and report (needs an endpoint, or --mock)
  gate     print the bar tau for a grid of stuck scores, to see what the costs do
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

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
        rows = run_replay(client, settings, temps, Path(a.data), users, not a.no_hooks, a.stride, a.max_assess,
                          all_ticks=a.all_ticks)
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
    r.add_argument("--all-ticks", action="store_true",
                   help="assess every sampled tick, not only those above the classifier's trigger (for `python -m llm.compare`)")
    r.set_defaults(fn=cmd_replay)
    g = sub.add_parser("gate", help="show the bar for a grid of stuck scores")
    g.add_argument("--p-need", type=float, default=0.6)
    g.add_argument("--p-accept", type=float, default=0.5)
    g.add_argument("--config")
    g.set_defaults(fn=cmd_gate)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
