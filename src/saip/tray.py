"""The tray icon and the offer popup. A second client of the local API, next to the dashboard.

    saip run             the agent, its dashboard and this tray together (the default)
    saip tray            only the tray, for an agent already running

It is a separate process and talks to the agent only through saip/server.py: it reads /api/events to draw the
icon and learn of questions, and posts answers and menu choices back. Nothing in the live loop knows it exists.

The icon is the SAIP mark (saip/icons.py), drawn per state. On macOS it is a template image, so the menu bar
recolours it for light and dark; elsewhere it is a small tile in the state's colour.

macOS only lets the main thread touch the menu bar. pystray's icon owns that thread there, so every change to the
icon, its tooltip or its menu is handed to the main thread (`_on_main`); doing it from another thread crashes the
process without a word, which is why the icon used to vanish a few seconds after it appeared.

The popup sits in the corner of the screen and never takes the keyboard: on Windows the window is marked
no-activate, so a Return key from someone still typing cannot answer for them; buttons work by mouse only.
Where a popup window cannot be drawn (macOS, where the tray icon and Tk both need the main thread, or a Python
without tkinter) the question is shown with the system dialog the app already uses.
"""
from __future__ import annotations

import json
import os
import queue
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from . import icons
from .server import DEFAULT_PORT

FLOW, STUCK, PAUSED, WAITING, OFFLINE = icons.FLOW, icons.STUCK, icons.PAUSED, icons.WAITING, icons.OFFLINE
WORDS = {FLOW: "in flow", STUCK: "possibly stuck", PAUSED: "paused", WAITING: "waiting for activity",
         OFFLINE: "agent not running"}
COLOURS = icons.COLOURS
INK, LIGHT, EDGE, TIMER = "#0E1116", "#F2F5F7", "#4A5563", "#12A39A"
PAUSE_MIN = 60
RETRY_S = 3.0
_OPEN = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # localhost never goes through a proxy


def state_of(tick: dict | None) -> str:
    """The same reading of a tick as the dashboard's headline."""
    if not tick:
        return WAITING
    if tick.get("paused"):
        return PAUSED
    return STUCK if tick.get("score", 0.0) >= tick.get("trigger", 1.0) else FLOW


def tooltip(state: str, tick: dict | None, quiet_until: float, now: float | None = None) -> str:
    text = f"SAIP: {WORDS[state]}"
    if tick and state in (FLOW, STUCK):
        text += f" (stuck score {tick['score']:.2f})"
    if quiet_until > (time.time() if now is None else now):
        text += time.strftime(", offers paused until %H:%M", time.localtime(quiet_until))
    return text


