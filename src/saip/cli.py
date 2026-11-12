"""The `saip` command.

  saip run        start SAIP: the agent, its dashboard (opened in your browser) and the tray icon.
                  The first run walks you through setup in the browser.
  saip start      the same, in the background: no terminal has to stay open
  saip stop       stop a SAIP started either way
  saip status     is it running, and what is it doing
  saip setup      the setup in the terminal (safe to repeat)
  saip doctor     check every moving part and say what is wrong
  saip repo       add / list / remove the folders whose git repositories get the commit hook
  saip offers     what happened to past offers (accepted, dismissed, ignored)
  saip tray       only the tray icon, for a SAIP already running
  saip uninstall  put the machine back exactly as it was before SAIP
"""
from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from llm.config import load_settings

from . import offers as offers_mod
from . import repos as repos_mod
from . import server as server_mod
from . import setup as setup_mod
from .aw import DEFAULT_URL, ActivityWatch
from .events import Bus, UiPlatform
from .hooks import githook
from .platform import current
from .runtime import RunOptions, Runtime


def cmd_setup(a) -> int:
    plat, aw = current(), ActivityWatch(a.url)
    results = setup_mod.run_setup(plat, aw, yes=a.yes, roots=a.root)
    return 1 if any(r.status == "failed" for r in results) else 0


def _same(a: Path, b: Path) -> bool:
    return a.expanduser().resolve() == b.expanduser().resolve()


def cmd_repo(a) -> int:
    """saip repo add|list|remove: choose the folders whose git repositories get the commit hook."""
    settings = load_settings()
    agent, python = settings.agent, sys.executable
    if a.action == "list":
        print("folders: " + (", ".join(agent.watch_roots) or "none (add one with `saip repo add FOLDER`)"))
        if agent.exclude:
            print("excluded: " + ", ".join(agent.exclude))
        states = repos_mod.inspect_all(repos_mod.discover(agent), python)
        print(f"{len(states)} repositories: {repos_mod.summarise(states)}" if states else "no repositories found")
        for line in repos_mod.describe(states):
            print(line)
        return 0
    if not settings.path:
        print("error: no private.toml yet. Run `saip setup` first.", file=sys.stderr)
        return 2
    folder = Path(a.folder).expanduser().resolve()
    if not folder.is_dir():
        print(f"error: {folder} is not a folder", file=sys.stderr)
        return 2
    shown = repos_mod.tilde(folder)
    if a.action == "add":
        roots = [r for r in agent.watch_roots if not _same(Path(r), folder)] + [shown]
        exclude = [e for e in agent.exclude if not _same(Path(e), folder)]
        agent = repos_mod.with_roots(agent, roots)
        states = repos_mod.inspect_all(repos_mod.discover(agent), python)
        print(f"Found {len(states)} repositories under {shown}: {repos_mod.summarise(states)}")
        for line in repos_mod.describe([s for s in states if s.state == githook.SKIP]):
            print(line)
        todo = [s.path for s in states if s.state in (githook.READY, githook.STALE)]
        if todo and not a.yes and input(f"Install the commit hook in {len(todo)} repositories? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing changed.")
            return 0
        repos_mod.set_config_list(settings.path, "agent", "watch_roots", roots)
        repos_mod.set_config_list(settings.path, "agent", "exclude", exclude)
        repos_mod.install_all(todo, python)
        found = [repos_mod.key(s.path) for s in states]
        setup_mod.record_decisions(accept=found)
        setup_mod.update_manifest(hooked_repos=[str(r) for r in repos_mod.discover(agent) if githook.has_hook(r)])
        print("Done." if todo else "Nothing to install.")
        return 0
    # remove
    under = [r for r in repos_mod.discover(agent) if r == folder or folder in r.parents]
    hooked = [r for r in under if githook.has_hook(r)]
    print(f"Will stop watching {shown} and remove the commit hook from {len(hooked)} repositories.")
    if not a.yes and input("Go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing changed.")
        return 0
    for r in hooked:
        githook.remove(r)
    setup_mod.record_decisions(decline=[repos_mod.key(r) for r in under])
    roots = [r for r in agent.watch_roots if not _same(Path(r), folder)]
    exclude = list(agent.exclude)
    if len(roots) == len(agent.watch_roots):               # it was not a root itself: keep it from being re-hooked
        exclude.append(shown)
    repos_mod.set_config_list(settings.path, "agent", "watch_roots", roots)
    repos_mod.set_config_list(settings.path, "agent", "exclude", exclude)
    print("Done.")
    return 0


