# SAIP

Proactive agent research project: detect whether a user is in **flow** or **stuck**
from background desktop telemetry, and interrupt only when it is worth it.
See `ProActive_Agent_Implementation_Plan.md` for the five-phase roadmap.

## Layout

```
synth/                     synthetic ActivityWatch day generator (Phase 1 fixtures)
  synth/                   the package: world, styles, generator, noise, writer, summary
  tests/                   pytest suite for the generator
ProActive_Agent_Implementation_Plan.md
```

## Running

The generator emits ActivityWatch-shaped events plus ground-truth labels, so the
Phase 2 classifier can be developed before real data is available.

```bash
pip install -r requirements.txt

# one synthetic day (run from synth/, the package root)
cd synth && python -m synth --seed 1 --out ../data/synthetic/day_seed1

# the shared dataset: 20 people, 10 working days each, in data/synthetic (from the repo root)
python synth/generate_dataset.py

# tests (from the repo root)
pytest
```

Each generated day contains `window/afk/input/web/terminal/fs` jsonl streams,
`labels.jsonl` (state segments, ground truth only), `help_requests.jsonl`,
`manifest.json` and an importable `aw-export.json`.

Each seed is a new person by default. To simulate one person across days, keep `--user`
fixed and change `--seed` and `--date`:

```bash
cd synth && python -m synth --user 7 --seed 1 --date 2026-11-04 --out ../data/synthetic/user7/day1
cd synth && python -m synth --user 7 --seed 2 --date 2026-11-05 --out ../data/synthetic/user7/day2
```

Behaviours a loader or classifier has to cope with, as in real exports:

- `aw-export.json` stores events newest first, so sort before use.
- Web events keep counting while Chrome is in the background. Intersect them with Chrome window
  events to get time actually spent on a page.
- A sleeping laptop leaves gaps in window, AFK and web, while input keeps reporting zeros.
- Opening a chatbot is not a stuck signal: `help_requests.jsonl` includes brainstorming and quick looks.
- `terminal` and `fs` come from custom hooks and do not exist in a stock ActivityWatch install.

Bucket ids mirror the real watchers (`aw-watcher-window_<host>`), so one loader
reads both real and synthetic exports. Match buckets by **type**, not id: a
hostname change on a real machine creates a second set of buckets.

## Not in the repo

`aw-buckets-export.json` (a real ActivityWatch export) is gitignored on purpose —
it is personal telemetry. Drop your own export next to the generator output
locally and it stays out of git.
