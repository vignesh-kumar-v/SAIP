# Dashboard and tray: setup for teammates

This branch adds a UI to SAIP. Nothing in the classifier, the gate or the LLM layer changes; `saip run` without
the new flags behaves as before.

| Piece | What it is | How to start it |
|---|---|---|
| Local API | Streams every tick, decision, offer and outcome; accepts answers | `saip run --ui` |
| Dashboard | A page at `http://localhost:5610/`: state, stuck score over 30 minutes, last gate decision, offer history, setup checks | open it in a browser |
| Tray | An icon by the clock and a small offer popup in the corner of the screen | `saip run --tray` |
| Demo mode | The dashboard fed with made-up activity; reads nothing on your machine | `python -m saip.server --demo` |

## Status

- The dashboard and the API have been run on Windows 11 against real ActivityWatch data with the mock model.
- The tray icon has been seen working on Windows 11. **The offer popup has not been confirmed on Windows yet**,
  including the property that it must not take keyboard focus. It has been drawn on a Linux test display.
- Nothing here has been run on macOS. There the tray shows offers with the existing system dialog instead of
  the popup.
- Not yet run against a real model, only `--mock-llm`.

Please report what you see on your machine.

## 1. Get the branch

```bash
git clone https://github.com/vignesh-kumar-v/SAIP.git
cd SAIP
git checkout ui-dashboard-tray
```

## 2. Install

Python 3.10 or newer.

Windows (Command Prompt):

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e ".[tray]"
pytest
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e ".[tray]"
pytest
```

`pytest` should finish with no failures (298 tests; a few are skipped on Windows).

## 3. Try the demo first

This needs no ActivityWatch and no model, and changes nothing on your machine.

```bash
python -m saip.server --demo --speed 4
```

Open `http://localhost:5610/`. The header says "Demo". The headline moves between "In flow" and "Possibly stuck",
and an offer card appears within about 15 seconds. To see the tray against the demo, run `saip tray` in a second
terminal (activate the venv there too).

## 4. Run it for real

### ActivityWatch must be recording input

SAIP needs three ActivityWatch watchers: window, AFK and **input**. The input watcher is not started by default.

1. Right-click the ActivityWatch icon in the system tray (on Windows it may be under the `^` arrow).
2. Open **Modules** and tick **aw-watcher-input**.
3. Check `http://localhost:5600/#/buckets`: `aw-watcher-input_<your machine>` should show "Updated" a few
   seconds ago.

It has to be ticked again after ActivityWatch restarts, unless you add it to ActivityWatch's autostart list.

### Start the agent

```bash
saip run --tray --mock-llm
```

`--tray` starts the agent, the dashboard and the tray together. `--mock-llm` uses a stand-in for the model, so no
endpoint is needed; its numbers and its offer text are canned.

The first run asks setup questions. To skip everything optional:

| Prompt | Answer |
|---|---|
| LLM endpoint URL | Enter |
| Model name | Enter |
| Add the terminal hook to your shell profile? | `n` |
| Folder(s) that contain your git projects | `none` |

You can turn those on later with `saip setup`; `saip uninstall` reverses anything setup changed.

To see decisions quickly while testing, add `--trigger 0`. That asks the model every 30 seconds whatever the
score is. The headline then reads "Possibly stuck" even at a low score, because it compares the score with that
lowered line.

## What to expect

- **Sitting still does not count as stuck.** The score responds mostly to *circling*: returning again and again to
  the same few windows. To raise it on purpose, switch between the same two windows for 2 to 3 minutes, staying
  3 to 5 seconds on each.
- **Testing the tool looks like being stuck**, because you keep flipping between the same terminal, browser and
  dashboard. The agent also scores the last 90 minutes of history, so it can start out high.
- **Offers go to whichever UI is open.** With the dashboard or tray connected they appear there; with neither,
  they fall back to the system dialog as before. Answering in one place closes the other.
- **Tray menu:** open the dashboard, pause offers for an hour, quit the tray.

## Troubleshooting

| What you see | Cause and fix |
|---|---|
| Dashboard stays on "Waiting for activity"; log says "no recent input events" | The input watcher is not running. See "ActivityWatch must be recording input". |
| "Possibly stuck" straight away in the demo | The demo is scripted to do that; it is not reading your activity. |
| Two SAIP icons in the tray | Two tray processes are running (for example `saip tray` and `saip run --tray`). Quit one from its menu. An icon that vanishes on hover was left by a process that had already ended. |
| `saip tray` says it needs pystray and Pillow | Run `pip install -e ".[tray]"`. |
| "could not start the dashboard on port 5610" | Something else is using the port, often an earlier run. Stop it, or pass `--ui-port 5611`. |
| Header says "Lost the agent. Reconnecting…" | `saip run` has stopped. Start it again; the page reconnects. |

## Where the code is

| File | Purpose |
|---|---|
| `src/saip/events.py` | Event bus; shows dialogs in a UI when one is connected |
| `src/saip/server.py` | Local HTTP API, serves the dashboard, demo mode |
| `src/saip/ui/dashboard.html` | The dashboard: one file, no build step |
| `src/saip/tray.py` | Tray icon and offer popup, a separate process using only the API |
| `src/saip/cli.py` | `--ui`, `--tray`, `saip tray`; the live loop publishes events |
| `tests/saip/test_ui.py` | Tests for all of the above |

The API listens on 127.0.0.1 only, refuses other hosts and cross-site POSTs, and carries scores, gate numbers and
offer text: no window titles and no activity summary.

## Known gaps

- Settings and repository management are still command-line only.
- Nothing stops a second tray from starting.
- The opt-in typing monitor needs `pynput`, which is not a declared dependency.
- `private.example.toml` contains a `typed_text_max_chars` setting the loader rejects; do not copy it as-is.
