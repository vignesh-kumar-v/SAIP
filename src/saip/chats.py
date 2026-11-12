"""Conversations with the model. By default they live in memory for this run and nothing is written to disk.

With "keep chats between runs" on (`keep_chats` in private.toml), every change is also saved to `chats.jsonl` in the
settings folder (one conversation per line, readable only by you) and loaded again at the next start. Turning it off
deletes that file.

A chat has two records. `items` is what the page shows: the person's messages, the model's, the tools it used and the
actions it proposed (with whether they were approved). `wire` is what the model is sent. Each of the person's messages
remembers how long `wire` was before it, so rewinding to it also takes the model's memory back to that point.
"""
from __future__ import annotations

import copy
import itertools
import json
import threading
import time
from pathlib import Path

MAX_CHATS = 20
MAX_MESSAGE = 4000
WEB_TOOLS = {"web_search": "A web search", "fetch_page": "A page that was opened"}
ACTION_FIELDS = ("name", "title", "path", "content", "diff", "exists", "state", "detail")


def _undo_write(undo: dict) -> tuple[bool, str]:
    from .chat_tools import undo_write
    return undo_write(undo)


class Chats:
    def __init__(self, path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._chats: dict[int, dict] = {}
        self._path: Path | None = None
        if path is not None:
            self.configure(path)

    # ---- keeping them between runs ----
    @property
    def path(self) -> Path | None:
        return self._path

    def configure(self, path: Path | None) -> None:
        """Start saving to `path` (loading what is there first), or stop and delete the file when `path` is None."""
        with self._lock:
            if path is None:
                if self._path is not None:
                    self._path.unlink(missing_ok=True)
                self._path = None
                return
            path = Path(path)
            if self._path == path:
                return
            self._path = path
            if not self._chats:
                self._load()
            else:
                self._save()

    def clear(self) -> int:
        """Forget every conversation (and the saved file's contents)."""
        with self._lock:
            n = len(self._chats)
            self._chats.clear()
            self._save()
            return n

    def delete(self, ids) -> int:
        """Forget the conversations with these ids (and save). Ids that are already gone are ignored.
        Files a chat wrote stay as they are: deleting a chat is not a rewind."""
        with self._lock:
            gone = [cid for cid in set(ids) if self._chats.pop(cid, None) is not None]
            if gone:
                self._save()
            return len(gone)

    def _load(self) -> None:
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            try:
                c = json.loads(line)
                cid = int(c["id"])
                assert isinstance(c["items"], list) and isinstance(c["wire"], list)
            except (ValueError, KeyError, TypeError, AssertionError):
                continue                                          # one damaged line must not hide the rest
            for i in c["items"]:
                i.pop("_busy", None)                              # an approval interrupted by a restart is retried
            self._chats[cid] = c
        for old in sorted(self._chats)[:-MAX_CHATS]:
            del self._chats[old]
        self._ids = itertools.count(max(self._chats, default=0) + 1)

    def _save(self) -> None:
        """Write everything (the lock is held). A failed write is ignored: the chat itself must not break."""
        if self._path is None:
            return
        from .config_file import write_private
        try:
            body = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in sorted(self._chats.values(), key=lambda c: c["id"]))
            write_private(self._path, body)
        except (OSError, TypeError, ValueError):
            pass

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
            self._save()
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
            if cid in self._chats:
                self._chats[cid]["context"] = context
                self._save()

    def add_item(self, cid: int, kind: str, text: str, **fields) -> int:
        with self._lock:
            item_id = self._append(self._chats[cid], kind, text[:MAX_MESSAGE], **fields)["id"]
            self._save()
            return item_id

    def record(self, cid: int, wire: list[dict], items: list[dict], start: int | None = None) -> None:
        """Add a finished turn and the wire that goes with it. `items` are {"kind", "text", ...fields};
        a "user" item remembers `start`, the length of the wire before the turn."""
        with self._lock:
            c = self._chats.get(cid)
            if c is None:                                         # deleted while the model was answering
                return
            for it in items:
                fields = {k: v for k, v in it.items() if k not in ("kind", "text")}
                if it["kind"] == "user":
                    fields["_w"] = start if start is not None else len(c["wire"])
                self._append(c, it["kind"], it["text"][:MAX_MESSAGE], **fields)
            c["wire"] = list(wire)
            self._save()

    def set_wire(self, cid: int, wire: list[dict]) -> None:
        with self._lock:
            if cid in self._chats:
                self._chats[cid]["wire"] = list(wire)
                self._save()

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
            c = self._chats.get(cid)
            item = self._find(c, item_id) if c is not None else None
            if item is not None:
                item["action"].update(state=state, detail=detail)
                item["_undo"] = undo
                item.pop("_busy", None)
                self._save()

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
            c = self._chats.get(cid)
            if c is None:                                         # deleted while the files were being put back
                return {"undone": done, "skipped": skipped}
            idx, _ = self._tail(c, item_id)
            c["wire"] = c["wire"][:c["items"][idx]["_w"]]
            del c["items"][idx:]
            self._save()
        return {"undone": done, "skipped": skipped}
