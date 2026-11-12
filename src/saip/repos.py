"""Finding the git repositories under the folders you chose, and keeping the commit hook installed in them.

You give a folder; every repository under it is found and hooked. Finding reads directory names only:
no file contents, no git history. Hooking follows the safety rules in hooks/githook.py, and repositories
where a hook cannot be added safely are listed so they can be polled instead.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from llm.config import AgentConfig

from .hooks import githook
from .hooks.githook import INSTALLED, READY, SKIP, STALE, RepoState

MAX_DEPTH = 4
MAX_REPOS_WITHOUT_ASKING = 25
SKIP_DIRS = frozenset({"node_modules", ".git", "__pycache__", "venv", "env", "build", "dist", "target", "site-packages",
                       "Library", "Applications", "AppData", "Pictures", "Movies", "Music", "Public", "Trash",
                       "vendor", "bower_components"})


def tilde(path: Path) -> str:
    """Show a path the way a person would type it: ~/code, not /Users/me/code."""
    try:
        return "~/" + str(Path(path).resolve().relative_to(Path.home()))
    except ValueError:
        return str(path)


def _resolve(p) -> Path:
    return Path(p).expanduser().resolve()


def find_repos(roots, exclude=(), max_depth: int = MAX_DEPTH) -> list[Path]:
    """Every folder containing `.git` under the roots, not descending into a repo once it is found."""
    excluded = [_resolve(e) for e in exclude]
    found: list[Path] = []

    def is_excluded(d: Path) -> bool:
        return any(d == e or e in d.parents for e in excluded)

    def walk(d: Path, depth: int) -> None:
        if is_excluded(d):
            return
        if (d / ".git").exists():
            found.append(d)
            return
        if depth >= max_depth:
            return
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False) and e.name not in SKIP_DIRS and not e.name.startswith("."):
                    walk(Path(e.path), depth + 1)
            except OSError:
                continue

    for root in roots:
        r = _resolve(root)
        if r.is_dir():
            walk(r, 0)
    return list(dict.fromkeys(found))


def discover(agent: AgentConfig) -> list[Path]:
    """Individually listed repos plus everything found under the watch roots, minus exclusions."""
    excluded = [_resolve(e) for e in agent.exclude]
    explicit = [_resolve(r) for r in agent.repos if (_resolve(r) / ".git").exists()]
    explicit = [r for r in explicit if not any(r == e or e in r.parents for e in excluded)]
    return list(dict.fromkeys(explicit + find_repos(agent.watch_roots, agent.exclude)))


def inspect_all(repos, python: str) -> list[RepoState]:
    return [githook.inspect(r, python) for r in repos]


def install_all(repos, python: str) -> list[RepoState]:
    return [githook.install(r, python) for r in repos]


def fallback_repos(states: list[RepoState]) -> list[Path]:
    """Real repos where no hook can be installed: these are still watched by polling."""
    return [s.path for s in states if s.state == SKIP and s.detail != "not a git repository"]


def key(path) -> str:
    return str(Path(path).resolve())


@dataclass
class SyncPlan:
    refresh: list[RepoState]      # accepted earlier, but the hook is missing or stale: put it back quietly
    new: list[RepoState]          # found, and neither accepted nor declined yet: these are what to ask about
    hooked: list[Path]            # accepted and hooked
    polled: list[Path]            # accepted, but a hook cannot be installed: watched by polling


def plan_sync(agent: AgentConfig, python: str, accepted: set, declined: set) -> SyncPlan:
    """Decide what the watcher should do about every repository under the chosen folders."""
    states = inspect_all(discover(agent), python)
    mine = [s for s in states if key(s.path) in accepted]
    return SyncPlan(
        refresh=[s for s in mine if s.state in (STALE, READY)],
        new=[s for s in states if key(s.path) not in accepted and key(s.path) not in declined],
        hooked=[s.path for s in mine if s.state == INSTALLED],
        polled=[s.path for s in mine if s.state == SKIP and s.detail != "not a git repository"])


def summarise(states: list[RepoState]) -> str:
    counts = {k: sum(s.state == k for s in states) for k in (INSTALLED, STALE, READY, SKIP)}
    parts = [f"{counts[INSTALLED]} hooked", f"{counts[READY] + counts[STALE]} to hook", f"{counts[SKIP]} skipped"]
    return ", ".join(parts)


def describe(states: list[RepoState]) -> list[str]:
    lines = []
    for s in states:
        note = {INSTALLED: "hook installed", STALE: "hook points at an old Python: run `saip setup`",
                READY: "can be hooked"}.get(s.state, "skipped: " + s.detail)
        lines.append(f"  {tilde(s.path)}: {note}")
    return lines


# ---- editing the one list in private.toml that we manage -------------------------------------------------

def set_config_list(path: Path, section: str, key: str, values: list[str]) -> None:
    """Set `key = [...]` inside `[section]`, keeping everything else in the file as it was."""
    from .config_file import set_value
    set_value(path, section, key, list(values))


def with_roots(agent: AgentConfig, roots: list[str]) -> AgentConfig:
    return replace(agent, watch_roots=tuple(roots))
