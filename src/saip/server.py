"""The local API the dashboard and the tray talk to, and the dashboard itself. Standard library only.

Reading
    GET  /api/app              what is running: version, platform, setup state, the agent's status, launch at login
    GET  /api/events           server-sent events: one "snapshot" first, then every event as it happens
    GET  /api/state            the same snapshot as plain JSON
    GET  /api/offers           the offer record (offers.jsonl) and its summary
    GET  /api/decisions        every gate decision (decisions.jsonl)
    GET  /api/export           ?what=offers|decisions&format=csv|jsonl, as a download
    GET  /api/inspector        the last model checks: the exact context sent, the answers, the offer (memory only)
    GET  /api/health           what `saip doctor` checks, as rows
    GET  /api/settings         private.toml as the page may see it (the API key only as set / not set)
    GET  /api/apps             installed apps (marked if ActivityWatch has seen them), with the block list
    GET  /api/setup/status | /api/setup/aw | /api/setup/permissions | /api/setup/hook | /api/repos | /api/autostart

Changing
    POST /api/asks/<id>        {"button": "Help me"} answers an open question
    POST /api/pause            {"minutes": 60} holds new offers back; {"minutes": 0} lets them through again
    POST /api/settings         {"llm": {...}, "agent": {...}, "gate": {...}} writes private.toml and reloads the agent
    POST /api/setup/aw         {"action": "install" | "start"}
    POST /api/setup/endpoint   {"base_url", "model", "api_key"} tests a model endpoint (nothing is saved)
    POST /api/setup/permissions {"id": "Input Monitoring"} shows the OS prompt or opens its settings pane
    POST /api/setup/keytest    {"seconds": 6} counts key presses for a few seconds (no characters kept)
    POST /api/setup/hook       {"action": "install" | "remove"}
    POST /api/repos/preview    {"folder"}; POST /api/repos {"action": "add" | "remove" | "decline", "folder"}
    POST /api/setup/finish     marks setup done and starts the agent
    POST /api/autostart        {"enabled": true}
    POST /api/shutdown         stops SAIP (the agent, this server and the tray)

Every other path serves the dashboard (src/saip/ui/dist, built from frontend/).

It listens on 127.0.0.1 only. Requests whose Host is not this machine are refused (so another site cannot reach it
through DNS rebinding); changes must be JSON (so a plain HTML form cannot send one) and come from this machine's
own pages (Origin), so a web page you visit cannot change your settings or answer an offer for you.

`python -m saip.server --demo` serves the dashboard with made-up activity and a pretend setup, for working on the
UI without ActivityWatch or a model. Nothing on the machine is read or changed.
"""
from __future__ import annotations

import csv
import io
import json
import mimetypes
import queue
import sys
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .events import Bus

DEFAULT_PORT = 5610
UI = Path(__file__).with_name("ui")
DIST = UI / "dist"
LOCAL = ("localhost", "127.0.0.1", "[::1]")
KEEPALIVE_S = 15.0
MAX_PAUSE_MIN = 24 * 60
MAX_BODY = 256 * 1024             # the largest request body read; real ones are a few hundred bytes
VERSION = "0.2.0"
TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
         ".woff2": "font/woff2", ".woff": "font/woff", ".png": "image/png", ".ico": "image/x-icon",
         ".json": "application/json", ".html": "text/html; charset=utf-8", ".map": "application/json"}
MISSING_UI = (b"<!doctype html><meta charset=utf-8><title>SAIP</title><body style='font:16px system-ui;padding:40px'>"
              b"<h1>SAIP</h1><p>The dashboard has not been built. From the repository: <code>cd frontend && npm install "
              b"&& npm run build</code>.</p><p>The API is running: <a href='/api/app'>/api/app</a>.</p>")


def _host_ok(host: str | None) -> bool:
    return bool(host) and host.rsplit(":", 1)[0].lower() in LOCAL


def _origin_ok(origin: str | None) -> bool:
    return origin is None or (urlparse(origin).hostname or "").lower() in ("localhost", "127.0.0.1", "::1")


class ApiError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class Download:
    def __init__(self, body: bytes, ctype: str, filename: str):
        self.body, self.ctype, self.filename = body, ctype, filename


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


def _rows_to_csv(rows: list[dict]) -> bytes:
    keys: list[str] = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in r.items()})
    return buf.getvalue().encode("utf-8")


