"""The local API a UI talks to, and the dashboard page itself. Standard library only.

    GET  /                 the dashboard
    GET  /api/events       server-sent events: one "snapshot" first, then every event as it happens
    GET  /api/state        the same snapshot as plain JSON
    GET  /api/offers       the offer record (offers.jsonl) and its summary
    GET  /api/health       what `saip doctor` checks, as rows
    POST /api/asks/<id>    {"button": "Help me"} answers an open question
    POST /api/pause        {"minutes": 60} holds new offers back; {"minutes": 0} lets them through again

It listens on 127.0.0.1 only. Requests whose Host is not this machine are refused (so another site cannot reach it
through DNS rebinding), and so are POSTs sent from another origin (so a web page cannot answer an offer for you).

`python -m saip.server --demo` serves the dashboard with made-up activity, for working on a UI without
ActivityWatcher or a model.
"""
from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .events import Bus

DEFAULT_PORT = 5610
PAGE = Path(__file__).with_name("ui") / "dashboard.html"
LOCAL = ("localhost", "127.0.0.1", "[::1]")
KEEPALIVE_S = 15.0
MAX_PAUSE_MIN = 24 * 60
MAX_BODY = 64 * 1024              # the largest request body read; real ones are a few dozen bytes


def _host_ok(host: str | None) -> bool:
    return bool(host) and host.rsplit(":", 1)[0].lower() in LOCAL


def _origin_ok(origin: str | None) -> bool:
    return origin is None or (urlparse(origin).hostname or "").lower() in ("localhost", "127.0.0.1", "::1")


def offers_payload() -> dict:
    from . import offers
    rows = offers.read_log()
    return {"summary": offers.summarise(rows), "offers": rows[-200:]}


def health_payload() -> dict:
    from . import setup
    from .aw import ActivityWatch
    from .platform import current
    results = setup.doctor(current(), ActivityWatch(), out=lambda _line: None)
    return {"checks": [{"name": r.name, "status": r.status, "detail": r.detail} for r in results]}


