"""Asking whether a newly found repository should be added to SAIP.

While `saip run` is going it rescans your chosen folders. A repository it has not seen before is never hooked
on its own: a dialog (or, where the system cannot show one, a terminal question) asks Add / Not now / Never.
Add hooks it (or polls it, where a hook cannot be installed), Never remembers the answer, and Not now simply
asks again next time. Questions are asked one at a time on a background thread, so monitoring never waits
for you.
"""
from __future__ import annotations

import queue
import threading
from typing import Callable

from llm.config import AgentConfig

from . import repos as repos_mod
from .hooks import githook
from .hooks.githook import READY, RepoState
from .platform import Platform
from .setup import record_decisions

ADD, LATER, NEVER = "Add", "Not now", "Never"


def question(state: RepoState) -> tuple[str, str]:
    where = repos_mod.tilde(state.path)
    how = ("SAIP will add a small git hook that records only the word 'commit' when you commit." if state.state == READY
           else f"A hook cannot be added here ({state.detail}), so SAIP would check it for new commits every few seconds instead.")
    return "SAIP found a new git repository", f"{where}\n\nAdd it to SAIP?\n{how}"


class RepoPrompter:
    def __init__(self, plat: Platform, python: str, policy: str = "ask",
                 on_accepted: Callable[[RepoState, bool], None] | None = None,
                 terminal: Callable[[str], str] = input, out: Callable[[str], None] = print):
        self.plat, self.python, self.policy = plat, python, policy
        self.on_accepted, self.terminal, self.out = on_accepted, terminal, out
        self.snoozed: set[str] = set()          # "Not now": not asked again until the next run
        self.queued: set[str] = set()
        self.q: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None

    # ---- the decision itself ----
    def ask(self, state: RepoState) -> str:
        title, message = question(state)
        if self.plat.can_ask():
            answer = self.plat.ask(title, message, (NEVER, LATER, ADD))
            if answer:
                return answer
            return LATER                          # dialog timed out or was dismissed: ask again next run
        reply = self.terminal(f"\n{title}: {message}\n[a]dd / [n]ot now / n[e]ver > ").strip().lower()
        return ADD if reply in ("a", "add", "y", "yes") else NEVER if reply in ("e", "never") else LATER

    def handle(self, state: RepoState) -> str:
        k = repos_mod.key(state.path)
        answer = ADD if self.policy == "add" else self.ask(state)
        if answer == ADD:
            hooked = githook.install(state.path, self.python).state == githook.INSTALLED
            record_decisions(accept=[k])
            self.out(f"  added {repos_mod.tilde(state.path)} ({'commit hook installed' if hooked else 'checked by polling'})")
            if self.on_accepted:
                self.on_accepted(state, hooked)
        elif answer == NEVER:
            record_decisions(decline=[k])
            self.out(f"  will not ask about {repos_mod.tilde(state.path)} again (undo with `saip repo add`)")
        else:
            self.snoozed.add(k)
        return answer

    # ---- background asking ----
    def submit(self, state: RepoState) -> bool:
        k = repos_mod.key(state.path)
        if self.policy == "ignore" or k in self.snoozed or k in self.queued:
            return False
        self.queued.add(k)
        self.q.put(state)
        return True

    def _worker(self) -> None:
        while True:
            state = self.q.get()
            if state is None:
                return
            try:
                self.handle(state)
            finally:
                self.queued.discard(repos_mod.key(state.path))

    def start(self) -> None:
        self.thread = threading.Thread(target=self._worker, daemon=True, name="saip-repo-prompts")
        self.thread.start()

    def stop(self) -> None:
        self.q.put(None)
