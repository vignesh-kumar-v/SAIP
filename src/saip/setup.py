"""First-run setup, the health check, and uninstall. Idempotent: running it again only fixes what is missing.

Anything that changes your machine (installing ActivityWatch, adding the shell hook, writing the config)
is explained first and needs a yes, unless --yes was given. Every change can be undone with `saip uninstall`.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from llm.config import find_config, load_settings, user_config_dir

from . import repos as repos_mod
from .aw import FS, TERMINAL, ActivityWatch
from .hooks import githook
from .hooks.shells import has_block, install_block, remove_block
from .platform import Platform

MARKER = "setup.json"
TEMPLATE = """# SAIP settings. This file is private: it is gitignored and holds your endpoint details.
[llm]
base_url = {base_url}      # an OpenAI-compatible endpoint that supports logprobs (vLLM, llama.cpp server, Ollama /v1)
model = {model}
api_key = ""

[agent]
watch_roots = {roots}      # folders searched for git repositories; each one gets a commit hook
"""


@dataclass
class Result:
    name: str
    status: str          # ok | changed | skipped | needs-you | failed
    detail: str = ""

    def line(self) -> str:
        icon = {"ok": "ok     ", "changed": "done   ", "skipped": "skipped", "needs-you": "YOU     ", "failed": "FAILED "}
        return f"  [{icon[self.status]}] {self.name}" + (f": {self.detail}" if self.detail else "")


def marker_path() -> Path:
    return user_config_dir() / MARKER


def is_set_up() -> bool:
    return marker_path().exists()


def load_manifest() -> dict:
    """Everything setup changed, so uninstall can put it all back. Lives in the setup marker file."""
    try:
        return json.loads(marker_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def decisions() -> tuple[set, set]:
    """The repositories you said yes to (accepted) and never to (declined)."""
    m = load_manifest()
    return set(m.get("accepted_repos", [])), set(m.get("declined_repos", []))


def record_decisions(accept=(), decline=(), forget=()) -> None:
    m = load_manifest()
    acc, dec = set(m.get("accepted_repos", [])), set(m.get("declined_repos", []))
    accept, decline, forget = {str(x) for x in accept}, {str(x) for x in decline}, {str(x) for x in forget}
    acc = (acc | accept) - decline - forget
    dec = (dec | decline) - accept - forget
    m["accepted_repos"], m["declined_repos"] = sorted(acc), sorted(dec)
    marker_path().parent.mkdir(parents=True, exist_ok=True)
    marker_path().write_text(json.dumps(m, indent=2), encoding="utf-8")


def update_manifest(**new) -> None:
    cur = load_manifest()
    for key, value in new.items():
        if isinstance(value, list):
            cur[key] = sorted(set(cur.get(key, [])) | set(value))
        elif isinstance(value, bool):
            cur[key] = bool(cur.get(key)) or value
        elif key == "config_created":
            cur[key] = cur.get(key) or value
        else:
            cur[key] = value
    marker_path().parent.mkdir(parents=True, exist_ok=True)
    marker_path().write_text(json.dumps(cur, indent=2), encoding="utf-8")


def default_config_path() -> Path:
    """Next to the code when run from a checkout (the file is gitignored), else in the per-user config folder."""
    pyproject = Path.cwd() / "pyproject.toml"
    if pyproject.exists() and 'name = "saip"' in pyproject.read_text(encoding="utf-8"):
        return Path.cwd() / "private.toml"
    return user_config_dir() / "private.toml"


def _wait_for(check: Callable[[], bool], seconds: float, step: float = 1.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(step)
    return check()


def _toml(v) -> str:
    return json.dumps(v)


def run_setup(plat: Platform, aw: ActivityWatch, *, yes: bool = False, ask: Callable[[str], str] = input,
              out: Callable[[str], None] = print, roots: list[str] | None = None,
              python: str | None = None) -> list[Result]:
    python = python or sys.executable
    results: list[Result] = []
    installed_aw, created_config, created_profile, discovered = False, None, None, []

    def confirm(question: str) -> bool:
        return yes or ask(f"{question} [y/N] ").strip().lower() in ("y", "yes")

    def add(r: Result) -> None:
        results.append(r)
        out(r.line())

    out(f"System: {plat.name} ({'tested' if plat.level == 'full' else 'EXPERIMENTAL: written for this OS but not yet run on a real machine'})")

    # 1. ActivityWatcher
    if aw.ping():
        add(Result("ActivityWatcher", "ok", "running"))
    else:
        if not plat.activitywatcher_installed():
            if not confirm("ActivityWatcher is not installed. Install it now?"):
                add(Result("ActivityWatcher", "needs-you", f"install it from {plat.activitywatch_url}"))
            else:
                ok, msg = plat.install_activitywatch()
                if not ok:
                    add(Result("ActivityWatcher", "failed", msg))
                else:
                    installed_aw = True
        if plat.activitywatcher_installed():
            plat.start_activitywatch()
            if _wait_for(aw.ping, 30):
                add(Result("ActivityWatcher", "changed", "started"))
            else:
                add(Result("ActivityWatcher", "needs-you", "installed but not answering on localhost:5600; open it manually"))

    # 2. configuration
    cfg_path = find_config()
    if cfg_path:
        add(Result("config", "ok", str(cfg_path)))
    else:
        target = default_config_path()
        if yes:
            base_url, model = "", ""
        else:
            base_url = ask("LLM endpoint URL (OpenAI-compatible, e.g. http://host:8000/v1; Enter to fill in later): ").strip()
            model = ask("Model name (Enter to fill in later): ").strip()
        target.parent.mkdir(parents=True, exist_ok=True)
        created_config = str(target)
        target.write_text(TEMPLATE.format(base_url=_toml(base_url), model=_toml(model), roots=_toml(roots or [])), encoding="utf-8")
        detail = f"wrote {target}" + ("" if base_url and model else "; fill in [llm] base_url and model before using the LLM")
        add(Result("config", "changed", detail))

    # 3. shell hook
    shell = plat.shell()
    profile = plat.shell_profile(shell)
    if profile is None:
        add(Result("terminal hook", "needs-you", f"no hook for the '{shell}' shell yet; zsh, bash and PowerShell are supported"))
    elif profile.exists() and has_block(profile.read_text()) and python in profile.read_text():
        add(Result("terminal hook", "ok", f"already in {profile}"))
    elif confirm(f"Add the terminal hook (exit status only, never command text) to {profile}?"):
        if not profile.exists():
            created_profile = str(profile)
        action = install_block(profile, shell, python)
        add(Result("terminal hook", "changed", f"{action} in {profile}; open a new terminal tab for it to take effect"))
    else:
        add(Result("terminal hook", "skipped", "without it, failed commands are not seen"))

    # 4. commit hooks: you give a folder, every git repository under it is found and hooked
    results_before = len(results)
    settings = load_settings()
    agent = settings.agent
    chosen = list(agent.watch_roots)
    for r in roots or []:
        t = repos_mod.tilde(Path(r).expanduser())
        if t not in chosen:
            chosen.append(t)
    if not chosen and not yes:
        sug = next((p for p in ("~/Developer", "~/code", "~/projects", "~/src") if Path(p).expanduser().is_dir()), "")
        raw = ask("Folder(s) that contain your git projects, comma separated"
                  f"{f' [{sug}]' if sug else ''} (type 'none' to skip): ").strip() or sug
        chosen = [] if raw.lower() == "none" else [x.strip() for x in raw.split(",") if x.strip()]
    if not chosen:
        add(Result("commit hooks", "skipped", "no folder given, so commits are not tracked; add one with `saip repo add FOLDER`"))
    else:
        if chosen != list(agent.watch_roots) and settings.path:
            repos_mod.set_config_list(settings.path, "agent", "watch_roots", chosen)
        discovered = repos_mod.discover(repos_mod.with_roots(agent, chosen))
        states = repos_mod.inspect_all(discovered, python)
        if not states:
            add(Result("commit hooks", "skipped", f"no git repositories found under {', '.join(chosen)}"))
        else:
            for line in repos_mod.describe([s for s in states if s.state == githook.SKIP]):
                out(line)
            todo = [s.path for s in states if s.state in (githook.READY, githook.STALE)]
            _, declined_before = decisions()
            if not todo:
                record_decisions(accept=[repos_mod.key(s.path) for s in states if repos_mod.key(s.path) not in declined_before])
                add(Result("commit hooks", "ok", repos_mod.summarise(states)))
            elif len(todo) > repos_mod.MAX_REPOS_WITHOUT_ASKING and not confirm(
                    f"Found {len(todo)} repositories under {', '.join(chosen)}, more than usual. Add all of them to SAIP?"):
                record_decisions(decline=[repos_mod.key(s.path) for s in states])
                add(Result("commit hooks", "skipped", f"{len(todo)} repositories found; narrow the folder with `saip repo add`"))
            elif len(todo) <= repos_mod.MAX_REPOS_WITHOUT_ASKING and not confirm(
                    f"Add these {len(states)} repositories to SAIP (it records only the word 'commit' when you commit)?"):
                record_decisions(decline=[repos_mod.key(s.path) for s in states])
                add(Result("commit hooks", "skipped", "not added; commits are not tracked. New repos will still be offered to you"))
            else:
                record_decisions(accept=[repos_mod.key(s.path) for s in states])
                repos_mod.install_all(todo, python)
                add(Result("commit hooks", "changed", repos_mod.summarise(repos_mod.inspect_all([s.path for s in states], python))))

    # 5. do the streams carry real data?
    for r in check_data(plat, aw):
        add(r)

    update_manifest(done=True, platform=plat.name, python=python, activitywatch_installed_by_saip=installed_aw,
                    config_created=created_config, profiles_created=[created_profile] if created_profile else [],
                    hooked_repos=[str(s.path) for s in repos_mod.inspect_all(discovered, python) if s.state == githook.INSTALLED])
    return results


def check_data(plat: Platform, aw: ActivityWatch, now: float | None = None) -> list[Result]:
    """Look at what ActivityWatch has actually recorded. This is also how missing OS permissions show up."""
    from classifier.loader import STREAMS

    now = time.time() if now is None else now
    if not aw.ping():
        return [Result("data", "failed", "ActivityWatcher is not reachable on localhost:5600")]
    buckets = aw.buckets()
    by_type: dict[str, list[str]] = {}
    for bid, b in buckets.items():
        by_type.setdefault(b.get("type"), []).append(bid)
    results = []
    window_ids = [b for t, ids in by_type.items() if STREAMS.get(t) == "window" for b in ids]
    input_ids = [b for t, ids in by_type.items() if STREAMS.get(t) == "input" for b in ids]
    afk_ids = [b for t, ids in by_type.items() if STREAMS.get(t) == "afk" for b in ids]

    def recent(ids, minutes):
        rows = []
        for bid in ids:
            rows += aw.events(bid, now - minutes * 60, now, 200)
        return rows

    if not window_ids:
        results.append(Result("window watcher", "failed", "no window bucket: aw-watcher-window is not running"))
    else:
        rows = recent(window_ids, 5)
        if not rows:
            results.append(Result("window watcher", "failed", "no window events in the last 5 minutes"))
        elif not any((e["data"].get("title") or "").strip() for e in rows):
            results.append(Result("window watcher", "needs-you", "events have no window titles. " + plat.permission_hint()))
        else:
            results.append(Result("window watcher", "ok", "events with titles arriving"))
    results.append(Result("AFK watcher", "ok" if afk_ids else "failed", "" if afk_ids else "no aw-watcher-afk bucket"))
    if not input_ids:
        results.append(Result("input watcher", "failed", "no aw-watcher-input bucket: enable it in ActivityWatch (key and click counts only)"))
    else:
        rows = recent(input_ids, 10)
        if not rows:                               # the bucket is there from an earlier day but nothing fills it now
            results.append(Result("input watcher", "failed", "aw-watcher-input has stopped: no events in 10 minutes. Start it "
                                  "from the ActivityWatch tray icon (Modules > aw-watcher-input); it does not start by itself"))
            return results
        busy = any(any(e["data"].get(k, 0) for k in ("presses", "clicks", "scrollX", "scrollY")) for e in rows)
        results.append(Result("input watcher", "ok" if busy else "needs-you",
                              "counts arriving" if busy else "only zeros in 10 minutes (idle, or missing permission). " + plat.permission_hint()))
    return results


def doctor(plat: Platform, aw: ActivityWatch, out: Callable[[str], None] = print) -> list[Result]:
    results = [Result("system", "ok", f"{plat.name} ({plat.level})")]
    results += check_data(plat, aw)
    shell = plat.shell()
    profile = plat.shell_profile(shell)
    hooked = bool(profile and profile.exists() and has_block(profile.read_text()))
    results.append(Result("terminal hook", "ok" if hooked else "needs-you", "installed" if hooked else "not installed: run `saip setup`"))
    if aw.ping():
        has_terminal = any(b.get("type") == "app.terminal.command" for b in aw.buckets().values())
        results.append(Result("terminal events", "ok" if has_terminal else "needs-you",
                              "bucket exists" if has_terminal else "none yet: open a new terminal and run a command"))
    settings = load_settings()
    results.append(Result("config", "ok" if settings.path else "needs-you", str(settings.path or "no private.toml: run `saip setup`")))
    states = repos_mod.inspect_all(repos_mod.discover(settings.agent), sys.executable)
    if not (settings.agent.watch_roots or settings.agent.repos):
        results.append(Result("commit hooks", "needs-you", "no folder chosen: run `saip repo add FOLDER`"))
    elif not states:
        results.append(Result("commit hooks", "needs-you", "no git repositories found under your folders"))
    else:
        stale = [s for s in states if s.state == githook.STALE]
        results.append(Result("commit hooks", "needs-you" if stale else "ok",
                              repos_mod.summarise(states) + ("; some point at an old Python: run `saip setup`" if stale else "")))
    llm = settings.llm
    if llm.base_url and llm.model:
        results.append(check_endpoint(settings))
    else:
        results.append(Result("LLM endpoint", "needs-you", "not configured: the agent will only print scores"))
    for r in results:
        out(r.line())
    return results


def check_endpoint(settings) -> Result:
    from llm import prompts
    from llm.client import LLMError, OpenAIChatClient
    from llm.confidence import probe

    try:
        p = probe(OpenAIChatClient(settings.llm), prompts.need_messages("Windows: 1 visit to 1 distinct window."))
        return Result("LLM endpoint", "ok", f"{settings.llm.base_url} answered with log-probabilities (p_yes = {p:.2f})")
    except (LLMError, ValueError) as e:
        return Result("LLM endpoint", "failed", str(e))


@dataclass
class Undo:
    name: str
    what: str                      # shown before anything is touched
    do: Callable[[], str]          # returns a short result detail


def plan_uninstall(plat: Platform, aw: ActivityWatch, *, keep_config: bool = False, python: str | None = None,
                   pip: Callable[[], tuple[bool, str]] | None = None) -> list[Undo]:
    """Everything setup (and running the app) may have changed, as a list of reversible steps."""
    m = load_manifest()
    settings = load_settings()
    undos: list[Undo] = []

    profile = plat.shell_profile(plat.shell())
    if profile and profile.exists() and has_block(profile.read_text()):
        created = str(profile) in m.get("profiles_created", [])

        def undo_profile() -> str:
            remove_block(profile)
            if created and not profile.read_text().strip():
                profile.unlink()
                return f"removed from {profile} and deleted the empty file setup had created"
            return f"removed from {profile}"
        undos.append(Undo("terminal hook", f"remove the marked block from {profile}", undo_profile))

    candidates = [Path(p) for p in m.get("hooked_repos", [])] + repos_mod.discover(settings.agent)
    hooked = [r for r in dict.fromkeys(candidates) if githook.has_hook(r)]
    if hooked:
        undos.append(Undo("commit hooks", f"remove the commit hook from {len(hooked)} repositories",
                          lambda: f"removed from {sum(githook.remove(r) for r in hooked)} repositories"))

    if aw.ping():
        mine = [bid for bid, b in aw.buckets().items()
                if (bid.startswith(TERMINAL[0] + "_") and b.get("type") == TERMINAL[1])
                or (bid.startswith(FS[0] + "_") and b.get("type") == FS[1])]
        if mine:
            def undo_buckets() -> str:
                for bid in mine:
                    aw.delete_bucket(bid)
                return f"deleted {len(mine)}"
            undos.append(Undo("ActivityWatcher data", "delete the bucket(s) SAIP created, with the events in them: "
                                                       + ", ".join(b.split("_")[0] for b in mine), undo_buckets))

    cfg = m.get("config_created")
    to_delete = [Path(cfg)] if cfg and Path(cfg).exists() and not keep_config else []
    cal = user_config_dir() / "calibration.json"
    if cal.exists() and not keep_config:
        to_delete.append(cal)
    for name in ("offers.jsonl", "decisions.jsonl", "saip.log"):
        f = user_config_dir() / name
        if f.exists() and not keep_config:
            to_delete.append(f)
    if to_delete:
        def undo_files() -> str:
            for f in to_delete:
                f.unlink()
            return "deleted " + ", ".join(str(f) for f in to_delete)
        undos.append(Undo("config", "delete " + ", ".join(str(f) for f in to_delete), undo_files))
    elif settings.path and not keep_config and not cfg:
        undos.append(Undo("config", f"keep {settings.path} (it existed before SAIP set anything up)", lambda: "kept"))

    from . import autostart
    if autostart.is_enabled():
        undos.append(Undo("launch at login", f"remove {autostart.where()}",
                          lambda: "removed" if autostart.disable() else "nothing to remove"))

    if m.get("activitywatch_installed_by_saip"):
        data = plat.activitywatch_data_dir()

        def undo_aw() -> str:
            ok, msg = plat.uninstall_activitywatch()
            return ("uninstalled" if ok else f"could not uninstall: {msg}") + (
                f"; its recorded activity stays in {data}, delete that folder if you want it gone" if data else "")
        undos.append(Undo("ActivityWatcher app", "uninstall ActivityWatcher (SAIP setup installed it); "
                                                 "your recorded activity data is left alone", undo_aw))

    def undo_state() -> str:
        marker_path().unlink(missing_ok=True)
        d = user_config_dir()
        try:
            d.rmdir()
        except OSError:
            pass
        return "removed the setup record" + (" and the empty settings folder" if not d.exists() else "")
    if marker_path().exists() or user_config_dir().exists():
        undos.append(Undo("setup record", f"remove {marker_path()} and the settings folder if it ends up empty", undo_state))

    if pip is not None:
        def undo_pip() -> str:
            ok, msg = pip()
            return "removed the saip command and packages" if ok else f"run `pip uninstall saip` yourself ({msg})"
        undos.append(Undo("saip command", "uninstall the saip Python package (last step)", undo_pip))
    return undos


def uninstall(plat: Platform, aw: ActivityWatch, *, yes: bool = False, keep_config: bool = False,
              ask: Callable[[str], str] = input, out: Callable[[str], None] = print,
              pip: Callable[[], tuple[bool, str]] | None = None) -> list[Result]:
    undos = plan_uninstall(plat, aw, keep_config=keep_config, pip=pip)
    if not undos:
        out("Nothing to undo: SAIP has not changed anything on this machine.")
        return []
    out("This will put your machine back as it was before SAIP:")
    for u in undos:
        out(f"  - {u.what}")
    if not yes and ask("Go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
        out("Nothing changed.")
        return []
    results = []
    for u in undos:
        try:
            r = Result(u.name, "changed", u.do())
        except Exception as e:                                    # keep going: one failure must not strand the rest
            r = Result(u.name, "failed", str(e))
        results.append(r)
        out(r.line())
    return results


# ---------------------------------------------------------------------------------------------------- web setup
# The setup wizard in the dashboard walks the same steps as `run_setup`, one request at a time. These functions
# are those steps without the terminal prompts.

def is_ready(settings=None) -> bool:
    """Setup has been finished and a model is configured: `saip run` can start the agent straight away."""
    settings = settings or load_settings()
    return bool(load_manifest().get("done")) and bool(settings.llm.base_url and settings.llm.model)


def ensure_config(roots: list[str] | None = None) -> Path:
    """The private.toml in use, created from the template (only you can read it) when there is none yet."""
    from .config_file import write_private
    found = find_config()
    if found:
        return found
    target = default_config_path()
    write_private(target, TEMPLATE.format(base_url=_toml(""), model=_toml(""), roots=_toml(roots or [])))
    update_manifest(config_created=str(target))
    return target


def aw_status(plat: Platform, aw: ActivityWatch) -> dict:
    running = aw.ping()
    checks = [r.__dict__ for r in check_data(plat, aw)] if running else []
    return {"running": running, "installed": running or plat.activitywatcher_installed(),
            "url": plat.activitywatch_url, "checks": checks,
            "permission_hint": plat.permission_hint(),
            "ok": running and all(c["status"] == "ok" for c in checks)}


def aw_install(plat: Platform, aw: ActivityWatch) -> dict:
    if not plat.activitywatcher_installed():
        ok, msg = plat.install_activitywatch()
        if not ok:
            return {"ok": False, "detail": msg}
        update_manifest(activitywatch_installed_by_saip=True)
    return aw_start(plat, aw)


def aw_start(plat: Platform, aw: ActivityWatch, wait_s: float = 30) -> dict:
    if not aw.ping():
        plat.start_activitywatch()
        if not _wait_for(aw.ping, wait_s):
            return {"ok": False, "detail": "started, but it is not answering on localhost:5600 yet; open it manually"}
    return {"ok": True, "detail": "running"}


def hook_status(plat: Platform, python: str | None = None) -> dict:
    python = python or sys.executable
    shell = plat.shell()
    profile = plat.shell_profile(shell)
    text = profile.read_text() if profile and profile.exists() else ""
    return {"shell": shell, "profile": str(profile) if profile else None, "supported": profile is not None,
            "installed": bool(text) and has_block(text) and python in text}


def hook_install(plat: Platform, python: str | None = None) -> dict:
    python = python or sys.executable
    shell = plat.shell()
    profile = plat.shell_profile(shell)
    if profile is None:
        return {"ok": False, "detail": f"no hook for the '{shell}' shell yet; zsh, bash and PowerShell are supported"}
    created = not profile.exists()
    action = install_block(profile, shell, python)
    if created:
        update_manifest(profiles_created=[str(profile)])
    return {"ok": True, "detail": f"{action} in {profile}; open a new terminal tab for it to take effect"}


def hook_remove(plat: Platform) -> dict:
    profile = plat.shell_profile(plat.shell())
    removed = bool(profile) and remove_block(profile)
    return {"ok": True, "detail": f"removed from {profile}" if removed else "nothing to remove"}


SUGGESTED_ROOTS = ("~/Developer", "~/code", "~/Code", "~/projects", "~/Projects", "~/src", "~/dev", "~/repos",
                   "~/Documents/GitHub", "~/source/repos")


def repo_overview(python: str | None = None) -> dict:
    python = python or sys.executable
    agent = load_settings().agent
    states = repos_mod.inspect_all(repos_mod.discover(agent), python)
    accepted, declined = decisions()
    undecided = [s for s in states if repos_mod.key(s.path) not in accepted | declined and s.state != githook.SKIP]
    return {"roots": list(agent.watch_roots), "exclude": list(agent.exclude),
            "undecided": [{"path": repos_mod.tilde(s.path), "state": s.state} for s in undecided],
            "suggestions": [p for p in dict.fromkeys(SUGGESTED_ROOTS) if Path(p).expanduser().is_dir()],
            "repos": [{"path": repos_mod.tilde(s.path), "state": s.state, "detail": s.detail} for s in states],
            "summary": repos_mod.summarise(states) if states else "no repositories found"}


def repo_preview(folder: str, python: str | None = None) -> dict:
    python = python or sys.executable
    path = Path(folder).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"{folder} is not a folder")
    agent = repos_mod.with_roots(load_settings().agent, [str(path)])
    states = repos_mod.inspect_all(repos_mod.discover(agent), python)
    return {"folder": repos_mod.tilde(path), "count": len(states),
            "too_many": len(states) > repos_mod.MAX_REPOS_WITHOUT_ASKING,
            "summary": repos_mod.summarise(states) if states else "no repositories found",
            "repos": [{"path": repos_mod.tilde(s.path), "state": s.state, "detail": s.detail} for s in states[:200]]}


def repo_add(folder: str, python: str | None = None) -> dict:
    """Watch this folder and add the commit hook to every repository under it (what `saip repo add` does)."""
    python = python or sys.executable
    path = Path(folder).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"{folder} is not a folder")
    cfg = ensure_config()
    agent = load_settings(cfg).agent
    shown = repos_mod.tilde(path)
    same = lambda a, b: Path(a).expanduser().resolve() == Path(b).expanduser().resolve()
    roots = [r for r in agent.watch_roots if not same(r, path)] + [shown]
    exclude = [e for e in agent.exclude if not same(e, path)]
    states = repos_mod.inspect_all(repos_mod.discover(repos_mod.with_roots(agent, roots)), python)
    repos_mod.set_config_list(cfg, "agent", "watch_roots", roots)
    repos_mod.set_config_list(cfg, "agent", "exclude", exclude)
    todo = [s.path for s in states if s.state in (githook.READY, githook.STALE)]
    repos_mod.install_all(todo, python)
    record_decisions(accept=[repos_mod.key(s.path) for s in states])
    agent = load_settings(cfg).agent
    update_manifest(hooked_repos=[str(r) for r in repos_mod.discover(agent) if githook.has_hook(r)])
    return {"ok": True, "detail": f"{len(todo)} hooked under {shown}" if todo else f"nothing new to hook under {shown}"}


def repo_decline(folder: str) -> dict:
    """Never hook the repositories under this folder, and stop asking about them."""
    path = Path(folder).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"{folder} is not a folder")
    agent = repos_mod.with_roots(load_settings().agent, [str(path)])
    found = repos_mod.discover(agent)
    record_decisions(decline=[repos_mod.key(r) for r in found])
    return {"ok": True, "detail": f"{len(found)} repositories under {repos_mod.tilde(path)} will not be hooked"}


def repo_remove(folder: str) -> dict:
    """Stop watching this folder and take the commit hook out of its repositories (what `saip repo remove` does)."""
    settings = load_settings()
    if not settings.path:
        raise ValueError("no private.toml yet")
    agent = settings.agent
    path = Path(folder).expanduser().resolve()
    shown = repos_mod.tilde(path)
    under = [r for r in repos_mod.discover(agent) if r == path or path in r.parents]
    removed = sum(githook.remove(r) for r in under if githook.has_hook(r))
    record_decisions(decline=[repos_mod.key(r) for r in under])
    same = lambda a, b: Path(a).expanduser().resolve() == Path(b).expanduser().resolve()
    roots = [r for r in agent.watch_roots if not same(r, path)]
    exclude = list(agent.exclude)
    if len(roots) == len(agent.watch_roots):
        exclude.append(shown)
    repos_mod.set_config_list(settings.path, "agent", "watch_roots", roots)
    repos_mod.set_config_list(settings.path, "agent", "exclude", exclude)
    return {"ok": True, "detail": f"removed the hook from {removed} repositories under {shown}"}


def finish(plat: Platform, python: str | None = None) -> dict:
    """Mark setup as done. Refused until a model is configured."""
    python = python or sys.executable
    settings = load_settings()
    if not (settings.llm.base_url and settings.llm.model):
        return {"ok": False, "detail": "a model endpoint is required: test and save one first"}
    update_manifest(done=True, platform=plat.name, python=python)
    return {"ok": True}