def cmd_doctor(a) -> int:
    results = setup_mod.doctor(current(), ActivityWatch(a.url))
    return 1 if any(r.status == "failed" for r in results) else 0


def cmd_offers(a) -> int:
    """saip offers: what happened to each offer (numbers and text only, nothing about your work)."""
    rows = offers_mod.read_log()
    print(offers_mod.summarise(rows))
    for r in rows[-a.last:]:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("time", 0)))
        print(f"  {when}  {r.get('outcome', '?'):10s}  s={r.get('stuck_score', 0):.2f}  bar={r.get('bar', 0):.2f}  {r.get('task', '')}")
    if rows:
        print(f"log: {offers_mod.log_path()}")
    return 0


def _pip_uninstall() -> tuple[bool, str]:
    if sys.platform.startswith("win"):
        return False, "Windows cannot remove a running program; run it afterwards"
    p = subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "saip"], capture_output=True, text=True)
    return p.returncode == 0, (p.stdout + p.stderr).strip()[-200:]


def cmd_uninstall(a) -> int:
    results = setup_mod.uninstall(current(), ActivityWatch(a.url), yes=a.yes, keep_config=a.keep_config,
                                  pip=None if a.keep_command else _pip_uninstall)
    return 1 if any(r.status == "failed" for r in results) else 0


def _run_options(a) -> RunOptions:
    return RunOptions(config=a.config, mock_llm=a.mock_llm, trigger=a.trigger, no_dialog=a.no_dialog,
                      notify=a.notify, read_screen=a.read_screen)


def cmd_run(a) -> int:
    """saip run: the agent, the dashboard and the tray. The first run is set up in the browser."""
    plat, aw = current(), ActivityWatch(a.url)
    if a.no_ui:
        return _run_terminal_only(a, plat, aw)
    port = a.ui_port
    other = server_mod.running_here(port)
    if other:
        page = f"http://localhost:{port}/"
        print(f"SAIP is already running: {page}")
        if not a.no_browser:
            webbrowser.open(page)
        return 0
    if a.setup:
        cmd_setup(argparse.Namespace(url=a.url, yes=a.yes, root=a.root))
        print()

    bus = Bus()
    runtime = Runtime(bus, plat, aw, _run_options(a), ui=UiPlatform(plat, bus))
    stop = threading.Event()
    ready = threading.Event()
    api = server_mod.Api(bus, plat=plat, aw=aw, runtime=runtime, on_ready=ready.set, on_shutdown=stop.set)
    try:
        httpd = server_mod.start(bus, port, api=api)
    except OSError as e:
        print(f"port {port} is taken ({e}); using a free one instead")
        httpd = server_mod.start(bus, 0, api=api)
    page = server_mod.url(httpd)
    print(f"Dashboard at {page}")

    tray = None
    if not a.no_tray:                    # its own process: it talks to the API like any other UI
        tray = subprocess.Popen([sys.executable, "-m", "saip.tray", "--follow", "--port", str(httpd.server_address[1])])

    first = not (a.no_setup or a.mock_llm) and not setup_mod.is_ready(load_settings(a.config))
    if not a.no_browser:
        def open_page():
            # A dashboard tab left open from an earlier run reconnects by itself within about a second.
            if not _wait(lambda: bus.listeners > (1 if tray else 0), 2.5):
                webbrowser.open(page + ("welcome" if first else ""))
        threading.Thread(target=open_page, daemon=True).start()

    def on_signal(*_):
        stop.set()
    signal.signal(signal.SIGINT, on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, on_signal)
    try:
        if first:
            print(f"First run: finish setting up in the browser at {page}welcome\n(Ctrl-C to stop)")
            while not (ready.is_set() or stop.is_set()):
                stop.wait(0.5)
        if not stop.is_set():
            runtime.start()
            print("Ctrl-C to stop.\n")
            while not stop.is_set():
                stop.wait(0.5)
    finally:
        print("\nStopping…")
        runtime.stop()
        bus.publish("status", level="waiting", text="SAIP has stopped")
        if tray is not None and tray.poll() is None:
            tray.terminate()
            try:
                tray.wait(3)
            except subprocess.TimeoutExpired:
                tray.kill()
        httpd.shutdown()
        print("Stopped.")
    return 0


