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

# tests (from the repo root)
pytest
```

Each generated day contains `window/afk/input/web/terminal/fs` jsonl streams,
`labels.jsonl` (state segments, ground truth only), `help_requests.jsonl`,
`manifest.json` and an importable `aw-export.json`.

Bucket ids mirror the real watchers (`aw-watcher-window_<host>`), so one loader
reads both real and synthetic exports. Match buckets by **type**, not id: a
hostname change on a real machine creates a second set of buckets.

## Not in the repo

`aw-buckets-export.json` (a real ActivityWatch export) is gitignored on purpose —
it is personal telemetry. Drop your own export next to the generator output
locally and it stays out of git.
