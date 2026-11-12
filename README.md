# SAIP

SAIP is a small assistant that notices when you are stuck and offers help, but only when an offer is worth the
interruption. It runs on your computer, watches how you work (which apps you use, how often you switch, whether
commands fail), and asks a language model of your choice whether to speak up.

It is a research project (CSE 576, Topics in NLP). It builds on ideas from
[thunlp/ProactiveAgent](https://github.com/thunlp/ProactiveAgent) and the PRISM interruption paper.

- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Using SAIP](#using-saip)
- [What SAIP can see, and where it goes](#what-saip-can-see-and-where-it-goes)
- [Settings](#settings)
- [Windows, macOS and Linux](#windows-macos-and-linux)
- [Removing SAIP](#removing-saip)
- [For developers](#for-developers)
- [Known limits](#known-limits)

## How it works

```
ActivityWatch  ──►  stuck score  ──►  two yes/no questions  ──►  the gate  ──►  an offer
(what you do)      (cheap, every      to your model            (is it worth     (notification,
                    5 seconds)        (only when the score      interrupting?)   card, popup)
                                       is high)
```

1. **ActivityWatch** is a free tracker that records which window is in front and when you type or click. SAIP reads
   it. Nothing leaves your machine at this step.
2. **The stuck score** is a number from 0 (in flow) to 1 (stuck), worked out every 5 seconds from the last few
   minutes. It uses simple rules, not a model. The biggest clue is *circling*: going back again and again to the
   same few windows. A paused score (you are away, in a meeting, or the screen is locked) is never treated as stuck.
3. **Two yes/no questions** go to your model, but only when the score crosses a line: "does this person need help
   right now?" and "would they accept an offer?". SAIP reads how sure the model is from its answer, not just the word.
4. **The gate** compares the chance of "yes, they would accept" with a bar. The bar is lower when you look very stuck
   and higher when you are deep in focus. Above the bar SAIP speaks. A near miss while you are in flow waits for a
   natural pause. Otherwise it stays quiet.
5. **An offer** is one short sentence. You can press **Help me** or **Not now**, or ignore it. After **Help me** the
   model writes help, and you can carry on in a chat.

How you answer changes how often SAIP speaks. Ignoring an offer makes it wait 1.5 times as long, dismissing one makes
it wait twice as long (up to 4 times), and accepting resets the wait. The formulas are in
[docs/State_Aware_Interruption_Formulas.md](docs/State_Aware_Interruption_Formulas.md).

## Quick start

You need Python 3.10 or newer and a language model behind an OpenAI-style endpoint that returns log-probabilities
(vLLM, llama.cpp server or Ollama's `/v1` all work). ActivityWatch can be installed for you during setup.

```bash
git clone git@github.com:vignesh-kumar-v/SAIP.git
cd SAIP
python3 -m venv .venv
source .venv/bin/activate        # on Windows: .venv\Scripts\activate
pip install -e .
saip run
```

`saip run` starts the agent, its dashboard and the tray icon, and opens your browser. **The first time, it opens a
setup page** and walks you through everything. Nothing on your computer changes until you press the button for it.

1. **ActivityWatch**: checks it is running, installs and starts it if missing, and shows whether its window, AFK and
   input watchers are reporting.
2. **Model (required)**: endpoint URL, model name and API key. SAIP tests the connection and saves it only if it
   works. You cannot finish setup without a working model.
3. **What the model can read**: typed text, screen text and window titles. All three start switched on. Turn off any
   you want to keep on your machine.
4. **Permissions**: what those need from your operating system, with a button for the right settings page and a
   key-press test.
5. **Blocked apps**: pick, from the apps installed on your machine, the ones SAIP must never read.
6. **Terminal hook**: a few lines in your shell's start-up file that report whether each command succeeded or failed
   (never the command itself).
7. **Git folders**: a folder that holds your projects. SAIP adds a small hook to each repository that notes when you
   commit. It asks once about repositories it finds, in a single prompt.
8. **Review and start**.

Want to look around first? This needs no ActivityWatch and no model, and changes nothing:

```bash
python -m saip.server --demo --speed 4
```

Open <http://localhost:5610/>. Add `--fresh` to start at the setup page.

## Using SAIP

### Commands

| Command | What it does |
|---|---|
| `saip run` | Start SAIP. Press Ctrl-C to stop. A second `saip run` just opens the running one. |
| `saip start` | The same, in the background. You can close the terminal. Output goes to `saip.log`. |
| `saip stop` | Stop SAIP (also in the tray menu and in Settings). |
| `saip status` | Say whether SAIP is running and what it is doing. |
| `saip doctor` | Check every part and say what is wrong. |
| `saip offers` | List what happened to past offers. |
| `saip repo add\|list\|remove` | Choose which folders' git repositories get the commit hook. |
| `saip setup` | The setup as questions in the terminal. Safe to repeat. |
| `saip tray` | Only the tray icon, for a SAIP that is already running. |
| `saip uninstall` | Put your machine back as it was. |

Useful options for `saip run`: `--no-browser`, `--no-tray`, `--no-ui` (terminal only), `--config FILE`,
`--ui-port N`, `--trigger 0` (ask the model every 30 seconds, for testing) and `--mock-llm` (a stand-in model, for
checking that the parts connect).

### The dashboard

SAIP serves a dashboard at <http://localhost:5610/>.

- **Live**: how you are doing right now in words ("Possibly stuck in Code"), the stuck score as a live chart with each
  stuck stretch labelled by app, what pushed the score up or down, time stuck per app, the last decision, and a feed.
- **Chat**: talk to the model about what you are doing (see below).
- **History**: every offer and every time the model was asked, with CSV and JSONL export.
- **Inspector**: the exact text the model was sent at each of the last 50 checks. This is kept in memory only.
- **Settings**: the model, what it may read, blocked apps, chat tools, saved chats, notifications, ActivityWatch,
  git folders, the terminal hook, timing and gate numbers, launch at login, and health checks. Changes apply at once.

Offers and other questions appear as cards on every page. There is a dark and a light theme.

### Offers, notifications and the tray

Each offer shows up as a **system notification** (so you see it with the dashboard hidden), as a card in the
dashboard, and, with no dashboard open, as a small popup or system dialog. There is no default button, so a stray
Return key cannot answer for you. Answering in one place closes the others.

The **tray icon** is the SAIP mark: a line that loops back on itself. It changes with your state (plain when in flow,
a dot when possibly stuck, broken when paused, faint when SAIP is not running). Its menu lets you open the dashboard,
pause offers (30 minutes, an hour, until tomorrow), switch the typed-text monitor and launch at login, open
settings, and quit.

### Help and chat

After **Help me**, the model writes help. Before it answers it may *look* first: read a file, your uncommitted git
changes, your screen text, or search the web if you switched that on. What it looked at is shown above its answer. A
toast offers **Continue in chat**.

The **Chat** page is a normal conversation, and it knows what you were doing in the last few minutes. You can:

- reply to one earlier message, or just part of it (select text and press **Reply to this part**);
- **edit** one of your messages and send it again, or **rewind** to it, which removes everything after it;
- **delete** chats you no longer want: hover over one in the list and press the bin, or press **Select**, tick several
  and press **Delete**. Files a chat wrote stay as they are;
- ask it to use tools (below). Replies are shown with Markdown: code in boxes with a Copy button, lists, bold.

**Chat tools** (Settings → Chat tools):

| Tool | What it does | Needs your approval? |
|---|---|---|
| Read your screen | The text near your cursor in the front app | No |
| Activity summary | What you did in the last few minutes | No |
| Git log and diff | Recent commits and uncommitted changes in your watched repositories | No |
| Read a file | Text and code files inside your watched folders only, never secret files or files git ignores | No |
| Web search and open a page | DuckDuckGo by default, or Brave or Tavily with a key. **Off until you switch it on.** | No |
| Copy to clipboard | Puts text you can see on your clipboard | **Yes** |
| Write a file | Plain-text and code files inside your watched folders only. You see the whole file, or the changes if it exists. | **Yes** |

It cannot write into other apps, make git commits, run commands, or touch anything inside `.git`. It can only
propose; nothing happens until you press **Approve**. **Rewind puts written files back as they were**. A file you
changed yourself in the meantime is left alone, and a clipboard copy or a web search cannot be undone (the dialog tells
you before you confirm).

Because the model may be small and can be talked round (by you, or by text it reads in a file or web page), some
limits hold whatever it asks:

- **Secret files are off limits**, for reading and writing: keys (`*.pem`, `id_rsa`, `.ssh/`, `.aws/`), `.env` files,
  `secrets.*`, `credentials.*`, service-account files and SAIP's own `private.toml`. Files git ignores are not read
  either, since that is where secrets usually live.
- **Everything a tool returns is masked** the same way as screen text (below). A write that would put `[hidden]` into
  a file is refused, so a masked secret is never written over the real one.
- **CI and editor settings are never written**: `.github/`, `.gitlab-ci.yml`, `.circleci/`, `.vscode/`, `.idea/`,
  `.husky/`, `.pre-commit-config.yaml` and similar. The chat can still read them.
- **Files that run code by themselves get a warning** on the approval card (shell scripts, `package.json`,
  `pyproject.toml`, `setup.py`, `conftest.py`, compose files), and the button reads **Approve anyway**.
- **An action proposed after reading text needs a tick.** If the model read a file, a diff, commit messages, your
  screen or a web page earlier in the chat, any copy or write it proposes lists what it read, and **Approve** stays
  off until you tick "I checked this". Text like that can carry instructions meant to steer the model. SAIP enforces
  this, not only the page, and saying no never needs it.
- **Replies are checked.** Code in a reply that deletes for good (`rm -rf`, `git reset --hard`, `DROP TABLE`), runs a
  script from the internet (`curl … | sh`), uses `sudo`, force-pushes or switches off a macOS protection is listed
  above the reply with the reason. A reply that says it saved a file or copied something when no approved action
  shows it, or that it ran, committed, deleted or installed anything (which SAIP cannot do), is marked too.

Chats are kept in memory and disappear when SAIP stops. To keep them, switch on **Keep chats between runs**
(Settings → Saved chats). They are then saved to `chats.jsonl` in the settings folder, readable only by you. Turning
it off deletes the file, and **Delete all chats** empties it.

### Running in the background and at login

`saip start` runs SAIP without a terminal. **Launch at login** (Settings or the tray menu, off by default) starts it
when you log in: a LaunchAgent on macOS, a registry `Run` entry on Windows, an autostart file on Linux.

On macOS the permissions follow the app that started SAIP. From a terminal, they belong to that terminal. A background
SAIP runs as plain Python, so macOS may ask again for Python itself (see [macOS](#macos)).

### Git commit hooks

The commit hook is a few lines at the top of `.git/hooks/post-commit`. It tells SAIP that a commit happened (a
commit counts as progress and lowers the stuck score). It runs in the background and ignores errors, so it cannot slow
down or break a commit. A repository is skipped, with the reason shown, if its hook cannot be added safely (for
example it sets its own `core.hooksPath`). Those you accept anyway are checked by polling instead. While SAIP runs it
looks for new repositories every few minutes and asks before adding one.

## What SAIP can see, and where it goes

Your **model endpoint** is the only place SAIP sends your information. It may be local or remote. If it is remote,
everything in the table below that you leave switched on goes there.

| What | Used for | Goes to the model? | How to control it |
|---|---|---|---|
| App names and how long you spent in each | The stuck score and the model's summary | Yes (names of blocked apps are replaced by "Private app") | Block list |
| How many keys and clicks | The stuck score | Counts only, never the keys | n/a |
| Window titles | A fuller summary | Only if switched on | Settings → What it reads |
| What you type | Context for the offer and help | Only if switched on: the newest 200 characters, kept in memory | Settings → What it reads, block list, **Esc** clears it, **F1** turns it off |
| Text near your cursor | Context for offers, help and the chat | Only if switched on, and only when an offer is written or the chat asks | Settings → What it reads, block list |
| Whether terminal commands fail | The stuck score | An exit status and a hash, never the command | Remove the terminal hook |
| Commits | The stuck score | The word "commit" only | Remove the git hook |
| Files, git changes, web pages | Chat tools | Only what the model reads with a tool | Settings → Chat tools |
| Your search words | Web search | To the search service, not the model | Web search is off by default |

Some protections apply everywhere:

- **One block list.** Apps on it are never named to the model, nothing typed in them is kept, and their screen text
  is never read. It is matched by whole words, ignoring case and `.exe` or `.app`, so "Chrome" blocks "Google Chrome"
  but "Mail" does not block "Gmail". Settings lists the apps installed on your machine to pick from.
- **Secrets are masked** before text goes to the model, in screen text, typed text and everything a chat tool
  returns: keys and tokens (such as `sk-…`, `ghp_…`, `AKIA…`),
  `password:` and `api_key=` lines, and long random-looking strings. This is pattern-based, so it is a safety net, not a
  guarantee.
- **The block list works per app, not per field.** macOS hides keys from SAIP while a password box has focus, but
  on other systems a password typed into an app you did not block can reach the typing buffer. Block browsers too if
  that matters to you.
- **The dashboard is local.** It listens on `127.0.0.1` only, refuses other host names, and accepts changes only as
  JSON from its own pages, so a website you visit cannot change your settings or answer an offer. Your API key is
  never sent back to the page.
- **Local records.** `offers.jsonl` and `decisions.jsonl` hold numbers, the offer text and the app name. They do not
  hold window titles, typed text or chat messages.
- **Web pages are untrusted.** Text from the web is treated as data. Even if a page tells the model to do something,
  the only actions it could reach are the two that need your Approve.

## Settings

Everything can be changed in the dashboard (Settings). Under the hood they live in `private.toml`, which is
git-ignored and readable only by you. SAIP looks for it in this order: the file named by `--config`, `$SAIP_CONFIG`,
`./private.toml`, then the settings folder. `private.example.toml` lists every setting with a comment. The ones you
are most likely to want:

| Setting | Meaning |
|---|---|
| `[llm] base_url`, `model`, `api_key` | Your model endpoint. The environment variables `SAIP_LLM_BASE_URL`, `SAIP_LLM_MODEL` and `SAIP_LLM_API_KEY` override them. |
| `[llm] reasoning_effort` | Set to `"none"` for a "thinking" model, so its first word is the answer. |
| `[llm] include_titles`, `read_screen` | Window titles and screen text go to the model. |
| `[llm] private_apps` | The block list. |
| `[llm] chat_tools`, `web_search`, `search_provider`, `search_api_key` | Chat tools and web search. |
| `[agent] monitor_typed_content` | The typed-text monitor. |
| `[agent] watch_roots` | Folders whose git repositories get the hook, and where chat tools may read and write. |
| `[agent] notify_offers`, `keep_chats` | System notifications, and saving chats between runs. |
| `[agent] cooldown_s`, `assess_interval_s`, `offer_timeout_s` | How often SAIP may speak. |
| `[gate] c0`, `m0`, `alpha`, `beta`, `delta`, `theta_flow` | The costs in the decision rule. |

**Where SAIP keeps its files:** `~/Library/Application Support/saip` on macOS, `%APPDATA%\saip` on Windows,
`~/.config/saip` on Linux. It holds `setup.json` (what setup changed, so uninstall can undo it), `offers.jsonl`,
`decisions.jsonl`, `calibration.json`, `saip.log`, and `chats.jsonl` if you keep chats.

## Windows, macOS and Linux

| | macOS | Windows | Linux |
|---|---|---|---|
| Status | **Tested** | Written, not yet run on a real machine | Written, not yet run on a real machine |
| Install ActivityWatch | Homebrew | winget | Download link |
| Shell hook | zsh | PowerShell | bash |
| Notifications | `osascript` | Toast | `notify-send` |
| Question dialogs | `osascript` | Message box | `zenity` (else the terminal) |
| Tray | Menu bar | System tray | Needs AppIndicator (GNOME needs an extension) |
| Typed-text monitor and screen text | Needs permissions | Written, not yet run | X11 only (Wayland hides keys and windows) |

"Not yet run" means the code follows the same interface and its logic is tested with the operating system calls faked.
CI runs the tests on all three systems.

### macOS

Typed text and screen text need two permissions in **System Settings → Privacy & Security**:
**Input Monitoring** and **Accessibility**. They are given to the app that started SAIP (Terminal, iTerm, VS Code).
The setup page and Settings → What it reads have a button for each and a key-press test. After you switch one on,
restart SAIP.

If you cannot find the page in System Settings, this opens it directly:

```bash
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
```

For `saip start` and launch at login, give the same two permissions to Python itself
(`…/Python.framework/Versions/3.x/Resources/Python.app`). Apps like Chrome do not expose their page text to the
Accessibility API, so screen text may be empty there. Window titles and typed text still work.

ActivityWatch's **input watcher** does not start by itself. In the ActivityWatch menu, open **Modules** and tick
`aw-watcher-input`. The setup page tells you if it is not reporting.

## Removing SAIP

```bash
saip uninstall
```

It lists everything SAIP changed, asks once, then reverses each change: the shell block, every commit hook (an existing
hook returns byte for byte), the ActivityWatcher buckets SAIP created, launch at login, SAIP's own files (offers,
decisions, saved chats, logs), ActivityWatch itself if SAIP installed it, and finally the `saip` command. A
`private.toml` that existed before setup is left alone, and your recorded ActivityWatch history is never deleted.
Options: `--keep-config` and `--keep-command`.

## For developers

### Layout

```
src/
  synth/        synthetic ActivityWatch days, for developing without real data
  classifier/   the stuck score: loader, app categories, features, scorer, evaluation, weights.json
  llm/          the model side: client, prompts, probes, calibration, the gate, offers, chat, privacy, config
  saip/         the app: command line, setup, live loop, API server, tray, platform adapters, hooks, chat tools
frontend/       the dashboard (Vite, React, TypeScript, Tailwind); its build is committed in src/saip/ui/dist
tests/          mirrors src/
docs/           the roadmap, the gate's formulas, and notes for trying the dashboard and tray (UI_SETUP.md)
scripts/        generate_dataset.py
private.example.toml   every setting, with comments
```

Everything runs from the repo root. New components get their own folder under `src/` and `tests/`.

### Tests

```bash
pip install -e ".[dev]"
pytest                          # about 410 tests; CI runs them on macOS, Windows and Linux, Python 3.10 and 3.12
cd frontend && npm install && npm test
```

### The dashboard source

The dashboard is built into `src/saip/ui/dist` and committed, so running SAIP needs no Node. After changing
`frontend/`:

```bash
cd frontend
npm install
npm run build          # writes src/saip/ui/dist: commit it
npm run dev            # hot reload on :5173, talking to a running SAIP or the demo on :5610
```

### The local API

`src/saip/server.py` lists every route in its header. `GET /api/events` streams ticks, decisions, stuck stretches,
offers and outcomes as server-sent events; the dashboard and the tray are both clients of it. The tray is a separate
process that uses only this API.

### Synthetic data

The generator writes ActivityWatch-shaped days with ground-truth labels, so the classifier can be built and checked
without real data.

```bash
python -m synth --seed 1 --out data/synthetic/day_seed1      # one day
python scripts/generate_dataset.py                           # 20 people, 10 days each, in data/synthetic
python -m synth --user 7 --seed 1 --date 2026-11-04 --out data/synthetic/user7/day1   # one person over several days
```

Each day has `window`, `afk`, `input`, `web`, `terminal` and `fs` streams, `labels.jsonl` (ground truth, read only by
evaluation), `help_requests.jsonl`, `manifest.json` and an importable `aw-export.json`. Real exports have quirks that
the generator copies: events are stored newest first; web events keep counting while a browser is in the background; a
sleeping laptop leaves gaps while input keeps reporting zeros; opening a chatbot is not a sign of being stuck; and
`terminal` and `fs` come from SAIP's hooks, not from stock ActivityWatch. Match buckets by **type**, not by id.

### The classifier

```bash
python -m classifier score --export aw-buckets-export.json --out scores.jsonl   # score any export
python -m classifier evaluate --split dev                                       # check against synthetic labels
python -m classifier evaluate --split dev --no-hooks                            # window, input and AFK only
```

It is a hand-weighted logistic score over ten signals (circling, revisits, switching, stalling, reaching, typing, AI
tools, failures, repeats, progress), smoothed over time, with no training and no GPU. Weights and thresholds are in
`src/classifier/weights.json`, one profile with terminal and git hooks and one for stock ActivityWatch.

### The decision layer

```bash
python -m llm gate                                    # the bar for a grid of stuck scores
python -m llm replay --mock --split dev               # run synthetic days through the pipeline, no model needed
python -m llm replay --split dev --fit                # real endpoint: fit calibration on the dev people
python -m llm replay --split test                     # report on the held-out people
```

`--mock` is a stand-in that checks the plumbing and says nothing about a real model. The generator has no "would
accept" label, so `--fit` uses the stuck label in its place. Both caveats are printed. Because the labels come from our own
generator, replay numbers test the pipeline and are not evidence about real users.

### Roadmap

The five phases are in [docs/ProActive_Agent_Implementation_Plan.md](docs/ProActive_Agent_Implementation_Plan.md):
data and ground truth, the state detector, the model and dynamic threshold, orchestration and interface, and
end-to-end testing. Phases 1 to 4 are built. Phase 5 (using it for real and tuning) has only just started.

## Known limits

- Only macOS has been run on a real machine. Windows and Linux are untested beyond unit tests.
- The stuck score ignores typing and reacts mostly to circling between windows, so focused work across two or three
  windows can read as stuck, and testing SAIP itself looks like being stuck.
- Browsers and some apps expose no page text, so "screen text" is often empty there.
- A small local model (for example a 9B one) with tools will sometimes pick the wrong tool or skip one it needed.
  Approval and the limits above keep that harmless, but answers will vary.
- Web search through DuckDuckGo uses its public HTML page, which is unofficial and can change or rate-limit.
- Evaluation numbers come from synthetic data. Nothing here shows how well SAIP works for real people yet.
- `aw-buckets-export.json` (a real ActivityWatch export) is git-ignored on purpose: it is personal data. Put your own
  in the repo root and it stays out of git.
