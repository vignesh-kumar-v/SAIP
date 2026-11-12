"""The OS permissions SAIP's own features need, checked and requested where the OS allows it.

ActivityWatch's watchers need their own grants; those are checked through the data they produce (setup.check_data).
This module is about the process running `saip`:

    macOS    Input Monitoring for the typed-text monitor, Accessibility for reading screen text. Both are granted
             to the app SAIP was started from (Terminal, iTerm, VS Code...), and take effect after SAIP restarts.
    Windows  nothing to grant.
    Linux    nothing to grant, but key and window reading need an X11 session; Wayland hides both.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import threading
import time

TERMINAL_NAMES = {"Apple_Terminal": "Terminal", "iTerm.app": "iTerm", "vscode": "Visual Studio Code",
                  "WarpTerminal": "Warp", "ghostty": "Ghostty", "WezTerm": "WezTerm", "Hyper": "Hyper",
                  "Tabby": "Tabby", "Alacritty": "Alacritty", "kitty": "kitty"}


def host_app() -> str:
    """The app macOS will ask about: the one SAIP was started from."""
    prog = os.environ.get("TERM_PROGRAM") or ("kitty" if os.environ.get("KITTY_WINDOW_ID") else "")
    if prog:
        return TERMINAL_NAMES.get(prog, prog)
    if os.environ.get("__CFBundleIdentifier"):
        return os.environ["__CFBundleIdentifier"].split(".")[-1]
    return "the app that started SAIP"


def _framework(name: str):
    try:
        return ctypes.cdll.LoadLibrary(f"/System/Library/Frameworks/{name}.framework/{name}")
    except OSError:
        return None


def _mac_bool(framework: str, fn: str) -> bool | None:
    lib = _framework(framework)
    if lib is None or not hasattr(lib, fn):
        return None
    f = getattr(lib, fn)
    f.restype = ctypes.c_bool
    try:
        return bool(f())
    except Exception:
        return None


def status(platform_name: str, features: dict) -> list[dict]:
    """Rows for the setup wizard: {"id", "label", "needed_for", "granted": True/False/None, "how", "can_request"}."""
    rows = []
    if platform_name == "macOS":
        app = host_app()
        if features.get("typed", True):
            rows.append({"id": "Input Monitoring", "label": "Input Monitoring", "needed_for": "typed-text monitor",
                         "granted": _mac_bool("CoreGraphics", "CGPreflightListenEventAccess"),
                         "how": f"Turn on {app} in System Settings > Privacy & Security > Input Monitoring, "
                                "then restart SAIP.", "can_request": True})
        if features.get("screen", True):
            rows.append({"id": "Accessibility", "label": "Accessibility", "needed_for": "screen text",
                         "granted": _mac_bool("ApplicationServices", "AXIsProcessTrusted"),
                         "how": f"Turn on {app} in System Settings > Privacy & Security > Accessibility, "
                                "then restart SAIP.", "can_request": True})
    elif platform_name == "Linux":
        wayland = os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        rows.append({"id": "X11", "label": "X11 session", "needed_for": "typed-text monitor and window tracking",
                     "granted": not wayland,
                     "how": "Wayland hides other apps' keys and windows. Log in with an X11 (Xorg) session.",
                     "can_request": False})
    return rows


def request(platform_name: str, perm_id: str, plat=None) -> bool:
    """Ask the OS to show its own prompt (macOS), falling back to opening the settings pane."""
    if platform_name != "macOS":
        return False
    asked = False
    if perm_id == "Input Monitoring":
        asked = _mac_bool("CoreGraphics", "CGRequestListenEventAccess") is not None
    elif perm_id == "Accessibility":
        try:
            from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
            asked = True
        except Exception:
            asked = False
    if plat is not None:
        plat.open_permission_pane(perm_id)
    return asked or plat is not None


def key_test(seconds: float = 6.0) -> dict:
    """Listen for key presses for a few seconds and count them (no characters are kept)."""
    try:
        from pynput import keyboard
    except Exception:
        return {"available": False, "presses": 0, "detail": "pynput is not installed: pip install -e ."}
    count = {"n": 0}
    done = threading.Event()

    def on_press(_key):
        count["n"] += 1
        if count["n"] >= 3:
            done.set()

    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    done.wait(max(1.0, min(seconds, 20.0)))
    listener.stop()
    time.sleep(0.05)
    ok = count["n"] > 0
    detail = "" if ok else ("No keys arrived. " + ("Grant Input Monitoring, then restart SAIP." if sys.platform == "darwin"
                                                  else "Check that this is an X11 session." if sys.platform.startswith("linux")
                                                  else ""))
    return {"available": True, "presses": count["n"], "detail": detail.strip()}
