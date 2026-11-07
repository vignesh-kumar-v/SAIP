"""The `saip` command.

  saip run        set everything up on the first run, then watch and decide (Ctrl-C to stop)
  saip setup      just the setup (safe to repeat)
  saip doctor     check every moving part and say what is wrong
  saip repo       add / list / remove the folders whose git repositories get the commit hook
  saip uninstall  put the machine back exactly as it was before SAIP
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from llm.assess import assess
from llm.calibration import load_temperatures
from llm.client import LLMError, OpenAIChatClient
from llm.config import load_settings, user_config_dir
from llm.context import build_context
from llm.replay import MockClient

from . import prompts as repos_mod_prompts
from . import repos as repos_mod
from . import setup as setup_mod
from .agent import NotReady, Orchestrator, describe, fetch_trace, score_now
from .aw import DEFAULT_URL, ActivityWatch, AWError
from .hooks import githook
from .platform import current
from .watchers import GitWatcher


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


def _pip_uninstall() -> tuple[bool, str]:
    if sys.platform.startswith("win"):
        return False, "Windows cannot remove a running program; run it afterwards"
    p = subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "saip"], capture_output=True, text=True)
    return p.returncode == 0, (p.stdout + p.stderr).strip()[-200:]


def cmd_uninstall(a) -> int:
    results = setup_mod.uninstall(current(), ActivityWatch(a.url), yes=a.yes, keep_config=a.keep_config,
                                  pip=None if a.keep_command else _pip_uninstall)
    return 1 if any(r.status == "failed" for r in results) else 0


def cmd_run(a) -> int:
    plat, aw = current(), ActivityWatch(a.url)
    if (a.setup or not setup_mod.is_set_up()) and not a.no_setup:
        print("First run: setting everything up.\n")
        cmd_setup(argparse.Namespace(url=a.url, yes=a.yes, root=a.root))
        print()
    settings = load_settings(a.config)
    cfg = settings.agent
    client, mode = None, "score only (no LLM endpoint configured: scores are printed, nothing is decided)"
    if a.mock_llm:
        client, mode = MockClient(), "MOCK LLM (pipeline check only)"
    elif settings.llm.base_url and settings.llm.model:
        client, mode = OpenAIChatClient(settings.llm), f"LLM {settings.llm.model} at {settings.llm.base_url}"
    temps = load_temperatures(user_config_dir() / "calibration.json")
    git = GitWatcher(aw, [])             # polls only accepted repositories where a hook cannot be installed
    last_scan = 0.0

    def accepted_now(state, hooked):
        if not hooked:
            git.repos = list(dict.fromkeys(git.repos + [state.path]))
        setup_mod.update_manifest(hooked_repos=[str(state.path)] if hooked else [])
    prompter = repos_mod_prompts.RepoPrompter(plat, sys.executable, cfg.new_repos, accepted_now)
    prompter.start()
    orch = Orchestrator(cfg)
    print(f"Watching on {plat.name}. Mode: {mode}\nCtrl-C to stop.\n")
    last_status = 0.0
    try:
        while True:
            now = time.time()
            if now - last_scan >= cfg.rescan_s:
                accepted, declined = setup_mod.decisions()
                plan = repos_mod.plan_sync(cfg, sys.executable, accepted, declined)
                for st in plan.refresh:                      # accepted before, hook since lost: put it back quietly
                    githook.install(st.path, sys.executable)
                setup_mod.update_manifest(hooked_repos=[str(p) for p in plan.hooked + [s.path for s in plan.refresh]])
                git.repos = plan.polled
                for st in plan.new:                          # never hooked on their own: ask first
                    prompter.submit(st)
                last_scan = now
            git.poll(now)
            try:
                trace = fetch_trace(aw, now, cfg.history_min * 60)
                tick = score_now(trace, now)
                if a.trigger is not None:
                    tick.theta_trig = a.trigger                # testing: wake the LLM at this score instead
            except NotReady as e:
                if now - last_status > 60:
                    print(f"{time.strftime('%H:%M:%S')}  waiting: {e}")
                    last_status = now
                time.sleep(cfg.tick_s)
                continue
            except AWError as e:
                print(f"{time.strftime('%H:%M:%S')}  {e}")
                time.sleep(15)
                continue
            if client is not None:
                def run_assess():
                    ctx = build_context(trace, tick.time, settings.llm.lookback_s, settings.llm.include_titles,
                                        settings.llm.private_apps)
                    return assess(client, settings, temps, ctx, tick.stuck_score)
                try:
                    ev = orch.step(now, tick, run_assess)
                except LLMError as e:
                    print(f"{time.strftime('%H:%M:%S')}  LLM error: {e}")
                    ev = None
                    orch.last_assess = now
                if ev:
                    print(describe(now, tick, ev))
                    if ev.kind == "offer":
                        print("          >>> Looks like you may be stuck. Want a hand?")
                        if a.notify:
                            plat.notify("SAIP", "Looks like you may be stuck. Want a hand?")
            if now - last_status >= 60:
                state = f"paused ({tick.reason})" if tick.paused else "scoring"
                print(f"{time.strftime('%H:%M:%S')}  status: {state}, stuck score {tick.stuck_score:.2f} "
                      f"(LLM wakes above {tick.theta_trig:.2f})")
                last_status = now
            time.sleep(cfg.tick_s)
    except KeyboardInterrupt:
        prompter.stop()
        print("\nStopped.")
        return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="saip", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL, help="ActivityWatcher server (default http://localhost:5600)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="set up on first run, then watch and decide")
    r.add_argument("--setup", action="store_true", help="run setup again first")
    r.add_argument("--yes", action="store_true", help="answer yes to every setup question")
    r.add_argument("--root", action="append", help="folder whose git repositories get the commit hook (repeatable)")
    r.add_argument("--no-setup", action="store_true", help="skip first-run setup and change nothing on this machine")
    r.add_argument("--trigger", type=float, help="testing: wake the LLM above this stuck score (0 = every assessment interval)")
    r.add_argument("--mock-llm", action="store_true", help="use a deterministic stand-in for the model")
    r.add_argument("--notify", action="store_true", help="also show a system notification when it speaks")
    r.add_argument("--config", help="path to private.toml")
    r.set_defaults(fn=cmd_run)
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
    u = sub.add_parser("uninstall", help="put the machine back as it was before SAIP")
    u.add_argument("--yes", action="store_true", help="do not ask before reverting")
    u.add_argument("--keep-config", action="store_true", help="keep private.toml and calibration.json")
    u.add_argument("--keep-command", action="store_true", help="do not pip-uninstall the saip package itself")
    u.set_defaults(fn=cmd_uninstall)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
