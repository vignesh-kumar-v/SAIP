"""Read an ActivityWatch export, real or synthetic, into time-sorted streams.

Buckets are matched by type, not id: a hostname change on a real machine creates a second set of
buckets, so all buckets of one type are merged. Exports store events newest first, so everything
is sorted here. The web bucket is ignored on purpose: window events already carry the URL, and web
events keep counting while the browser is in the background.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

STREAMS = {
    "currentwindow": "window",
    "afkstatus": "afk",
    "os.hid.input": "input",
    "app.terminal.command": "terminal",       # custom hook, absent from a stock ActivityWatch install
    "app.fs.activity": "fs",                  # custom hook
}
REQUIRED = ("window", "afk", "input")


@dataclass
class Trace:
    streams: dict = field(default_factory=dict)      # name -> [{"t": epoch seconds, "duration": s, "data": {...}}]
    hooks: bool = False                              # a terminal bucket exists, so failures can be observed


def _epoch(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def load_export(path: str | Path) -> Trace:
    buckets = json.loads(Path(path).read_text(encoding="utf-8"))["buckets"]
    streams: dict[str, list] = {name: [] for name in STREAMS.values()}
    hooks = False
    for bucket in buckets.values():
        name = STREAMS.get(bucket.get("type"))
        if name is None:
            continue
        hooks = hooks or name == "terminal"
        streams[name].extend(dict(t=_epoch(e["timestamp"]), duration=float(e["duration"]), data=e["data"])
                             for e in bucket["events"])
    for name in REQUIRED:
        if not streams[name]:
            raise ValueError(f"{path}: no events of the required '{name}' stream")
    for rows in streams.values():
        rows.sort(key=lambda e: (e["t"], e["duration"]))
    return Trace(streams, hooks)