class Client:
    """Follows the agent's event stream and reports what changed as (name, payload) pairs on `out`.

    Names: "state" (payload is the tooltip text; read `.state`), "ask" (a question to show), "ask-closed" (its id),
    "app" (the agent's settings changed; read `.app`), "gone" (the stream ended after having worked).
    """

    def __init__(self, base: str, out: queue.Queue | None = None, retry_s: float = RETRY_S):
        self.base, self.out, self.retry_s = base.rstrip("/"), out or queue.Queue(), retry_s
        self.state, self.tick, self.quiet_until = OFFLINE, None, 0.0
        self.app: dict = {}
        self.connected_once = False
        self._stop = threading.Event()

    # ---- talking to the agent ----
    def _post(self, path: str, body: dict) -> bool:
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            with _OPEN.open(req, timeout=10) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def _get(self, path: str) -> dict | None:
        try:
            with _OPEN.open(self.base + path, timeout=5) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def answer(self, ask_id: int, button: str) -> bool:
        return self._post(f"/api/asks/{ask_id}", {"button": button})

    def pause(self, minutes: float) -> bool:
        """Hold offers back for this long; 0 lets them through again."""
        return self._post("/api/pause", {"minutes": minutes})

    def set_typing(self, on: bool) -> bool:
        return self._post("/api/settings", {"agent": {"monitor_typed_content": bool(on)}})

    def set_autostart(self, on: bool) -> bool:
        return self._post("/api/autostart", {"enabled": bool(on)})

    def shutdown(self) -> bool:
        return self._post("/api/shutdown", {})

    def refresh_app(self) -> None:
        got = self._get("/api/app")
        if got:
            self.app = got
            self.out.put(("app", None))

    @property
    def quiet(self) -> bool:
        return self.quiet_until > time.time()

    # ---- listening ----
    def _set(self, state: str) -> None:
        self.state = state
        self.out.put(("state", tooltip(state, self.tick, self.quiet_until)))

    def handle(self, kind: str, data: dict) -> None:
        if kind == "snapshot":
            self.tick = data["ticks"][-1] if data.get("ticks") else None
            self.quiet_until = data.get("quiet_until", 0.0)
            self._set(state_of(self.tick))
            for ask in data.get("asks", []):
                self.out.put(("ask", ask))
            threading.Thread(target=self.refresh_app, daemon=True).start()
        elif kind == "tick":
            prev, self.tick = self.state, data
            new = state_of(data)
            if new != prev or self.out.qsize() < 3:
                self._set(new)
        elif kind == "quiet":
            self.quiet_until = data.get("until", 0.0)
            self._set(self.state)
        elif kind == "app":
            self.app = {**self.app, **{k: v for k, v in data.items() if k not in ("id", "kind", "at")}}
            self.out.put(("app", None))
        elif kind == "ask":
            self.out.put(("ask", data))
        elif kind == "ask-closed":
            self.out.put(("ask-closed", data.get("ask")))

    def follow_once(self) -> None:
        """Read the stream until it ends. Raises OSError / URLError when the agent cannot be reached."""
        with _OPEN.open(self.base + "/api/events?client=tray", timeout=40) as stream:      # the server sends a keep-alive every 15 s
            self.connected_once = True
            kind = None
            for raw in stream:
                if self._stop.is_set():
                    return
                line = raw.decode("utf-8").rstrip("\n")
                if line.startswith("event: "):
                    kind = line[7:]
                elif line.startswith("data: ") and kind:
                    self.handle(kind, json.loads(line[6:]))
                    kind = None

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.follow_once()
            except (urllib.error.URLError, OSError, ValueError):
                pass
            if self._stop.is_set():
                return
            self.tick = None
            self._set(OFFLINE)
            if self.connected_once:
                self.out.put(("gone", None))
            self._stop.wait(self.retry_s)

    def start(self) -> None:
        threading.Thread(target=self.run, daemon=True, name="saip-tray-events").start()

    def stop(self) -> None:
        self._stop.set()


# ------------------------------------------------------------------------------------------------ the icon
def icon_image(state: str, size: int = 64, template: bool | None = None):
    """The SAIP mark for a state (see saip/icons.py)."""
    template = sys.platform == "darwin" if template is None else template
    return icons.render(state, size, template=template)


def _mac_template(icon) -> None:
    """Have pystray hand the image to macOS as a template, at the menu bar's real pixel density."""
    import AppKit
    import Foundation
    import io

    def assert_image():
        thickness = icon._status_bar.thickness()
        if icon._icon_image is not None:
            return
        b = io.BytesIO()
        icon._icon.save(b, "png")
        img = AppKit.NSImage.alloc().initWithData_(Foundation.NSData(b.getvalue()))
        side = thickness * 0.9
        img.setSize_((side, side))
        img.setTemplate_(True)
        icon._icon_image = img
        icon._status_item.button().setImage_(img)
    icon._assert_image = assert_image


def _on_main(fn) -> None:
    """Run fn on the thread that owns the menu bar (macOS); elsewhere pystray is thread-safe enough to call now."""
    if sys.platform == "darwin":
        from PyObjCTools import AppHelper
        AppHelper.callAfter(fn)
    else:
        fn()


