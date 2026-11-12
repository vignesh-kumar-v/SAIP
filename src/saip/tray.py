"""The tray icon and the offer popup. A second client of the local API, next to the dashboard.

    saip run --tray      the agent, its API and this tray together
    saip tray            only the tray, for an agent already running with --ui

It is a separate process and talks to the agent only through saip/server.py: it reads /api/events to colour the
icon and learn of questions, and posts the answer back. Nothing in the live loop knows it exists.

The popup sits in the corner of the screen and never takes the keyboard: on Windows the window is marked
no-activate, so a Return key from someone still typing cannot answer for them; buttons work by mouse only.
Where a popup window cannot be drawn (macOS, where the tray icon and Tk both need the main thread, or a Python
without tkinter) the question is shown with the system dialog the app already uses.

Needs `pip install "saip[tray]"` (pystray and Pillow). They are imported only inside `main`, so the rest of the
package, and the tests, work without them.
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

from .server import DEFAULT_PORT

FLOW, STUCK, PAUSED, WAITING, OFFLINE = "flow", "stuck", "paused", "waiting", "offline"
WORDS = {FLOW: "in flow", STUCK: "possibly stuck", PAUSED: "paused", WAITING: "waiting for activity",
         OFFLINE: "agent not running"}
COLOURS = {FLOW: "#1F7A80", STUCK: "#B4620C", PAUSED: "#8A98A6", WAITING: "#8A98A6", OFFLINE: "#8A98A6"}
INK, LIGHT, EDGE, TIMER = "#13233A", "#F2F6F8", "#6F8196", "#7FD0D6"
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
    "gone" (the stream ended after having worked: the agent has stopped).
    """

    def __init__(self, base: str, out: queue.Queue | None = None, retry_s: float = RETRY_S):
        self.base, self.out, self.retry_s = base.rstrip("/"), out or queue.Queue(), retry_s
        self.state, self.tick, self.quiet_until = OFFLINE, None, 0.0
        self.connected_once = False
        self._stop = threading.Event()

    # ---- talking to the agent ----
    def _post(self, path: str, body: dict) -> bool:
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            with _OPEN.open(req, timeout=5) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def answer(self, ask_id: int, button: str) -> bool:
        return self._post(f"/api/asks/{ask_id}", {"button": button})

    def pause(self, minutes: float) -> bool:
        """Hold offers back for this long; 0 lets them through again."""
        return self._post("/api/pause", {"minutes": minutes})

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
        elif kind == "tick":
            self.tick = data
            self._set(state_of(data))
        elif kind == "quiet":
            self.quiet_until = data.get("until", 0.0)
            self._set(self.state)
        elif kind == "ask":
            self.out.put(("ask", data))
        elif kind == "ask-closed":
            self.out.put(("ask-closed", data.get("ask")))

    def follow_once(self) -> None:
        """Read the stream until it ends. Raises OSError / URLError when the agent cannot be reached."""
        with _OPEN.open(self.base + "/api/events", timeout=40) as stream:      # the server sends a keep-alive every 15 s
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
def icon_image(state: str, size: int = 64):
    """A disc in the state's colour, with a mark so the states differ by shape as well as colour."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d, pad, c = ImageDraw.Draw(img), size // 10, size // 2
    box = (pad, pad, size - pad, size - pad)
    if state == OFFLINE:
        d.ellipse(box, outline=COLOURS[state], width=size // 8)
        return img
    d.ellipse(box, fill=COLOURS[state])
    if state == STUCK:                                           # a white dot
        r = size // 7
        d.ellipse((c - r, c - r, c + r, c + r), fill="white")
    elif state in (PAUSED, WAITING):                             # two bars
        w, h, gap = size // 9, size // 4, size // 12
        d.rectangle((c - gap - w, c - h, c - gap, c + h), fill="white")
        d.rectangle((c + gap, c - h, c + gap + w, c + h), fill="white")
    return img


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
        tk.Label(win, text=ask["message"], bg=INK, fg=LIGHT, font=font, justify="left", anchor="w",
                 wraplength=self.WIDTH - 40).pack(fill="x", padx=20, pady=(18, 12))
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
        bar = tk.Frame(win, bg=TIMER, height=4, width=self.WIDTH)       # time left to answer
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
            left = int(max(1.0, ask["at"] + ask["timeout_s"] - time.time()))
            got = plat.ask(ask.get("title", "SAIP"), ask["message"], tuple(ask["buttons"]), timeout=left, default=False)
            if got:
                client.answer(ask["id"], got)
        threading.Thread(target=work, daemon=True, name="saip-tray-dialog").start()
    return show


# ------------------------------------------------------------------------------------------------ putting it together
def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="saip tray", description="Tray icon and offer popup for a running `saip run --ui`.")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"the agent's --ui-port (default {DEFAULT_PORT})")
    ap.add_argument("--follow", action="store_true", help="quit when the agent stops (used by `saip run --tray`)")
    a = ap.parse_args(argv)
    try:
        import pystray
    except Exception as e:                                       # not installed, or no desktop session to draw on
        print(f"The tray icon needs pystray and Pillow and a desktop session ({e}).\n"
              'Install them with: pip install "saip[tray]"', file=sys.stderr)
        return 2
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

    def toggle_pause(icon, item):
        client.pause(0 if client.quiet else PAUSE_MIN)

    menu = pystray.Menu(
        pystray.MenuItem("Open dashboard", lambda icon, item: webbrowser.open(f"http://localhost:{a.port}/"), default=True),
        pystray.MenuItem(lambda item: "Let offers through again" if client.quiet else "Pause offers for an hour", toggle_pause),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit the tray", lambda icon, item: events.put(("quit", None))))
    icon = pystray.Icon("saip", icon_image(OFFLINE), tooltip(OFFLINE, None, 0.0), menu)
    if use_tk:
        popups = Popups(root, client.answer)
        show_ask, close_ask, close_all = popups.show, popups.close, popups.close_all
    else:
        show_ask, close_ask, close_all = _dialog_asker(client), (lambda _id: None), (lambda: None)

    def handle(name, payload) -> bool:
        """Apply one change. False means it is time to quit."""
        if name == "state":
            icon.icon, icon.title = icon_image(client.state), payload
            icon.update_menu()
            if client.state == OFFLINE:                          # the agent is gone, so nothing can be answered
                close_all()
        elif name == "ask":
            show_ask(payload)
        elif name == "ask-closed":
            close_ask(payload)
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
            icon.stop()
        threading.Thread(target=pump_thread, daemon=True, name="saip-tray-pump").start()
        icon.run()
    client.stop()
    stopper = threading.Thread(target=icon.stop, daemon=True)    # takes the icon out of the tray
    stopper.start()
    stopper.join(2.0)
    sys.stdout.flush()
    os._exit(0)          # some tray backends leave a thread running after stop; there is nothing here left to save


if __name__ == "__main__":
    sys.exit(main())