def _need(body, *keys) -> list:
    if not isinstance(body, dict):
        raise ApiError(400, "expected a JSON object")
    try:
        return [body[k] for k in keys]
    except KeyError as e:
        raise ApiError(400, f"missing {e.args[0]!r}") from None


def _classify(detail: str) -> str:
    d = detail.lower()
    if "401" in d or "403" in d or "unauthor" in d or "api key" in d or "forbidden" in d:
        return "The endpoint refused the API key."
    if "404" in d or "not found" in d or "model" in d and "exist" in d:
        return "The endpoint answered, but not for that model name or path (does the URL end in /v1?)."
    if "logprob" in d or "log-prob" in d:
        return "The endpoint answered without log-probabilities; SAIP needs them to read the model's confidence."
    if "timed out" in d or "timeout" in d:
        return "The endpoint did not answer in time."
    if "cannot reach" in d or "connection" in d or "urlopen" in d or "name or service" in d or "nodename" in d:
        return "Could not reach that URL."
    return "The endpoint did not give a usable answer."


# ---------------------------------------------------------------------------------------------------- the API
class Api:
    """What each route does. The handler only checks the request and turns results into HTTP."""

    def __init__(self, bus: Bus, *, plat=None, aw=None, runtime=None, port: int | None = None,
                 on_ready=None, on_shutdown=None, offers=offers_payload, health=health_payload):
        self.bus, self.runtime, self.port = bus, runtime, port
        self._plat, self._aw = plat, aw
        self.on_ready, self.on_shutdown = on_ready, on_shutdown
        self.offers, self.health = offers, health

    @property
    def plat(self):
        if self._plat is None:
            from .platform import current
            self._plat = current()
        return self._plat

    @property
    def aw(self):
        if self._aw is None:
            from .aw import ActivityWatch
            self._aw = ActivityWatch()
        return self._aw

    def changed(self) -> None:
        self.bus.publish("app", **self.app())

    # ---- reading ----
    def app(self) -> dict:
        from . import autostart, setup
        from llm.config import load_settings
        settings = load_settings(getattr(getattr(self.runtime, "opts", None), "config", None))
        return {"app": "saip", "version": VERSION, "platform": self.plat.name, "demo": bool(self.bus.meta.get("demo")),
                "port": self.port, "pages": self.bus.pages, "ready": setup.is_ready(settings), "setup_done": bool(setup.load_manifest().get("done")),
                "model": settings.llm.model or None, "config": str(settings.path) if settings.path else None,
                "runtime": self.runtime.status() if self.runtime else None, "autostart": autostart.is_enabled(),
                "monitor_typed_content": settings.agent.monitor_typed_content, "quiet_until": self.bus.quiet_until}

    def decisions(self) -> dict:
        from . import offers
        rows = offers.read_decisions()
        return {"rows": rows[-5000:], "total": len(rows)}

    def export(self, what: str, fmt: str) -> Download:
        from . import offers
        if what not in ("offers", "decisions") or fmt not in ("csv", "jsonl"):
            raise ApiError(400, "expected ?what=offers|decisions&format=csv|jsonl")
        rows = offers.read_log() if what == "offers" else offers.read_decisions()
        stamp = time.strftime("%Y%m%d-%H%M")
        if fmt == "csv":
            return Download(_rows_to_csv(rows), "text/csv; charset=utf-8", f"saip-{what}-{stamp}.csv")
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
        return Download(body, "application/x-ndjson", f"saip-{what}-{stamp}.jsonl")

    def inspector(self) -> dict:
        entries = list(self.runtime.inspector) if self.runtime else []
        return {"entries": entries[::-1]}

    def settings(self) -> dict:
        from llm.config import load_settings
        from .config_file import public_settings
        return public_settings(load_settings(getattr(getattr(self.runtime, "opts", None), "config", None)))

    def apps(self) -> dict:
        from llm.config import load_settings
        from llm.privacy import GROUPS, SUGGESTED_GROUPS, group_of, is_blocked
        from .platform.apps import merge
        blocked = list(load_settings().llm.private_apps)
        rows = merge(self.plat.installed_apps(), self.seen_apps(), installed_only=True)
        listed = {r["value"] for r in rows}
        rows += [{"label": b, "value": b, "installed": False, "seen": False} for b in blocked if b not in listed
                 and not any(is_blocked(r["value"], [b]) for r in rows)]
        for r in rows:
            r["group"] = group_of(r["value"]) if group_of(r["value"]) != "other" else group_of(r["label"])
            r["blocked"] = is_blocked(r["value"], blocked) or is_blocked(r["label"], blocked)
            r["suggested"] = r["group"] in SUGGESTED_GROUPS
        return {"apps": sorted(rows, key=lambda r: r["label"].lower()), "blocked": blocked,
                "groups": [{"id": g, "label": label} for g, label, _ in GROUPS] + [{"id": "other", "label": "Other apps"}]}

    def seen_apps(self, days: float = 7.0) -> list[str]:
        """App names ActivityWatch recorded lately: the exact strings the block list is compared with."""
        from classifier.loader import STREAMS
        try:
            ids = [bid for bid, b in self.aw.buckets().items() if STREAMS.get(b.get("type")) == "window"]
            now = time.time()
            names: dict[str, float] = {}
            for bid in ids:
                for e in self.aw.events(bid, now - days * 86400, now, 20000):
                    a = (e.get("data") or {}).get("app")
                    if a:
                        names[a] = names.get(a, 0.0) + float(e.get("duration") or 0)
            return sorted(names, key=lambda a: -names[a])[:300]
        except Exception:
            return []

    def setup_status(self) -> dict:
        from . import setup
        from llm.config import find_config, load_settings
        s = load_settings()
        return {"ready": setup.is_ready(s), "done": bool(setup.load_manifest().get("done")),
                "config": str(find_config()) if find_config() else None,
                "model_set": bool(s.llm.base_url and s.llm.model), "api_key_set": bool(s.llm.api_key),
                "llm": {"base_url": s.llm.base_url, "model": s.llm.model},
                "features": {"typed": s.agent.monitor_typed_content, "screen": s.llm.read_screen,
                             "titles": s.llm.include_titles},
                "platform": self.plat.name}

    def aw_status(self) -> dict:
        from . import setup
        return setup.aw_status(self.plat, self.aw)

    def permissions(self, typed: bool = True, screen: bool = True) -> dict:
        from . import permissions
        return {"rows": permissions.status(self.plat.name, {"typed": typed, "screen": screen}),
                "host_app": permissions.host_app()}

    def hook_status(self) -> dict:
        from . import setup
        return setup.hook_status(self.plat)

    def repos(self) -> dict:
        from . import setup
        return setup.repo_overview()

    def autostart(self) -> dict:
        from . import autostart
        return {"enabled": autostart.is_enabled(), "where": autostart.where()}

    # ---- changing ----
    def save_settings(self, body: dict) -> dict:
        from . import setup
        from .config_file import SettingError, write_settings
        try:
            changed = write_settings(setup.ensure_config(), body)
        except SettingError as e:
            raise ApiError(400, str(e)) from None
        self.reload()
        return {"ok": True, "changed": changed, "settings": self.settings()}

    def reload(self) -> None:
        def work():
            if self.runtime is not None and self.runtime.running:
                self.runtime.reload()
            self.changed()
        threading.Thread(target=work, daemon=True, name="saip-reload").start()

    def aw_action(self, action: str) -> dict:
        from . import setup
        if action == "install":
            return setup.aw_install(self.plat, self.aw)
        if action == "start":
            return setup.aw_start(self.plat, self.aw)
        raise ApiError(400, "expected action install or start")

    def test_endpoint(self, base_url: str, model: str, api_key: str | None) -> dict:
        from llm.config import load_settings
        from . import setup
        s = load_settings()
        key = api_key if api_key else s.llm.api_key
        if not str(base_url).strip() or not str(model).strip():
            return {"ok": False, "detail": "Enter the endpoint URL and the model name.", "hint": ""}
        trial = replace(s, llm=replace(s.llm, base_url=str(base_url).strip(), model=str(model).strip(), api_key=key or ""))
        began = time.time()
        r = setup.check_endpoint(trial)
        took = round(time.time() - began, 2)
        if r.status == "ok":
            return {"ok": True, "detail": r.detail, "seconds": took}
        return {"ok": False, "detail": r.detail, "hint": _classify(r.detail), "seconds": took}

    def request_permission(self, perm_id: str) -> dict:
        from . import permissions
        return {"ok": permissions.request(self.plat.name, str(perm_id), self.plat)}

    def key_test(self, seconds: float) -> dict:
        from . import permissions
        return permissions.key_test(float(seconds))

    def hook_action(self, action: str) -> dict:
        from . import setup
        if action == "install":
            return setup.hook_install(self.plat)
        if action == "remove":
            return setup.hook_remove(self.plat)
        raise ApiError(400, "expected action install or remove")

    def repo_preview(self, folder: str) -> dict:
        from . import setup
        try:
            return setup.repo_preview(str(folder))
        except ValueError as e:
            raise ApiError(400, str(e)) from None

    def repo_action(self, action: str, folder: str) -> dict:
        from . import setup
        try:
            if action == "add":
                out = setup.repo_add(str(folder))
            elif action == "remove":
                out = setup.repo_remove(str(folder))
            elif action == "decline":
                out = setup.repo_decline(str(folder))
            else:
                raise ApiError(400, "expected action add or remove")
        except ValueError as e:
            raise ApiError(400, str(e)) from None
        self.reload()
        return out

    def finish(self) -> dict:
        from . import setup
        out = setup.finish(self.plat)
        if out.get("ok"):
            if self.on_ready:
                threading.Thread(target=self.on_ready, daemon=True, name="saip-ready").start()
            self.changed()
        return out

    def set_autostart(self, enabled: bool) -> dict:
        from . import autostart
        from llm.config import find_config
        if enabled:
            autostart.enable(find_config(getattr(getattr(self.runtime, "opts", None), "config", None)))
        else:
            autostart.disable()
        self.changed()
        return self.autostart()

    def shutdown(self) -> dict:
        if self.on_shutdown is None:
            raise ApiError(409, "this server cannot be stopped from the page")
        threading.Timer(0.2, self.on_shutdown).start()
        return {"ok": True}