def make_handler(bus: Bus, offers=offers_payload, health=health_payload):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:          # the terminal belongs to the monitor's own lines
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj).encode())

        def _allowed(self) -> bool:
            if _host_ok(self.headers.get("Host")):
                return True
            self._json({"error": "this server only answers on localhost"}, 403)
            return False

        def do_GET(self) -> None:
            if not self._allowed():
                return
            path = urlparse(self.path).path
            if path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/state":
                self._json(bus.snapshot())
            elif path == "/api/offers":
                self._json(offers())
            elif path == "/api/health":
                self._json(health())
            elif path == "/api/events":
                self._stream()
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:
            # Read the body before answering anything, even a refusal. Replying while it is still unread makes
            # Windows reset the connection, and the sender then sees an aborted connection instead of the reply.
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY:
                self.close_connection = True
                self._json({"error": "request body missing a valid length, or too large"}, 400)
                return
            raw = self.rfile.read(length)
            if not self._allowed():
                return
            if not _origin_ok(self.headers.get("Origin")):
                self._json({"error": "requests from other sites are refused"}, 403)
                return
            parts = urlparse(self.path).path.strip("/").split("/")
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = None
            if parts == ["api", "pause"]:
                try:
                    minutes = float(body["minutes"])
                    assert 0 <= minutes <= MAX_PAUSE_MIN
                except (KeyError, TypeError, ValueError, AssertionError):
                    self._json({"error": f"expected {{\"minutes\": 0 to {MAX_PAUSE_MIN}}}"}, 400)
                    return
                self._json({"ok": True, "quiet_until": bus.quiet(minutes)})
                return
            try:
                ask_id, button = int(parts[2]), str(body["button"])
                assert parts[:2] == ["api", "asks"] and len(parts) == 3
            except (ValueError, KeyError, IndexError, AssertionError, TypeError):
                self._json({"error": "expected POST /api/asks/<id> with {\"button\": ...}"}, 400)
                return
            ok = bus.answer(ask_id, button)
            self._json({"ok": ok} if ok else {"ok": False, "error": "that question is no longer open"}, 200 if ok else 409)

        def _stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            q = bus.subscribe()
            try:
                self._event("snapshot", bus.snapshot())
                while True:
                    try:
                        ev = q.get(timeout=KEEPALIVE_S)
                    except queue.Empty:
                        self.wfile.write(b": still here\n\n")
                        self.wfile.flush()
                        continue
                    if ev is None:
                        return
                    self._event(ev["kind"], ev)
            except (BrokenPipeError, ConnectionError, OSError):
                pass
            finally:
                bus.unsubscribe(q)
                self.close_connection = True

        def _event(self, kind: str, data: dict) -> None:
            self.wfile.write(f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start(bus: Bus, port: int = DEFAULT_PORT, **payloads) -> ThreadingHTTPServer:
    """Serve on 127.0.0.1 in a background thread. Port 0 picks a free one (see `.server_address`)."""
    httpd = _Server(("127.0.0.1", port), make_handler(bus, **payloads))
    threading.Thread(target=httpd.serve_forever, daemon=True, name="saip-ui").start()
    return httpd


def url(httpd: ThreadingHTTPServer) -> str:
    return f"http://localhost:{httpd.server_address[1]}/"


# ---------------------------------------------------------------------------------------------------- demo
def _demo(bus: Bus, speed: float = 1.0) -> None:
    """Made-up activity shaped like the real events: a calm stretch, a climb, a deferred offer, then an offer."""
    import math
    import random
    import time
    rng, trigger, score, i, mult = random.Random(7), 0.19, 0.04, 0, 1.0
    history: list[dict] = []
    while True:
        i += 1
        phase = (i % 150) / 150
        target = 0.05 if phase < 0.35 else 0.62 if phase < 0.8 else 0.08
        score += 0.1 * (target - score) + rng.uniform(-0.01, 0.01)
        score = min(1.0, max(0.0, score))
        paused = 0.86 < phase < 0.93
        bus.publish("tick", time=time.time(), score=round(score, 4), paused=paused,
                    reason="afk" if paused else None, trigger=trigger)
        if not paused and score >= trigger and i % 12 == 0 and time.time() >= bus.quiet_until:
            p_need = min(0.97, 0.35 + score * 0.8 + rng.uniform(-0.05, 0.05))
            p_accept = min(0.97, 0.2 + score * 0.9 + rng.uniform(-0.08, 0.08))
            c_fa, c_fn = math.exp(math.log(3) * (1 - score)), 2 * math.exp(math.log(2) * score)
            bar = c_fa / (c_fa + p_need * c_fn)
            kind = "offer" if p_accept >= bar else "deferred" if p_accept >= bar - 0.1 and score < 0.5 else "quiet"
            bus.publish("decision", decision=kind, score=round(score, 4), p_need=round(p_need, 4),
                        p_accept=round(p_accept, 4), bar=round(bar, 4), resampled=abs(p_accept - bar) <= 0.1)
            if kind == "offer":
                message = "You've gone back and forth between the same two windows several times lately. Want to talk through the next step?"
                bus.publish("offer", message=message, task="Talk through the next step", generated=True)
                began = time.time()
                answer = bus.ask("SAIP", message, ("Not now", "Help me"), 45)
                outcome = {"Help me": "accepted", "Not now": "dismissed"}.get(answer, "ignored")
                mult = 1.0 if outcome == "accepted" else min(mult * (2.0 if outcome == "dismissed" else 1.5), 4.0)
                history.append({"time": began, "stuck_score": score, "p_need": p_need, "p_accept": p_accept, "bar": bar,
                                "task": "Talk through the next step", "message": message, "outcome": outcome,
                                "answered_after_s": time.time() - began if answer else None})
                _demo.history = history
                bus.publish("outcome", outcome=outcome, cooldown_mult=mult)
                if outcome == "accepted":
                    text = ("Name the one thing you expected to see in the second window and did not. If you can say "
                            "it in a sentence, start there; if not, tell me what each window is for.")
                    bus.publish("help", text=text)
                    bus.ask("SAIP", text, ("Close", "Copy"), 180)
                score = 0.1
        time.sleep(1.0 / speed)


def _demo_offers() -> dict:
    from . import offers
    rows = getattr(_demo, "history", [])
    return {"summary": offers.summarise(rows), "offers": rows}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m saip.server", description="Serve the dashboard with made-up activity.")
    ap.add_argument("--demo", action="store_true", required=True, help="feed made-up events (the real ones come from `saip run --ui`)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--speed", type=float, default=1.0, help="ticks per second")
    a = ap.parse_args(argv)
    bus = Bus()
    bus.meta["demo"] = True
    demo_health = lambda: {"checks": [{"name": "demo", "status": "ok", "detail": "made-up activity, nothing on this machine is read"}]}
    httpd = start(bus, a.port, offers=_demo_offers, health=demo_health)
    print(f"Demo dashboard at {url(httpd)}  (Ctrl-C to stop)")
    try:
        _demo(bus, a.speed)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
