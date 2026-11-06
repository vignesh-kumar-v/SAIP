"""Our own watcher: commits, from polling git, written to ActivityWatcher as app.fs.activity events.

Polling means no git hook is installed and nothing global is changed. A repo is only read for its HEAD
commit id; its files and history are never touched or stored.
"""
from __future__ import annotations

import subprocess
import time
from typing import Callable

from .aw import FS, ActivityWatch, AWError, bucket_id, iso


def git_head(repo: str) -> str | None:
    try:
        p = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


class GitWatcher:
    def __init__(self, aw: ActivityWatch, repos, head: Callable[[str], str | None] = git_head):
        self.aw, self.repos, self.head = aw, list(repos), head
        self.last: dict[str, str | None] = {}

    def poll(self, now: float | None = None) -> int:
        """Post one commit event per repo whose HEAD moved since the last poll. The first poll only records."""
        now = time.time() if now is None else now
        posted = 0
        for repo in self.repos:
            head = self.head(repo)
            before = self.last.get(repo)
            self.last[repo] = head
            if head and before and head != before:
                try:
                    bid = bucket_id(FS)
                    self.aw.ensure_bucket(bid, FS[1], FS[0])
                    self.aw.insert(bid, [{"timestamp": iso(now), "duration": 0.0, "data": {"kind": "commit"}}])
                    posted += 1
                except AWError:
                    pass
        return posted
