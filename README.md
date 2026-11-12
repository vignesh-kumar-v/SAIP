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

One command starts everything:

```bash
pip install -e .
saip run              # the agent, its dashboard (opens in your browser) and the tray icon. Ctrl-C to stop.
saip doctor           # check every moving part
saip offers           # what happened to past offers: accepted, dismissed, ignored
saip setup            # the same setup as questions in the terminal (safe to repeat)
saip tray             # only the tray icon, for a SAIP already running
saip uninstall        # put the machine back as it was before SAIP
```

The first `saip run` opens `http://localhost:5610/welcome` and sets everything up in the browser, step by step.
Nothing on your machine changes until you press the button for it, and the agent starts when you finish:

1. **ActivityWatch**: checks it is running, and installs and starts it if missing (Homebrew on macOS, winget on
   Windows, a download link on Linux). Shows whether the window, AFK and input watchers are reporting.
2. **Model (required)**: endpoint URL, model name and API key. The connection is tested for log-probabilities and
   saved to `private.toml` (readable only by you) when it passes. Setup cannot finish without a working model.
3. **What the model can read**: the typed-text monitor, screen text and window titles. All three start switched on;
   turn off any you want kept on this machine.
4. **Permissions**: what those need from the OS (on macOS, Input Monitoring and Accessibility for the app that
   started `saip`), with a button to open the right settings pane and a key-press test.
5. **Blocked apps**: pick, from the apps installed on this machine, the ones SAIP must never read. One list covers
   typed text, screen text and the activity summary.
6. **Terminal hook**: a marked block in your shell start-up file (zsh, bash or PowerShell). It reports each
   command's exit status only, never the command text.
7. **Git folders**: a folder that contains your projects; each repository under it gets a `post-commit` hook that
   sends only the word "commit".
8. **Review**: the `saip doctor` checks, then start.

`saip run --no-ui` runs in the terminal only, with the setup as terminal questions (`--yes` answers yes to all).
`--no-tray` and `--no-browser` turn those two off. A second `saip run` opens the dashboard of the one already
running.

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

**Dashboard and local API.** `saip run` serves the dashboard at `http://localhost:5610/` (change with `--ui-port`):

- **Live**: the state in words and the app you are stuck in, the stuck score as a live chart with each stuck stretch
  labelled by app, what pushed the score at the latest tick (each classifier feature's weight x value), time stuck
  per app, the last gate decision and a feed of events.
- **History**: every offer and every model check, with CSV and JSONL export.
- **Inspector**: the exact context sent to the model at each of the last 50 checks, with the answers and the offer.
  Kept in memory only.
- **Settings**: the model, what it may read, blocked apps, ActivityWatch, git folders and the terminal hook, timing
  and gate numbers, launch at login, and the health checks. Saving reloads the agent; no restart is needed.

Offers, the help text and the new-repository question appear on every page with the same buttons; with no page
open they fall back to the tray popup or the system dialog. The page is one client of a small API
(`saip/server.py`, which lists every route): `GET /api/events` streams ticks, decisions, stuck stretches, offers
and outcomes as server-sent events. It listens on 127.0.0.1 only, refuses other hosts, and accepts changes only as
JSON from this machine's own pages, so a web site you visit cannot change settings or answer an offer. The API key
is never sent back to the page. Every gate decision is appended to `decisions.jsonl` in the settings folder
(numbers and the app name). `python -m saip.server --demo` serves the dashboard with made-up activity, and
`--demo --fresh` starts it at the setup wizard; neither reads or changes anything.

The dashboard's source is in `frontend/` (Vite, React, TypeScript, Tailwind). Its build is committed in
`src/saip/ui/dist`, so running SAIP needs no Node. After changing the frontend: `cd frontend && npm install &&
npm run build`. `npm run dev` serves it with hot reload against a running `saip run` or the demo on port 5610.

**Tray icon.** `saip run` shows the SAIP mark in the menu bar or system tray: the plain mark in flow, with a dot when
possibly stuck, broken when paused, faint when the agent is not running. Its menu shows the state, opens the
dashboard, pauses offers (30 minutes, an hour, until tomorrow), switches the typed-text monitor and launch at login,
opens settings and the health check, and quits SAIP. On Windows and Linux an offer also appears as a small popup in
the corner of the screen that never takes the keyboard; on macOS it uses the system dialog. The tray is a separate
process that uses only the API. Tested on macOS; the Windows and Linux paths are written to the same interface and
covered by unit tests but have not been run on those systems since this rewrite. On GNOME the icon needs the
AppIndicator extension.

**Launch at login.** Off by default; switch it on from the tray or Settings. It adds a LaunchAgent on macOS, a
value under the user's `Run` registry key on Windows, or an XDG autostart entry on Linux, and starts
`saip run --no-browser`. `saip uninstall` removes it.

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

**Typed-text monitor.** On when `monitor_typed_content = true` (the setup wizard offers it switched on; in a
hand-written `private.toml` it is off unless you set it). On macOS the app running `saip` needs Input Monitoring; on
Linux it needs an X11 session. SAIP keeps the newest `typed_text_max_chars` (200) typed characters in memory. It
records in any app except those on the block list (`private_apps`), empties the buffer when you enter a blocked
app, and records nothing when it cannot tell which app is in front. Shortcuts are ignored, Esc clears the buffer, F1
turns it off. Nothing is written to disk or printed. The newest typed text is added to the behaviour summary the
model sees, as thunlp/ProactiveAgent does with its `user_input`: it reaches the two yes/no questions, the offer and
the help, at whichever endpoint you configured (local or remote). Obvious secrets such as API keys and
`password: ...` are masked first. The classifier score does not use it. The block list works per app, not per
field: macOS hides keys from listeners while a password field has focus, but elsewhere a password typed into an app
that is not blocked can enter the buffer, so block browsers too if that matters. Window titles are a separate
switch (`include_titles = true`), and page or editor text comes from `read_screen`.

**The block list.** `private_apps` is one list for the typing monitor, screen text and the activity summary
(`llm/privacy.py`). Names are matched as whole words, ignoring case and `.exe`/`.app`, in both directions, so
"Chrome" blocks "Google Chrome" and "Mail" does not block "Gmail". Settings lists the apps installed on the machine
(macOS bundles, Linux `.desktop` files, Windows Start Menu and registry), marking the ones you used lately. Names
already on your list that match no installed app stay visible so you can remove them.

**Privacy.** With the typed-text monitor off, keys are counted, never recorded. The terminal hook sends an exit code and a hash, not the command.
What the model sees is a short summary of app names, dwell times and counts; window titles stay out unless
`include_titles = true`, typed text only with the monitor on (see above), and apps on `private_apps` are never named or read. Everything is sent only to the endpoint you
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
