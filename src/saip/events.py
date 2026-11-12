"""What the live loop tells a UI, and how a UI answers its questions.

The loop publishes small dict events (tick, decision, offer, outcome, help, status) to a Bus. The local server
(saip/server.py) streams them to whatever is listening: the dashboard, a tray popup, a test. Events hold the same
things the terminal prints and `offers.jsonl` records: scores, the gate's numbers and offer text. No window titles,
no activity summary, nothing typed.

Questions go the other way. `Bus.ask` publishes an "ask" event and blocks until a listener answers it or it times
out, which is exactly the contract of `Platform.ask`. `UiPlatform` wraps the real platform so offers, the help text
and the new-repository question appear in the UI when one is connected and fall back to the system dialog when not.
"""
from __future__ import annotations

import itertools
import queue
import threading
import time
from collections import deque

TICKS_KEPT = 720             # an hour at one tick per 5 s
LOG_KEPT = 200               # everything that is not a tick


class Bus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        self._subs: set[queue.Queue] = set()
        self._tray: set[queue.Queue] = set()      # subscribers that are the tray, not a dashboard page
        self._ticks: deque = deque(maxlen=TICKS_KEPT)
        self._log: deque = deque(maxlen=LOG_KEPT)
        self._asks: dict[int, dict] = {}          # open questions: id -> {"event", "answer", "done"}
        self.quiet_until = 0.0                    # no new offers before this time (the tray's "pause offers")
        self.meta: dict = {}                      # facts about this run a UI should show, e.g. {"demo": True}

    # ---- publishing ----
    def publish(self, kind: str, **data) -> dict:
        ev = {"id": next(self._seq), "kind": kind, "at": data.pop("at", None) or time.time(), **data}
        with self._lock:
            (self._ticks if kind == "tick" else self._log).append(ev)
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(ev)
            except queue.Full:                    # a stalled listener must never slow the monitor
                pass
        return ev

    # ---- listening ----
    def subscribe(self, tray: bool = False) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subs.add(q)
            if tray:
                self._tray.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)
            self._tray.discard(q)

    @property
    def pages(self) -> int:
        """Dashboard pages open right now (the tray does not count)."""
        with self._lock:
            return len(self._subs - self._tray)

    @property
    def listeners(self) -> int:
        with self._lock:
            return len(self._subs)

    def snapshot(self) -> dict:
        """Everything a UI needs to draw itself when it connects part-way through."""
        with self._lock:
            return {"ticks": list(self._ticks), "log": list(self._log),
                    "asks": [a["event"] for a in self._asks.values()],
                    "quiet_until": self.quiet_until, "meta": dict(self.meta)}

    # ---- holding offers back ----
    def quiet(self, minutes: float, now: float | None = None) -> float:
        """Hold new offers back for this many minutes (0 lets them through again). Returns the end time."""
        now = time.time() if now is None else now
        self.quiet_until = now + 60.0 * minutes if minutes > 0 else 0.0
        self.publish("quiet", until=self.quiet_until)
        return self.quiet_until

    # ---- questions ----
    def ask(self, title: str, message: str, buttons: tuple, timeout: float) -> str | None:
        """Show a question to the listeners and wait. The button pressed, or None if nobody answered in time."""
        done = threading.Event()
        with self._lock:
            ask_id = next(self._seq)
            entry = {"answer": None, "done": done, "event": {
                "id": ask_id, "kind": "ask", "at": time.time(), "title": title, "message": message,
                "buttons": list(buttons), "timeout_s": float(timeout)}}
            self._asks[ask_id] = entry
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(entry["event"])
            except queue.Full:
                pass
        done.wait(timeout)
        with self._lock:
            self._asks.pop(ask_id, None)
        self.publish("ask-closed", ask=ask_id, answer=entry["answer"])
        return entry["answer"]

    def answer(self, ask_id: int, button: str) -> bool:
        """A listener pressed a button. False if the question is gone or has no such button."""
        with self._lock:
            entry = self._asks.get(ask_id)
            if entry is None or button not in entry["event"]["buttons"] or entry["done"].is_set():
                return False
            entry["answer"] = button
            entry["done"].set()
        return True


class UiPlatform:
    """The real platform, with its dialogs shown in the UI whenever one is listening."""

    def __init__(self, plat, bus: Bus):
        self._plat, self._bus = plat, bus

    def __getattr__(self, name):
        return getattr(self._plat, name)

    def can_ask(self) -> bool:
        return True

    def ask(self, title: str, message: str, buttons: tuple = ("Never", "Not now", "Add"), timeout: int = 120,
            default: bool = True) -> str | None:
        if self._bus.listeners:
            return self._bus.ask(title, message, buttons, timeout)
        if self._plat.can_ask():
            return self._plat.ask(title, message, buttons, timeout=timeout, default=default)
        return None