def log_file() -> Path:
    from llm.config import user_config_dir
    return user_config_dir() / "saip.log"


def cmd_start(a) -> int:
    """saip start: run SAIP detached from this terminal, with its output in a log file."""
    port = a.ui_port
    if server_mod.running_here(port):
        print(f"SAIP is already running: http://localhost:{port}/")
        return 0
    from llm.config import find_config
    cmd = [sys.executable, "-m", "saip", "--url", a.url, "run", "--ui-port", str(port)]
    config = find_config(a.config)
    if config:                                   # by path: the background process may not start in this folder
        cmd += ["--config", str(Path(config).resolve())]
    for flag in ("no_browser", "no_tray", "mock_llm"):
        if getattr(a, flag):
            cmd.append("--" + flag.replace("_", "-"))
    log = log_file()
    log.parent.mkdir(parents=True, exist_ok=True)
    out = open(log, "a", encoding="utf-8")
    out.write(f"\n--- saip start, {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    out.flush()
    detach = ({"creationflags": 0x00000008 | 0x00000200} if sys.platform.startswith("win")      # detached, own group
              else {"start_new_session": True})
    env = {**__import__("os").environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=out, env=env, **detach)
    if not _wait(lambda: server_mod.running_here(port) is not None or proc.poll() is not None, 15):
        print(f"SAIP did not answer on port {port} within 15 seconds. See {log}")
        return 1
    if proc.poll() is not None and not server_mod.running_here(port):
        print(f"SAIP stopped right away (exit code {proc.returncode}). See {log}")
        return 1
    print(f"SAIP is running in the background: http://localhost:{port}/\n"
          f"Stop it with `saip stop` or from the tray menu. Its output is in {log}")
    return 0


def cmd_stop(a) -> int:
    """saip stop: ask a running SAIP to shut down (agent, dashboard and tray)."""
    import json
    import urllib.request
    port = a.ui_port
    if not server_mod.running_here(port):
        print("SAIP is not running.")
        return 0
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", b"{}", {"Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5) as r:
            json.loads(r.read())
    except Exception as e:
        print(f"SAIP did not accept the stop request ({e}).")
        return 1
    if _wait(lambda: server_mod.running_here(port, timeout=0.5) is None, 15):
        print("SAIP has stopped.")
        return 0
    print("SAIP is still shutting down; check again with `saip status`.")
    return 1


def cmd_status(a) -> int:
    """saip status: exit code 0 when running, 3 when not."""
    info = server_mod.running_here(a.ui_port)
    if not info:
        print("SAIP is not running. Start it with `saip start` (background) or `saip run`.")
        return 3
    rt = info.get("runtime") or {}
    if not info.get("ready"):
        state = "waiting for setup to be finished in the browser"
    elif rt.get("error"):
        state = f"the agent stopped: {rt['error']}"
    elif rt.get("running"):
        state = {"llm": f"watching, model {rt.get('model')}", "mock": "watching, mock model",
                 "score-only": "scoring only (no model configured)"}.get(rt.get("mode"), "watching")
    else:
        state = "dashboard up, agent not running"
    print(f"SAIP is running: http://localhost:{a.ui_port}/\n  {state}")
    if info.get("quiet_until", 0) > time.time():
        print(time.strftime("  offers paused until %H:%M", time.localtime(info["quiet_until"])))
    return 0


