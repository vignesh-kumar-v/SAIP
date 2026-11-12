"""Conversations with the model, kept in memory for this run (nothing is written to disk)."""
from __future__ import annotations

import itertools
import threading
import time

MAX_CHATS = 20
MAX_MESSAGE = 4000


class Chats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._chats: dict[int, dict] = {}

    def create(self, title: str, context: str = "", screen: dict | None = None, first: str | None = None,
               live: bool = False) -> int:
        """`live`: started from the page, so the activity summary is refreshed on every message."""
        with self._lock:
            cid = next(self._ids)
            now = time.time()
            self._chats[cid] = {"id": cid, "title": (title or "New chat")[:80], "created": now, "context": context,
                                "screen": screen, "live": live, "messages": []}
            if first:
                self._chats[cid]["messages"].append({"role": "assistant", "text": first, "at": now})
            for old in sorted(self._chats)[:-MAX_CHATS]:
                del self._chats[old]
            return cid

    def get(self, cid: int) -> dict | None:
        with self._lock:
            c = self._chats.get(cid)
            return None if c is None else {**c, "messages": list(c["messages"])}

    def set_context(self, cid: int, context: str) -> None:
        with self._lock:
            self._chats[cid]["context"] = context

    def add(self, cid: int, role: str, text: str) -> None:
        with self._lock:
            self._chats[cid]["messages"].append({"role": role, "text": text[:MAX_MESSAGE], "at": time.time()})

    def listing(self) -> list[dict]:
        with self._lock:
            return [{"id": c["id"], "title": c["title"], "created": c["created"], "count": len(c["messages"]),
                     "last": (c["messages"][-1]["text"][:90] if c["messages"] else "")}
                    for c in sorted(self._chats.values(), key=lambda c: -c["created"])]

    @staticmethod
    def view(c: dict) -> dict:
        """What the page may see: no background context, only the transcript."""
        return {"id": c["id"], "title": c["title"], "created": c["created"], "messages": c["messages"],
                "has_screen": bool(c.get("screen"))}
