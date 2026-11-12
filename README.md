# SAIP

Proactive agent research project: detect whether a user is in **flow** or **stuck**
from background desktop telemetry, and interrupt only when it is worth it.
See `docs/ProActive_Agent_Implementation_Plan.md` for the five-phase roadmap.

## Layout

```
src/                       importable packages, side by side
  synth/                     synthetic ActivityWatch day generator (Phase 1 fixtures): world, styles, generator, noise, writer, summary
  classifier/                stuck-state classifier (Phase 2): loader, categories, features, scorer, evaluate, weights.json
  llm/                       Phase 3: endpoint client, context, yes/no probes, calibration, state-aware gate, replay eval
  saip/                      the app: `saip` command, first-run setup, ActivityWatcher client, live loop, OS adapters, hooks
tests/                     mirrors src/
  synth/ classifier/ llm/ saip/   pytest suites
scripts/
  generate_dataset.py        builds the shared multi-person dataset
docs/
  ProActive_Agent_Implementation_Plan.md
data/                      generated output (gitignored)
pyproject.toml             package metadata; `pip install -e .` puts the packages on the path and installs the `saip` command
private.example.toml       template for private.toml (endpoint, gate costs, agent timings); private.toml is gitignored
.github/workflows/ci.yml   runs the tests on macOS, Windows and Linux
pytest.ini  requirements.txt
```

Everything runs from the repo root. New components get their own folder under `src/` and `tests/`.

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

## The app

One command sets everything up the first time and then watches your machine:

```bash
pip install -e .
saip run              # first run: set up, then watch and decide. Ctrl-C to stop.
saip doctor           # check every moving part
saip offers           # what happened to past offers: accepted, dismissed, ignored
saip setup            # just the setup (safe to repeat)
saip uninstall        # remove the shell hook (add --purge to delete private.toml too)
```

First-run setup, in order, asking before anything changes your machine (`--yes` answers yes to all):

1. Checks ActivityWatcher is running, and installs and starts it if missing (Homebrew on macOS, winget on Windows,
   a download link on Linux).
2. Writes `private.toml` with your LLM endpoint and model.
3. Adds a marked block to your shell start-up file (zsh, bash or PowerShell). It reports each command's exit
   status only, never the command text, so failed commands and repeats are visible to the classifier.
4. Asks for a folder that contains your git projects (for example `~/Developer`), lists every repository under it,
   and after one confirmation adds a `post-commit` hook to each. The hook sends only the word "commit".
5. Looks at what ActivityWatcher actually records. If window titles are empty or the input counts are all zero, a
   macOS permission is missing, and it says which one.

**Commit hooks.** Git runs `.git/hooks/post-commit` after every real commit, so the agent is told the moment a
commit happens (a branch switch or pull does not count). The hook is added at the top of an existing hook without
touching its lines, runs in the background and ignores errors, so it cannot slow down or break a commit.
A repo is skipped, with the reason shown, when a hook cannot be added safely: it sets its own `core.hooksPath`
(husky and similar), its hook is not a shell script, or the hook file is not executable. Skipped repos you accept
are checked by polling `git rev-parse HEAD` instead.

**New repositories.** While `saip run` is going it rescans your folders every few minutes. A repo it has not seen
before is never hooked on its own: a dialog asks **Add / Not now / Never** (a terminal question where the system
cannot show a dialog). Not now asks again next run, Never is remembered. Set `new_repos = "add"` or `"ignore"` in
`private.toml` to change that. Manage folders with:

```bash
saip repo add ~/Developer     # find and hook every repo under it
saip repo list                # each repo and whether its hook is working
saip repo remove ~/Developer/old-project
```