# ------------------------------------------------------------------------------------------------ the popup
def _work_area(root) -> tuple[int, int]:
    """Bottom-right corner of the usable screen (above the Windows taskbar)."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes
            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):     # SPI_GETWORKAREA
                return rect.right, rect.bottom
        except Exception:
            pass
    return root.winfo_screenwidth(), root.winfo_screenheight() - 48


def _never_take_focus(win) -> None:
    """Windows: mark the popup no-activate so showing or clicking it never moves the keyboard away from your work."""
    if not sys.platform.startswith("win"):
        return                                                   # elsewhere a borderless window is left alone already
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        style = user32.GetWindowLongW(hwnd, -20)                 # GWL_EXSTYLE
        user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x00000080)    # WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    except Exception:
        pass


class Popups:
    """One small borderless window per open question, stacked up from the corner of the screen. Tk, main thread only."""
    WIDTH, MARGIN = 380, 16

    def __init__(self, root, on_answer):
        self.root, self.on_answer = root, on_answer
        self.open: dict[int, object] = {}

    def show(self, ask: dict) -> None:
        import tkinter as tk
        if ask["id"] in self.open:
            return
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=INK)
        font = ("Segoe UI", 11) if sys.platform.startswith("win") else ("Helvetica", 11)
        tk.Label(win, text="SAIP", bg=INK, fg=TIMER, font=font + ("bold",), anchor="w").pack(fill="x", padx=20, pady=(14, 0))
        tk.Label(win, text=ask["message"], bg=INK, fg=LIGHT, font=font, justify="left", anchor="w",
                 wraplength=self.WIDTH - 40).pack(fill="x", padx=20, pady=(6, 12))
        row = tk.Frame(win, bg=INK)
        row.pack(fill="x", padx=20, pady=(0, 16))
        last = len(ask["buttons"]) - 1
        for i, name in enumerate(ask["buttons"]):                # buttons run from the "no" side to the "yes" side
            yes = i == last
            tk.Button(row, text=name, font=font + ("bold",), relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
                      takefocus=0, bg=LIGHT if yes else INK, fg=INK if yes else LIGHT,
                      activebackground=LIGHT if yes else EDGE, activeforeground=INK if yes else LIGHT,
                      highlightthickness=1, highlightbackground=EDGE,
                      command=lambda n=name: self.on_answer(ask["id"], n)).pack(side="left", padx=(0, 10))
        bar = tk.Frame(win, bg=TIMER, height=3, width=self.WIDTH)       # time left to answer
        bar.pack(anchor="w")
        self.open[ask["id"]] = win
        win.update_idletasks()
        _never_take_focus(win)
        self._place()
        win.deiconify()
        ends = ask["at"] + ask["timeout_s"]

        def shrink():
            if ask["id"] not in self.open:
                return
            left = max(0.0, ends - time.time()) / max(ask["timeout_s"], 1e-6)
            bar.configure(width=max(1, int(self.WIDTH * left)))
            if left > 0:
                win.after(250, shrink)
        shrink()

    def close(self, ask_id) -> None:
        win = self.open.pop(ask_id, None)
        if win is not None:
            win.destroy()
            self._place()

    def close_all(self) -> None:
        for ask_id in list(self.open):
            self.close(ask_id)

    def _place(self) -> None:
        right, bottom = _work_area(self.root)
        y = bottom - self.MARGIN
        for win in self.open.values():
            win.update_idletasks()
            h = win.winfo_reqheight()
            y -= h
            win.geometry(f"{self.WIDTH}x{h}+{right - self.WIDTH - self.MARGIN}+{y}")
            y -= 10


def _dialog_asker(client: Client):
    """Where no popup window can be drawn: show each question with the system dialog, off the main thread."""
    from .platform import current
    plat = current()

    def show(ask: dict) -> None:
        def work():
            time.sleep(1.0)                      # a dashboard page, if open, shows the card; one answer is enough
            page = client._get("/api/app")
            if page and page.get("pages", 0) > 0:
                return
            left = int(max(1.0, ask["at"] + ask["timeout_s"] - time.time()))
            got = plat.ask(ask.get("title", "SAIP"), ask["message"], tuple(ask["buttons"]), timeout=left, default=False)
            if got:
                client.answer(ask["id"], got)
        threading.Thread(target=work, daemon=True, name="saip-tray-dialog").start()
    return show


# ------------------------------------------------------------------------------------------------ one at a time
class _Lock:
    """One tray per user: a second `saip tray` would put a second icon next to the first."""

    def __init__(self):
        from llm.config import user_config_dir
        self.path = user_config_dir() / "tray.lock"
        self.f = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "a+")
        try:
            if sys.platform.startswith("win"):
                import msvcrt
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.f.close()
            self.f = None
            return False


def build_menu(pystray, client: Client, port: int, events: queue.Queue):
    """The tray menu. Items read the latest state each time the menu is drawn."""
    page = lambda path="": (lambda icon, item: webbrowser.open(f"http://localhost:{port}/{path}"))

    def until_tomorrow() -> float:
        t = time.localtime()
        tomorrow = time.mktime((t.tm_year, t.tm_mon, t.tm_mday + 1, 9, 0, 0, 0, 0, -1))
        return max(1.0, (tomorrow - time.time()) / 60)

    def status_text(item) -> str:
        words = tooltip(client.state, client.tick, client.quiet_until).removeprefix("SAIP: ")
        app = (client.tick or {}).get("app")
        return f"SAIP: {words}" + (f" in {app}" if app and client.state == STUCK else "")

    typing_on = lambda item: bool(client.app.get("monitor_typed_content"))
    auto_on = lambda item: bool(client.app.get("autostart"))
    pause = lambda minutes: (lambda icon, item: client.pause(minutes() if callable(minutes) else minutes))
    return pystray.Menu(
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open dashboard", page(), default=True),
        pystray.MenuItem("Pause offers", pystray.Menu(
            pystray.MenuItem("For 30 minutes", pause(30)),
            pystray.MenuItem("For an hour", pause(60)),
            pystray.MenuItem("Until tomorrow morning", pause(until_tomorrow)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Let offers through again", pause(0), enabled=lambda item: client.quiet))),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Typed-text monitor", lambda icon, item: client.set_typing(not typing_on(item)),
                         checked=typing_on, enabled=lambda item: client.state != OFFLINE),
        pystray.MenuItem("Launch at login", lambda icon, item: client.set_autostart(not auto_on(item)),
                         checked=auto_on, enabled=lambda item: client.state != OFFLINE),
        pystray.MenuItem("Settings…", page("settings")),
        pystray.MenuItem("Health check…", page("settings#health")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit SAIP", lambda icon, item: events.put(("quit-all", None))))


# ------------------------------------------------------------------------------------------------ putting it together
def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="saip tray", description="Tray icon and offer popup for a running `saip run`.")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"the agent's --ui-port (default {DEFAULT_PORT})")
    ap.add_argument("--follow", action="store_true", help="quit when the agent stops (used by `saip run`)")
    a = ap.parse_args(argv)
    try:
        import pystray
    except Exception as e:                                       # not installed, or no desktop session to draw on
        print(f"The tray icon needs pystray and Pillow and a desktop session ({e}).\n"
              'Install them with: pip install -e .', file=sys.stderr)
        return 2
    lock = _Lock()
    if not lock.acquire():
        print("A SAIP tray icon is already running.", file=sys.stderr)
        return 0
    # 127.0.0.1, not "localhost": Windows tries IPv6 first and waits about two seconds before falling back, on every call
    base = f"http://127.0.0.1:{a.port}"
    events: queue.Queue = queue.Queue()
    client = Client(base, events)
    use_tk = sys.platform != "darwin"
    if use_tk:
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
        except Exception:
            use_tk = False

    icon = pystray.Icon("saip", icon_image(OFFLINE), tooltip(OFFLINE, None, 0.0),
                        build_menu(pystray, client, a.port, events))
    if sys.platform == "darwin":
        try:
            _mac_template(icon)
        except Exception:
            pass
    if use_tk:
        popups = Popups(root, client.answer)
        show_ask, close_ask, close_all = popups.show, popups.close, popups.close_all
    else:
        show_ask, close_ask, close_all = _dialog_asker(client), (lambda _id: None), (lambda: None)
    drawn = {"state": None}

    def redraw(tip: str) -> None:
        if drawn["state"] != client.state:
            icon.icon = icon_image(client.state)
            drawn["state"] = client.state
        icon.title = tip
        icon.update_menu()

    def handle(name, payload) -> bool:
        """Apply one change. False means it is time to quit."""
        if name == "state":
            _on_main(lambda: redraw(payload))
            if client.state == OFFLINE:                          # the agent is gone, so nothing can be answered
                close_all()
        elif name == "app":
            _on_main(icon.update_menu)
        elif name == "ask":
            show_ask(payload)
        elif name == "ask-closed":
            close_ask(payload)
        elif name == "quit-all":
            if not client.shutdown():                            # no agent to stop: just leave
                return False
        elif name == "quit" or (name == "gone" and a.follow):
            return False
        return True

    signal.signal(signal.SIGINT, lambda *_: events.put(("quit", None)))
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: events.put(("quit", None)))
    client.start()

    if use_tk:                                                   # Tk owns the main thread; the icon runs beside it
        icon.run_detached()

        def pump():
            try:
                while True:
                    if not handle(*events.get_nowait()):
                        root.quit()
                        return
            except queue.Empty:
                pass
            root.after(100, pump)
        root.after(100, pump)
        root.mainloop()
    else:                                                        # the icon owns the main thread (required on macOS)
        def pump_thread():
            while handle(*events.get()):
                pass
            _on_main(icon.stop)
        threading.Thread(target=pump_thread, daemon=True, name="saip-tray-pump").start()
        icon.run()
    client.stop()
    if use_tk:
        stopper = threading.Thread(target=icon.stop, daemon=True)    # takes the icon out of the tray
        stopper.start()
        stopper.join(2.0)
    sys.stdout.flush()
    os._exit(0)          # some tray backends leave a thread running after stop; there is nothing here left to save


if __name__ == "__main__":
    sys.exit(main())
