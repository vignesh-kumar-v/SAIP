"""A `post-commit` hook that tells SAIP a commit happened.

Git runs `.git/hooks/post-commit` after every real commit (including amends and merge commits, but not
branch switches, pulls or rebases). Our block goes at the top of that file, between marker lines:

* an existing hook keeps all of its own lines; ours is added and can be removed again exactly;
* it runs in the background and ignores errors, so it can never slow down or break a commit;
* it sends only the word "commit": no message, diff or file names.

Repos where adding a block is unsafe are skipped (and polled instead): a repo that sets its own
`core.hooksPath` (husky and similar generate those files), a hook written in another language, and a
hook that is not executable (git ignores those on purpose).
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .shells import BEGIN, END, has_block, strip_block

HOOK_NAME = "post-commit"
NOTE = ('# Tells SAIP that a commit happened (just the word "commit": no message, diff or file names). '
        "Remove: saip repo remove")
SHELLS = re.compile(r"\b(sh|bash|zsh|dash|ash)\b")

INSTALLED, STALE, READY, SKIP = "installed", "stale", "ready", "skip"


@dataclass(frozen=True)
class RepoState:
    path: Path
    state: str           # installed | stale (hook points at an old Python) | ready (can install) | skip (cannot)
    detail: str = ""


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)
    return p.returncode, p.stdout.strip()


def hooks_dir(repo: Path) -> Path | None:
    code, out = _git(repo, "rev-parse", "--git-path", "hooks")      # handles worktrees and submodules
    if code != 0 or not out:
        return None
    p = Path(out)
    return p if p.is_absolute() else Path(repo) / p


def hook_block(python: str) -> str:
    return f'{BEGIN}\n{NOTE}\n"{Path(python).as_posix()}" -m saip.hooks commit >/dev/null 2>&1 &\n{END}\n'


def _with_block(text: str, python: str) -> str:
    text = strip_block(text) if has_block(text) else text
    block = hook_block(python)
    if not text:
        return "#!/bin/sh\n" + block
    if text.startswith("#!"):
        first, _, rest = text.partition("\n")
        return first + "\n" + block + rest
    return block + text


def _python_works(text: str, python: str) -> bool:
    """The hook is fine if it names this Python, or any Python that still exists and can be run.

    `.venv/bin/python` and `.venv/bin/python3.12` are the same interpreter under two names, so comparing the
    text alone would call a working hook stale. It is only stale when the interpreter it points at is gone.
    """
    m = re.search(r'"([^"\n]+)" -m saip\.hooks commit', text)
    if not m:
        return False
    named = m.group(1)
    if named == Path(python).as_posix():
        return True
    p = Path(named)
    return p.exists() and os.access(p, os.X_OK)


def inspect(repo: Path, python: str) -> RepoState:
    repo = Path(repo)
    if not (repo / ".git").exists():
        return RepoState(repo, SKIP, "not a git repository")
    code, out = _git(repo, "config", "--get", "core.hooksPath")
    if code == 0 and out:
        return RepoState(repo, SKIP, "sets its own core.hooksPath (husky or similar): those hook files are generated")
    d = hooks_dir(repo)
    if d is None:
        return RepoState(repo, SKIP, "cannot find the hooks folder")
    path = d / HOOK_NAME
    if path.exists():
        text = path.read_text(errors="replace")
        if has_block(text):
            return RepoState(repo, INSTALLED if _python_works(text, python) else STALE)
        first = text.split("\n", 1)[0]
        if first.startswith("#!") and not SHELLS.search(first):
            return RepoState(repo, SKIP, "existing post-commit hook is not a shell script")
        if os.name != "nt" and not os.access(path, os.X_OK):
            return RepoState(repo, SKIP, "existing post-commit hook is not executable, so git ignores it")
    return RepoState(repo, READY)


def install(repo: Path, python: str) -> RepoState:
    st = inspect(repo, python)
    if st.state == SKIP:
        return st
    path = hooks_dir(Path(repo)) / HOOK_NAME
    old = path.read_text(errors="replace") if path.exists() else ""
    new = _with_block(old, python)
    if new == old:
        return RepoState(st.path, INSTALLED, "unchanged")
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    path.write_text(new)
    if not existed:
        path.chmod(path.stat().st_mode | 0o755)
    return RepoState(st.path, INSTALLED, "updated" if has_block(old) else "added")


def has_hook(repo: Path) -> bool:
    d = hooks_dir(Path(repo)) if (Path(repo) / ".git").exists() else None
    path = d / HOOK_NAME if d else None
    return bool(path and path.exists() and has_block(path.read_text(errors="replace")))


def remove(repo: Path) -> bool:
    d = hooks_dir(Path(repo))
    path = d / HOOK_NAME if d else None
    if not path or not path.exists():
        return False
    text = path.read_text(errors="replace")
    if not has_block(text):
        return False
    rest = strip_block(text)
    if rest.strip() in ("", "#!/bin/sh"):          # we created the file and nothing else is in it
        path.unlink()
    else:
        path.write_text(rest)
    return True