# ---------------------------------------------------------------------------------------------------- HTTP
def make_handler(bus: Bus, offers=offers_payload, health=health_payload, api: Api | None = None):
    api = api or Api(bus, offers=offers, health=health)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:          # the terminal belongs to the monitor's own lines
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json", extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj).encode())

        def _allowed(self) -> bool:
            if _host_ok(self.headers.get("Host")):
                return True
            self._json({"error": "this server only answers on localhost"}, 403)
            return False

        def _reply(self, fn) -> None:
            try:
                out = fn()
            except ApiError as e:
                self._json({"ok": False, "error": e.message}, e.code)
                return
            except Exception as e:                    # one broken route must not take the server down
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
                return
            if isinstance(out, Download):
                self._send(200, out.body, out.ctype, {"Content-Disposition": f'attachment; filename="{out.filename}"'})
            else:
                self._json(out)

        # ---- GET ----
        def do_GET(self) -> None:
            if not self._allowed():
                return
            url = urlparse(self.path)
            path, q = url.path, {k: v[-1] for k, v in parse_qs(url.query).items()}
            routes = {
                "/api/state": bus.snapshot, "/api/offers": api.offers, "/api/health": api.health,
                "/api/app": api.app, "/api/decisions": api.decisions, "/api/inspector": api.inspector,
                "/api/settings": api.settings, "/api/apps": api.apps, "/api/setup/status": api.setup_status,
                "/api/setup/aw": api.aw_status, "/api/setup/hook": api.hook_status, "/api/repos": api.repos,
                "/api/autostart": api.autostart,
                "/api/setup/permissions": lambda: api.permissions(q.get("typed", "1") == "1", q.get("screen", "1") == "1"),
                "/api/export": lambda: api.export(q.get("what", "decisions"), q.get("format", "csv")),
            }
            if path == "/api/events":
                self._stream(q.get("client") == "tray")
            elif path in routes:
                self._reply(routes[path])
            elif path.startswith("/api/"):
                self._json({"error": "not found"}, 404)
            else:
                self._static(path)

        def _static(self, path: str) -> None:
            index = DIST / "index.html"
            if not index.exists():
                self._send(200, MISSING_UI, "text/html; charset=utf-8")
                return
            rel = path.lstrip("/")
            target = (DIST / rel).resolve() if rel else index
            try:
                target.relative_to(DIST.resolve())
            except ValueError:
                target = index
            if not target.is_file():
                target = index                             # a page route: the app draws it
            ctype = TYPES.get(target.suffix) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), ctype)

        # ---- POST ----
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
            if raw and "application/json" not in (self.headers.get("Content-Type") or ""):
                self._json({"error": "send JSON (Content-Type: application/json)"}, 415)
                return
            path = urlparse(self.path).path
            parts = path.strip("/").split("/")
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
            if parts[:2] == ["api", "asks"]:
                try:
                    ask_id, button = int(parts[2]), str(body["button"])
                    assert len(parts) == 3
                except (ValueError, KeyError, IndexError, AssertionError, TypeError):
                    self._json({"error": "expected POST /api/asks/<id> with {\"button\": ...}"}, 400)
                    return
                ok = bus.answer(ask_id, button)
                self._json({"ok": ok} if ok else {"ok": False, "error": "that question is no longer open"}, 200 if ok else 409)
                return
            b = body if isinstance(body, dict) else {}
            routes = {
                "/api/settings": lambda: api.save_settings(b),
                "/api/setup/aw": lambda: api.aw_action(*_need(body, "action")),
                "/api/setup/endpoint": lambda: api.test_endpoint(*_need(body, "base_url", "model"), b.get("api_key")),
                "/api/setup/permissions": lambda: api.request_permission(*_need(body, "id")),
                "/api/setup/keytest": lambda: api.key_test(b.get("seconds", 6)),
                "/api/setup/hook": lambda: api.hook_action(*_need(body, "action")),
                "/api/repos/preview": lambda: api.repo_preview(*_need(body, "folder")),
                "/api/repos": lambda: api.repo_action(*_need(body, "action", "folder")),
                "/api/setup/finish": api.finish,
                "/api/autostart": lambda: api.set_autostart(bool(_need(body, "enabled")[0])),
                "/api/shutdown": api.shutdown,
            }
            if path in routes:
                if body is None:
                    self._json({"error": "the body is not valid JSON"}, 400)
                    return
                self._reply(routes[path])
            else:
                self._json({"error": "not found"}, 404)

        # ---- the stream ----
        def _stream(self, tray: bool = False) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            q = bus.subscribe(tray)
            try:
                self._event("snapshot", bus.snapshot(), retry_ms=1000)
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

        def _event(self, kind: str, data: dict, retry_ms: int | None = None) -> None:
            retry = f"retry: {retry_ms}\n" if retry_ms else ""
            self.wfile.write(f"event: {kind}\n{retry}data: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = not sys.platform.startswith("win")     # on Windows it would let two servers share a port


def start(bus: Bus, port: int = DEFAULT_PORT, api: Api | None = None, **payloads) -> ThreadingHTTPServer:
    """Serve on 127.0.0.1 in a background thread. Port 0 picks a free one (see `.server_address`)."""
    httpd = _Server(("127.0.0.1", port), make_handler(bus, api=api, **payloads))
    if api is not None:
        api.port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True, name="saip-ui").start()
    return httpd


