"""A small client for the local ActivityWatcher server (REST on localhost:5600). Standard library only."""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DEFAULT_URL = os.environ.get("SAIP_AW_URL", "http://localhost:5600")


class AWError(RuntimeError):
    pass


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def hostname() -> str:
    return socket.gethostname()


class ActivityWatch:
    def __init__(self, base: str = DEFAULT_URL, timeout: float = 3.0):
        self.base, self.timeout = base.rstrip("/"), timeout

    def _call(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + "/api/0" + path, data, {"Content-Type": "application/json"},
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code == 304:                      # bucket already exists
                return None
            raise AWError(f"ActivityWatcher returned HTTP {e.code} for {method} {path}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise AWError(f"cannot reach ActivityWatcher at {self.base}: {e}") from e

    def ping(self) -> bool:
        try:
            self._call("GET", "/info")
            return True
        except AWError:
            return False

    def buckets(self) -> dict:
        return self._call("GET", "/buckets/") or {}

    def bucket_ids_by_type(self, btype: str) -> list[str]:
        return [bid for bid, b in self.buckets().items() if b.get("type") == btype]

    def ensure_bucket(self, bucket_id: str, btype: str, client: str, host: str | None = None) -> None:
        self._call("POST", f"/buckets/{urllib.parse.quote(bucket_id)}",
                   {"client": client, "type": btype, "hostname": host or hostname()})

    def delete_bucket(self, bucket_id: str) -> None:
        self._call("DELETE", f"/buckets/{urllib.parse.quote(bucket_id)}?force=1")

    def insert(self, bucket_id: str, events: list[dict]) -> None:
        self._call("POST", f"/buckets/{urllib.parse.quote(bucket_id)}/events", events)

    def events(self, bucket_id: str, start: float, end: float, limit: int = -1) -> list[dict]:
        q = urllib.parse.urlencode({"start": iso(start), "end": iso(end), "limit": limit})
        return self._call("GET", f"/buckets/{urllib.parse.quote(bucket_id)}/events?{q}") or []


# Bucket types the classifier's loader matches by type (never by id), with the ids and clients we write under.
TERMINAL = ("aw-watcher-terminal", "app.terminal.command")
FS = ("aw-watcher-fs", "app.fs.activity")
INPUT = ("aw-watcher-input", "os.hid.input")


def bucket_id(kind: tuple, host: str | None = None) -> str:
    return f"{kind[0]}_{host or hostname()}"
