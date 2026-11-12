"""Showing an offer, learning what the user did with it, and keeping a private record.

An offer ends in one of three ways, as in thunlp/ProactiveAgent:

    accepted   the user pressed the "yes" button
    dismissed  the user pressed the "no" button
    ignored    nothing was pressed before the dialog timed out

Where the system cannot show a dialog the offer is printed and the outcome is recorded as "unanswered", which
changes nothing. The dialog has no default button, so a Return key pressed by someone still typing cannot answer
for them. The dialog runs on a background thread, so monitoring never waits for it.

The record is one JSON line per offer in the settings folder. It holds the gate's numbers, the offer text and the
outcome: no window titles, no activity summary, nothing you typed. It is the data later calibration will fit on.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from llm.config import user_config_dir
from llm.gate import Decision
from llm.proposal import Proposal

from .platform import Platform

ACCEPTED, DISMISSED, IGNORED, UNANSWERED = "accepted", "dismissed", "ignored", "unanswered"
OUTCOMES = (ACCEPTED, DISMISSED, IGNORED, UNANSWERED)
YES, NO = "Help me", "Not now"
TITLE = "SAIP"


def log_path() -> Path:
    return user_config_dir() / "offers.jsonl"


@dataclass(frozen=True)
class Offer:
    time: float
    stuck_score: float
    p_need: float
    p_accept: float
    bar: float
    resampled: bool
    proposal: Proposal


def record(offer: Offer, outcome: str, answered_after_s: float | None) -> dict:
    d, p = offer, offer.proposal
    return {"time": round(d.time, 1), "stuck_score": round(d.stuck_score, 4), "p_need": round(d.p_need, 4),
            "p_accept": round(d.p_accept, 4), "bar": round(d.bar, 4), "resampled": d.resampled,
            "task": p.task, "message": p.message, "generated": p.generated, "outcome": outcome,
            "answered_after_s": None if answered_after_s is None else round(answered_after_s, 1)}


def make_offer(now: float, decision: Decision, resampled: bool, proposal: Proposal) -> Offer:
    return Offer(now, decision.stuck_score, decision.p_need, decision.p_accept, decision.tau, resampled, proposal)


def append_log(row: dict, path: Path | None = None) -> None:
    path = path or log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def decisions_path() -> Path:
    """Every gate decision, not only the ones that became offers: numbers and the app in front, no text."""
    return user_config_dir() / "decisions.jsonl"


def append_decision(row: dict, path: Path | None = None) -> None:
    append_log(row, path or decisions_path())


def read_decisions(path: Path | None = None) -> list[dict]:
    return read_log(path or decisions_path())


def read_log(path: Path | None = None) -> list[dict]:
    path = path or log_path()
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue                                  # a half-written last line must not hide the rest
    return rows


def summarise(rows: list[dict]) -> str:
    if not rows:
        return "no offers recorded yet"
    counts = {o: sum(r.get("outcome") == o for r in rows) for o in OUTCOMES}
    answered = counts[ACCEPTED] + counts[DISMISSED]
    rate = f", accepted {counts[ACCEPTED] / answered:.0%} of those answered" if answered else ""
    return f"{len(rows)} offers: " + ", ".join(f"{counts[o]} {o}" for o in OUTCOMES if counts[o]) + rate


class OfferPresenter:
    """Shows offers one at a time on a background thread and reports each outcome through `on_outcome`."""

    def __init__(self, plat: Platform, timeout_s: float = 45.0, use_dialog: bool = True,
                 on_outcome: Callable[[Offer, str], None] | None = None, log: Path | None = None):
        self.plat, self.timeout_s = plat, timeout_s
        self.dialog = use_dialog and plat.can_ask()
        self.on_outcome, self.log = on_outcome, log
        self.q: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None

    def question(self, offer: Offer) -> str:
        return offer.proposal.message

    def ask(self, offer: Offer) -> tuple[str, float | None]:
        """Show one offer and wait for the answer. Returns (outcome, seconds taken to answer)."""
        if not self.dialog:
            return UNANSWERED, None
        began = time.time()
        answer = self.plat.ask(TITLE, self.question(offer), (NO, YES), timeout=int(self.timeout_s), default=False)
        took = time.time() - began
        return {YES: ACCEPTED, NO: DISMISSED}.get(answer, IGNORED), (took if answer else None)

    def handle(self, offer: Offer) -> str:
        outcome, took = self.ask(offer)
        append_log(record(offer, outcome, took), self.log)
        if self.on_outcome:
            self.on_outcome(offer, outcome)
        return outcome

    def submit(self, offer: Offer) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self._worker, daemon=True, name="saip-offers")
            self.thread.start()
        self.q.put(offer)

    def _worker(self) -> None:
        while True:
            offer = self.q.get()
            if offer is None:
                return
            try:
                self.handle(offer)
            except Exception:                         # a failed dialog must never take the monitor down
                pass

    def stop(self) -> None:
        self.q.put(None)