def _wait(check, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(0.1)
    return check()


def _run_terminal_only(a, plat, aw) -> int:
    """saip run --no-ui: as before the dashboard existed. Setup asks its questions in the terminal."""
    if (a.setup or not setup_mod.is_set_up()) and not a.no_setup:
        print("First run: setting everything up.\n")
        cmd_setup(argparse.Namespace(url=a.url, yes=a.yes, root=a.root))
        print()
    bus = Bus()
    runtime = Runtime(bus, plat, aw, _run_options(a), ui=plat)
    runtime.start()
    print("Ctrl-C to stop.\n")
    try:
        while runtime.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    runtime.stop()
    print("\nStopped.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="saip", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL, help="ActivityWatcher server (default http://localhost:5600)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="start SAIP (dashboard, tray, agent); the first run is set up in the browser")
    r.add_argument("--setup", action="store_true", help="run the terminal setup again first")
    r.add_argument("--yes", action="store_true", help="answer yes to every terminal setup question")
    r.add_argument("--root", action="append", help="folder whose git repositories get the commit hook (repeatable)")
    r.add_argument("--no-setup", action="store_true", help="skip first-run setup and change nothing on this machine")
    r.add_argument("--trigger", type=float, help="testing: wake the LLM above this stuck score (0 = every assessment interval)")
    r.add_argument("--mock-llm", action="store_true", help="testing: use a deterministic stand-in for the model")
    r.add_argument("--notify", action="store_true", help="with --no-dialog, show a system notification when it speaks")
    r.add_argument("--no-dialog", action="store_true", help="print offers instead of asking with a dialog (no accept/dismiss)")
    r.add_argument("--read-screen", action="store_true",
                   help="read the text near your cursor when writing an offer (or set read_screen in private.toml)")
    r.add_argument("--config", help="path to private.toml")
    r.add_argument("--no-ui", action="store_true", help="no dashboard and no tray: the terminal only, as before")
    r.add_argument("--no-tray", action="store_true", help="no tray icon")
    r.add_argument("--no-browser", action="store_true", help="do not open the dashboard in the browser")
    r.add_argument("--ui", action="store_true", help=argparse.SUPPRESS)       # the dashboard is on by default now
    r.add_argument("--tray", action="store_true", help=argparse.SUPPRESS)     # so is the tray
    r.add_argument("--ui-port", type=int, default=server_mod.DEFAULT_PORT, help=f"dashboard port (default {server_mod.DEFAULT_PORT})")
    r.set_defaults(fn=cmd_run)
    st = sub.add_parser("start", help="start SAIP in the background (no terminal has to stay open)")
    st.add_argument("--config", help="path to private.toml")
    st.add_argument("--no-browser", action="store_true", help="do not open the dashboard in the browser")
    st.add_argument("--no-tray", action="store_true", help="no tray icon")
    st.add_argument("--mock-llm", action="store_true", help="testing: use a deterministic stand-in for the model")
    st.add_argument("--ui-port", type=int, default=server_mod.DEFAULT_PORT, help="dashboard port")
    st.set_defaults(fn=cmd_start)
    for name, fn, text in (("stop", cmd_stop, "stop a running SAIP"), ("status", cmd_status, "say whether SAIP is running")):
        x = sub.add_parser(name, help=text)
        x.add_argument("--ui-port", type=int, default=server_mod.DEFAULT_PORT, help="dashboard port")
        x.set_defaults(fn=fn)
    s = sub.add_parser("setup", help="set everything up (safe to repeat)")
    s.add_argument("--yes", action="store_true")
    s.add_argument("--root", action="append", help="folder whose git repositories get the commit hook (repeatable)")
    s.set_defaults(fn=cmd_setup)
    g = sub.add_parser("repo", help="choose the folders whose git repositories get the commit hook")
    g.add_argument("action", choices=["add", "list", "remove"])
    g.add_argument("folder", nargs="?")
    g.add_argument("--yes", action="store_true")
    g.set_defaults(fn=cmd_repo)
    d = sub.add_parser("doctor", help="check every moving part")
    d.set_defaults(fn=cmd_doctor)
    o = sub.add_parser("offers", help="show what happened to past offers")
    o.add_argument("--last", type=int, default=10, help="how many recent offers to list")
    o.set_defaults(fn=cmd_offers)
    t = sub.add_parser("tray", help="only the tray icon, for a SAIP already running")
    t.add_argument("--port", type=int, default=server_mod.DEFAULT_PORT, help="the agent's --ui-port")
    t.set_defaults(fn=lambda a: __import__("saip.tray", fromlist=["main"]).main(["--port", str(a.port)]))
    u = sub.add_parser("uninstall", help="put the machine back as it was before SAIP")
    u.add_argument("--yes", action="store_true", help="do not ask before reverting")
    u.add_argument("--keep-config", action="store_true", help="keep private.toml and calibration.json")
    u.add_argument("--keep-command", action="store_true", help="do not pip-uninstall the saip package itself")
    u.set_defaults(fn=cmd_uninstall)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