**Offers.** When the gate decides to speak, the model writes a one-line offer from the same behaviour summary
(adapted from the prompt in [thunlp/ProactiveAgent](https://github.com/thunlp/ProactiveAgent)): it may say what
pattern it noticed, and by default never what the problem is, because it does not see your work. A dialog shows it with
**Help me / Not now**, with no default button so a stray Return cannot answer for you. As in that project, an offer
ends as *accepted*, *dismissed* or *ignored* (no answer within `offer_timeout_s`, default 45 s). Ignoring stretches
the quiet period after an offer by 1.5x and dismissing by 2x (up to 4x); accepting resets it. The last answer is also
passed to the next offer's prompt. Every offer is appended to `offers.jsonl` in the settings folder: the gate's
numbers, the offer text and the outcome, nothing about your windows or typing. `saip run --no-dialog` prints offers
instead (`--notify` then shows a notification), and `saip offers` summarises the record.

**Dashboard and local API.** `saip run --ui` also serves a page at `http://localhost:5610/` (change with `--ui-port`):
the stuck score over the last 30 minutes, the last gate decision in words, the offer record and the `saip doctor`
checks. While the page is open, offers, the help text and the new-repository question appear there with the same
buttons; with it closed they fall back to the system dialog. The page is one client of a small API (`saip/server.py`):
`GET /api/events` streams every tick, decision, offer and outcome as server-sent events, and `POST /api/asks/<id>`
answers an open question, so a tray popup or any other UI can use the same two calls. It listens on 127.0.0.1 only,
refuses other hosts and cross-site POSTs, and carries scores, gate numbers and offer text: no window titles, no
activity summary. `python -m saip.server --demo` serves the page with made-up activity, for working on a UI without
ActivityWatcher or a model.

**Tray icon and popup.** `pip install "saip[tray]"`, then `saip run --tray` (or `saip tray` beside an agent already
running with `--ui`). The icon shows the state (in flow, possibly stuck, paused, agent not running) and its menu opens
the dashboard or pauses offers for an hour. An offer appears as a small popup in the corner of the screen with the
same buttons and time limit. The popup never takes the keyboard, so typing cannot answer it; whichever of the popup
and the dashboard is answered first wins and the other closes. It is a separate process that uses only the API above.
Written for Windows and Linux and not yet run on a real machine; on macOS, and where Python has no tkinter, the
question falls back to the system dialog.

**Context from your screen (opt-in).** thunlp gets its context from raw window titles and typed keys. SAIP's default
only counts keys. `include_titles = true` adds window titles, the typed-text monitor (below) adds what you typed, and
`read_screen = true` (or `saip run --read-screen`) reads the text around your cursor in the front app only at the
moment an offer is written, so the offer can say what you are actually working on. All of these go to whichever model
endpoint you configure, local or remote. Screen reading skips apps on the private list and password fields, masks
keys and tokens, and keeps at most `screen_chars` (1200) characters. Pressing **Help me** then
has the model write concrete help from the same text, shown in a dialog (with **Copy**); SAIP never types into your
documents. On macOS the app running `saip` needs Accessibility (System Settings > Privacy & Security). Other systems
do not read the screen yet.

**Undoing it.** `saip uninstall` puts the machine back as it was before SAIP. Setup records every change it makes,
and uninstall lists them, asks once, then reverses each: the shell block (and the profile file, if setup created
it), every repo hook (an existing hook returns byte for byte), the buckets SAIP created in ActivityWatcher, the
settings files setup wrote, ActivityWatcher itself if setup installed it, and finally the `saip` package. A
`private.toml` that existed before setup is left alone, your recorded ActivityWatcher history is never deleted,
and `--keep-config` / `--keep-command` keep the settings or the command.

| | macOS | Windows | Linux |
|---|---|---|---|
| Status | tested | experimental | experimental |
| Install ActivityWatcher | `brew install --cask` | `winget install` | link only |
| Shell hook | zsh `~/.zshrc` | PowerShell `$PROFILE` | bash `~/.bashrc` |
| Notification | `osascript` | toast | `notify-send` |
| Dialogs (new repo, offers) | `osascript` | message box | `zenity` (else terminal) |

"Experimental" means the adapter follows the same interface and its logic is covered by tests with the OS
calls faked, but it has not been run on a real machine yet. The CI matrix runs those tests on all three systems.
On Linux, ActivityWatcher's window watcher needs an X11 session; Wayland cannot see the focused window.
`classifier/categories.py` also lists Windows and Linux app names on a best-effort basis: an unknown app counts
as "other" and does not count as work, so extend the sets when a real export shows new names.

**Typed-text monitor (opt-in, Linux and macOS).** Off by default. With `monitor_typed_content = true` and the
optional extra installed (`pip install -e ".[typing]"`, which adds `pynput`; on macOS the app running `saip` needs
Input Monitoring), SAIP keeps the newest `typed_text_max_chars` (200) typed characters in memory. It records only
while an app on a short whitelist (editors, Word, browsers) is in front, never in an app on `private_apps`, and
empties the buffer when you leave. Shortcuts are ignored, Esc clears the buffer, F1 turns it off. Nothing is written
to disk or printed. The newest typed text is added to the behaviour summary the model sees, as thunlp/ProactiveAgent
does with its `user_input`: it reaches the two yes/no questions, the offer and the help, whichever endpoint you
configured (local or remote). Obvious secrets such as API keys and `password: ...` are masked first. The
classifier score does not use it. The whitelist is per app, not per field, so a password box inside an allowed
browser can still be read: drop the browser from the whitelist if that matters. Window titles are a separate switch
(`include_titles = true`), and page or editor text comes from `read_screen`.

**Privacy.** Unless you turn on the typed-text monitor, keys are counted, never recorded. The terminal hook sends an exit code and a hash, not the command.
What the model sees is a short summary of app names, dwell times and counts; window titles stay out unless
`include_titles = true`, typed text only with the monitor on (see above), and apps on `private_apps` are never named. Everything is sent only to the endpoint you
configure.

## The LLM layer (Phase 3)

`src/llm` turns the classifier's stuck score plus two yes/no questions into speak, wait or quiet. The
formulas and their derivation are in `docs/State_Aware_Interruption_Formulas.md` (`.html` is the rendered copy).

```bash
python -m llm gate                                   # the bar tau for a grid of stuck scores
python -m llm replay --mock --split dev              # run synthetic days through the pipeline, no model needed
python -m llm replay --split dev --fit               # real endpoint: fit calibration temperatures on dev people
python -m llm replay --split test                    # then report on the people held out
```

`--mock` is a deterministic stand-in that checks the plumbing and says nothing about a real model. The generator
has no acceptance label, so `--fit` fits `T_accept` on the stuck label as a stand-in. Both caveats are printed.
The synthetic labels come from our own generator, so replay numbers test the pipeline and are not evidence about
real users.

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
