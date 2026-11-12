"""Conversations with the model, kept in memory for this run (nothing is written to disk).

A chat has two records. `items` is what the page shows: the person's messages, the model's, the tools it used and the
actions it proposed (with whether they were approved). `wire` is what the model is sent. Each of the person's messages
remembers how long `wire` was before it, so rewinding to it also takes the model's memory back to that point.
"""
from __future__ import annotations

import copy
import itertools
import threading
import time

MAX_CHATS = 20
MAX_MESSAGE = 4000
WEB_TOOLS = {"web_search": "A web search", "fetch_page": "A page that was opened"}
ACTION_FIELDS = ("name", "title", "path", "content", "diff", "exists", "state", "detail")


def _undo_write(undo: dict) -> tuple[bool, str]:
    from .chat_tools import undo_write
    return undo_write(undo)


class Chats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._chats: dict[int, dict] = {}

    def create(self, title: str, context: str = "", screen: dict | None = None, first: str | None = None,
               live: bool = False, looked: list[dict] | None = None) -> int:
        """`live`: started from the page, so the activity summary is refreshed on every message.
        `looked`: what the model read while writing `first` ({"name", "label", "ok"}), shown above it."""
        with self._lock:
            cid = next(self._ids)
            now = time.time()
            self._chats[cid] = {"id": cid, "title": (title or "New chat")[:80], "created": now, "context": context,
                                "screen": screen, "live": live, "first": first or None, "items": [], "wire": [],
                                "next_item": 1}
            for e in looked or []:
                self._append(self._chats[cid], "tool", e["label"], tool={"name": e["name"], "label": e["label"], "ok": e["ok"]})
            if first:
                self._append(self._chats[cid], "assistant", first)
            for old in sorted(self._chats)[:-MAX_CHATS]:
                del self._chats[old]
            return cid

    @staticmethod
    def _append(c: dict, kind: str, text: str, **fields) -> dict:
        item = {"id": c["next_item"], "kind": kind, "at": time.time(), "text": text, **fields}
        c["next_item"] += 1
        c["items"].append(item)
        return item

    def get(self, cid: int) -> dict | None:
        with self._lock:
            c = self._chats.get(cid)
            if c is None:
                return None
            return {**c, "items": copy.deepcopy(c["items"]), "wire": list(c["wire"]), "messages": self._messages(c)}

    @staticmethod
    def _messages(c: dict) -> list[dict]:
        return [{"role": i["kind"], "text": i["text"], "at": i["at"]} for i in c["items"]
                if i["kind"] in ("user", "assistant")]

    def set_context(self, cid: int, context: str) -> None:
        with self._lock:
            self._chats[cid]["context"] = context

    def add_item(self, cid: int, kind: str, text: str, **fields) -> int:
        with self._lock:
            return self._append(self._chats[cid], kind, text[:MAX_MESSAGE], **fields)["id"]

    def record(self, cid: int, wire: list[dict], items: list[dict], start: int | None = None) -> None:
        """Add a finished turn and the wire that goes with it. `items` are {"kind", "text", ...fields};
        a "user" item remembers `start`, the length of the wire before the turn."""
        with self._lock:
            c = self._chats[cid]
            for it in items:
                fields = {k: v for k, v in it.items() if k not in ("kind", "text")}
                if it["kind"] == "user":
                    fields["_w"] = start if start is not None else len(c["wire"])
                self._append(c, it["kind"], it["text"][:MAX_MESSAGE], **fields)
            c["wire"] = list(wire)

    def set_wire(self, cid: int, wire: list[dict]) -> None:
        with self._lock:
            self._chats[cid]["wire"] = list(wire)

    def claim_action(self, cid: int, item_id: int) -> bool:
        """True for the one caller that may now decide this pending action."""
        with self._lock:
            item = self._find(self._chats[cid], item_id)
            if item is None or item["kind"] != "action" or item["action"]["state"] != "pending" or item.get("_busy"):
                return False
            item["_busy"] = True
            return True

    def set_action(self, cid: int, item_id: int, state: str, detail: str | None = None, undo: dict | None = None) -> None:
        with self._lock:
            item = self._find(self._chats[cid], item_id)
            if item is not None:
                item["action"].update(state=state, detail=detail)
                item["_undo"] = undo
                item.pop("_busy", None)

    @staticmethod
    def _find(c: dict, item_id: int) -> dict | None:
        return next((i for i in c["items"] if i["id"] == item_id), None)

    @staticmethod
    def pending(c: dict) -> dict | None:
        return next((i for i in c["items"] if i["kind"] == "action" and i["action"]["state"] == "pending"), None)

    def listing(self) -> list[dict]:
        with self._lock:
            rows = []
            for c in sorted(self._chats.values(), key=lambda c: -c["created"]):
                said = self._messages(c)
                rows.append({"id": c["id"], "title": c["title"], "created": c["created"], "count": len(said),
                             "last": said[-1]["text"][:90] if said else ""})
            return rows

    @staticmethod
    def view(c: dict) -> dict:
        """What the page may see: no background context, no model wire, no arguments or undo records."""
        items = []
        for i in c["items"]:
            row = {"id": i["id"], "kind": i["kind"], "at": i["at"], "text": i["text"]}
            if i["kind"] == "tool":
                row["tool"] = {k: i["tool"].get(k) for k in ("name", "label", "ok")}
            elif i["kind"] == "action":
                row["action"] = {**{k: i["action"].get(k) for k in ACTION_FIELDS},
                                 "undoable": i["action"]["state"] == "approved" and bool(i.get("_undo"))}
            items.append(row)
        return {"id": c["id"], "title": c["title"], "created": c["created"], "has_screen": bool(c.get("screen")),
                "tools": [], "items": items, "messages": Chats._messages(c)}

    # ---- rewinding ----
    @staticmethod
    def _tail(c: dict, item_id: int) -> tuple[int, list[dict]]:
        idx = next((n for n, i in enumerate(c["items"]) if i["id"] == item_id), None)
        if idx is None:
            raise KeyError("no such message")
        if c["items"][idx]["kind"] != "user":
            raise ValueError("you can only go back to one of your own messages")
        return idx, c["items"][idx:]

    @staticmethod
    def _undoable(tail: list[dict]) -> list[dict]:
        return [i for i in reversed(tail) if i["kind"] == "action" and i["action"]["name"] == "write_file"
                and i["action"]["state"] == "approved" and i.get("_undo")]

    def rewind_preview(self, cid: int, item_id: int) -> dict:
        with self._lock:
            c = self._chats[cid]
            _, tail = self._tail(c, item_id)
            undo = [{"path": i["_undo"]["path"], "effect": "restore" if i["_undo"].get("existed") else "delete"}
                    for i in self._undoable(tail)]
            cannot = []
            for i in tail:
                if i["kind"] == "action" and i["action"]["name"] == "copy_to_clipboard" \
                        and i["action"]["state"] == "approved":
                    cannot.append("A copy to the clipboard.")
                elif i["kind"] == "tool" and i["tool"]["name"] in WEB_TOOLS:
                    cannot.append(f"{WEB_TOOLS[i['tool']['name']]}: {i['tool']['label']}")
            return {"item": item_id, "drops": len(tail), "undo": undo, "cannot_undo": cannot}

    def rewind(self, cid: int, item_id: int, undo=_undo_write) -> dict:
        """Put files back (newest write first), then drop the message and everything after it, wire included."""
        with self._lock:
            _, tail = self._tail(self._chats[cid], item_id)
            records = [i["_undo"] for i in self._undoable(tail)]
        done, skipped = [], []
        for rec in records:                                     # touching files: not under the lock
            ok, message = undo(rec)
            (done if ok else skipped).append(message)
        with self._lock:
            c = self._chats[cid]
            idx, _ = self._tail(c, item_id)
            c["wire"] = c["wire"][:c["items"][idx]["_w"]]
            del c["items"][idx:]
        return {"undone": done, "skipped": skipped}
