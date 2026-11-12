# Dashboard and tray: setup for teammates

`saip run` now starts the agent, the dashboard and the tray icon together, and the first run is set up in the
browser. The classifier, the gate and the LLM layer are unchanged.

## Status

| Piece | macOS | Windows | Linux |
|---|---|---|---|
| Dashboard and API | run in a browser against the demo and against a real `saip run` | not run since the rewrite | not run since the rewrite |
| Tray icon and menu | process confirmed to stay up through state changes; menu items not clicked by hand yet | unit tests only | unit tests only |
| Offer popup | uses the system dialog | as before (Tk popup), not re-run | as before, not re-run |
| Installed-apps list | run (118 apps found) | parser tested on fixture files | parser tested on fixture files |
| Launch at login | file contents tested; not logged out and in | registry value untested | file contents tested |
| Typed-text monitor with the block list | unit tests; needs Input Monitoring to try for real | foreground lookup is new and untested | needs X11 |

Please report what you see on your machine, especially on Windows and Linux.

## Install

Python 3.10 or newer. No Node is needed: the built dashboard is in the repository.

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

## Try the demo first

Needs no ActivityWatch and no model, and changes nothing on your machine.

```bash
python -m saip.server --demo --speed 4
```

Open `http://localhost:5610/`. Add `--fresh` to start at the setup wizard (in the demo, an endpoint URL containing
`fail` fails the connection test; anything else passes). To see the tray against the demo, run `saip tray` in a
second terminal.

## Run it for real

```bash
saip run
```

The browser opens. On the first run it opens the setup wizard; the agent starts when you finish. You need a model
endpoint that returns log-probabilities. For a pipeline check without one: `saip run --mock-llm`.

ActivityWatch's **input** watcher is not started by default: right-click the ActivityWatch icon, open **Modules**
and tick **aw-watcher-input**. The wizard's ActivityWatch step shows whether it is reporting.

On macOS the typed-text monitor and screen text need Input Monitoring and Accessibility for the app you started
`saip` from (Terminal, iTerm, VS Code). The wizard has a button for each and a key-press test. They take effect
after SAIP is restarted.

To see decisions quickly while testing, add `--trigger 0`: the model is asked every 30 seconds whatever the score.

## What to expect

- **Sitting still does not count as stuck.** The score responds mostly to circling: returning again and again to
  the same few windows. To raise it on purpose, switch between the same two windows for 2 to 3 minutes.
- **Testing the tool looks like being stuck**, because you keep flipping between the same terminal, browser and
  dashboard.
- **Offers go to whichever UI is open**: the dashboard (a card in the corner of every page), else the tray popup
  or system dialog. Answering in one place closes the other.

## Troubleshooting

| What you see | Cause and fix |
|---|---|
| "The dashboard has not been built" | `src/saip/ui/dist` is missing: `cd frontend && npm install && npm run build`. |
| Dashboard stays on "Waiting for activity" | The input watcher is not running. See above. |
| No tray icon on Linux | GNOME needs the AppIndicator extension. The dashboard works without the tray. |
| No tray icon on macOS | Run `python -m saip.tray` in a terminal and report what it prints. |
| "SAIP is already running" | It is: the dashboard was opened for you. Quit it from the tray or Settings > Startup. |
| Header says "Lost the agent" | `saip run` has stopped. Start it again; the page reconnects. |

## Working on the frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, talks to the API on port 5610 (a real run or the demo)
npm run build      # writes src/saip/ui/dist; commit the result
npm test
```

## Where the code is

| File | Purpose |
|---|---|
| `frontend/` | The dashboard's source (React) |
| `src/saip/ui/dist/` | Its build, served by the API |
| `src/saip/server.py` | Local HTTP API and static files |
| `src/saip/demo.py` | Made-up activity and a pretend setup |
| `src/saip/runtime.py` | The live loop, startable and reloadable |
| `src/saip/events.py` | Event bus; shows dialogs in a UI when one is connected |
| `src/saip/tray.py`, `src/saip/icons.py` | Tray icon, menu and popup; the SAIP mark |
| `src/saip/config_file.py` | Writes single settings into `private.toml` |
| `src/saip/permissions.py`, `src/saip/autostart.py` | OS permission checks; launch at login |
| `src/saip/platform/apps.py` | Installed apps and the app in front, per OS |
| `src/llm/privacy.py` | The block list |
| `tests/saip/test_ui.py`, `tests/saip/test_dashboard_api.py` | Tests for the above |