def url(httpd: ThreadingHTTPServer) -> str:
    return f"http://localhost:{httpd.server_address[1]}/"


def running_here(port: int, timeout: float = 1.0) -> dict | None:
    """The /api/app of a SAIP already serving on this port, or None."""
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/app", timeout=timeout) as r:
            info = json.loads(r.read())
            return info if info.get("app") == "saip" else None
    except Exception:
        return None


# ---------------------------------------------------------------------------------------------------- demo
def main(argv=None) -> int:
    import argparse
    from saip.demo import DemoApi, demo_loop
    ap = argparse.ArgumentParser(prog="python -m saip.server", description="Serve the dashboard with made-up activity.")
    ap.add_argument("--demo", action="store_true", required=True, help="feed made-up events (the real ones come from `saip run`)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--speed", type=float, default=1.0, help="ticks per second")
    ap.add_argument("--fresh", action="store_true", help="start as a first run, with the setup wizard")
    a = ap.parse_args(argv)
    bus = Bus()
    bus.meta["demo"] = True
    api = DemoApi(bus, fresh=a.fresh)
    httpd = start(bus, a.port, api=api)
    print(f"Demo dashboard at {url(httpd)}  (Ctrl-C to stop)")
    try:
        demo_loop(bus, api, a.speed)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
