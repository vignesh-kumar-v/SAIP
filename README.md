# SAIP

Proactive agent research project: detect whether a user is in **flow** or **stuck**
from background desktop telemetry, and interrupt only when it is worth it.
See `docs/ProActive_Agent_Implementation_Plan.md` for the five-phase roadmap.

## Layout

```
src/                       importable packages, side by side
  synth/                     synthetic ActivityWatch day generator (Phase 1 fixtures): world, styles, generator, noise, writer, summary
  classifier/                stuck-state classifier (Phase 2): loader, categories, features, scorer, evaluate, weights.json
tests/                     mirrors src/
  synth/                     pytest suite for the generator
  classifier/                pytest suite for the classifier
scripts/
  generate_dataset.py        builds the shared multi-person dataset
docs/
  ProActive_Agent_Implementation_Plan.md
data/                      generated output (gitignored)
pyproject.toml             package metadata; `pip install -e .` puts synth and classifier on the path
pytest.ini  requirements.txt
```

Everything runs from the repo root. New components (bench, live agent, LLM layer) get their own
folder under `src/` and `tests/`.

## Running

The generator emits ActivityWatch-shaped events plus ground-truth labels, so the
Phase 2 classifier can be developed before real data is available.

```bash
pip install -r requirements.txt
pip install -e .          # once: makes `python -m synth` and `python -m classifier` work (or prefix with PYTHONPATH=src)

# one synthetic day
python -m synth --seed 1 --out data/synthetic/day_seed1

# the shared dataset: 20 people, 10 working days each, in data/synthetic
python scripts/generate_dataset.py

# tests
pytest
```

Each generated day contains `window/afk/input/web/terminal/fs` jsonl streams,
`labels.jsonl` (state segments, ground truth only), `help_requests.jsonl`,
`manifest.json` and an importable `aw-export.json`.

Each seed is a new person by default. To simulate one person across days, keep `--user`
fixed and change `--seed` and `--date`:

```bash
python -m synth --user 7 --seed 1 --date 2026-11-04 --out data/synthetic/user7/day1
python -m synth --user 7 --seed 2 --date 2026-11-05 --out data/synthetic/user7/day2
```

Behaviours a loader or classifier has to cope with, as in real exports:

- `aw-export.json` stores events newest first, so sort before use.
- Web events keep counting while Chrome is in the background. Intersect them with Chrome window
  events to get time actually spent on a page.
- A sleeping laptop leaves gaps in window, AFK and web, while input keeps reporting zeros.
- Coding has two gears: rapid hopping between editor, browser and terminal, and long deep stretches.
  Both are flow, so busy switching alone says little about being stuck.
- One person varies from day to day, for example directing an agent one day and hand-coding the next.
- Opening a chatbot is not a stuck signal: `help_requests.jsonl` includes brainstorming and quick looks.
- `terminal` and `fs` come from custom hooks and do not exist in a stock ActivityWatch install.

Bucket ids mirror the real watchers (`aw-watcher-window_<host>`), so one loader
reads both real and synthetic exports. Match buckets by **type**, not id: a
hostname change on a real machine creates a second set of buckets.

## Classifier

A hand-weighted score from 0 (flow) to 1 (stuck), updated every 5 s and smoothed over time. It needs
no training and no GPU. Run from the repo root:

```bash
# score any ActivityWatch export, real or synthetic
python -m classifier score --export aw-buckets-export.json --out scores.jsonl

# check the score against the synthetic labels; tune on dev, keep test for final checks
python -m classifier evaluate --split dev
python -m classifier evaluate --split dev --no-hooks    # window, input and AFK only
```

The strongest signal without terminal data is `circling`: many visits over 5 minutes to only a few
windows. Rapid coding switches just as often but keeps reaching new files and pages.

Weights and thresholds live in `src/classifier/weights.json`, with one profile for data with
terminal and file hooks and one for stock ActivityWatch. Labels are read only by `evaluate`. They come
from our own generator, so these results test the pipeline and are not evidence of real-world accuracy.

## Not in the repo

`aw-buckets-export.json` (a real ActivityWatch export) is gitignored on purpose —
it is personal telemetry. Drop your own export in the repo root
locally and it stays out of git.
