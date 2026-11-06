"""CLI: python -m synth --seed 1 [--date 2026-11-04] [--out data/synthetic/day_seed1]"""
from __future__ import annotations

import argparse
from datetime import date

from .generator import generate_day
from .summary import format_table, summarize
from .writer import write_day


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate one synthetic ActivityWatch day.")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--date", type=date.fromisoformat, default=date(2026, 11, 4))
    ap.add_argument("--tz-offset", type=float, default=-7.0, help="local UTC offset in hours (Phoenix = -7)")
    ap.add_argument("--start-hour", type=float, default=9.0)
    ap.add_argument("--end-hour", type=float, default=17.5)
    ap.add_argument("--noise", type=float, default=1.0,
                    help="background noise level: 0 clean, 1 default, 2 heavy")
    ap.add_argument("--user", type=int, default=None,
                    help="keep one person across days: same style, projects and documents; --seed still varies the day")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    result = generate_day(args.seed, args.date, args.tz_offset, args.start_hour, args.end_hour,
                          args.noise, args.user)
    out = write_day(result, args.out or f"data/synthetic/day_seed{args.seed}")
    print(f"wrote {out}")
    print(format_table(summarize(result)))


if __name__ == "__main__":
    main()
