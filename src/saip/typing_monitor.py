"""Opt-in typed-text monitor for SAIP (Linux + macOS).

Listens for key presses system-wide (via the optional `pynput` package) and keeps the most recent typed
characters in a small in-memory buffer, so the agent can know roughly what the user is writing.

Safeguards, because this is a keystroke reader:

* Off unless `monitor_typed_content = true` in private.toml, and `pynput` must be installed
  (`pip install -e ".[typing]"`).
* Only records while the front app is on the whitelist, and never while it is on the private list
  (password managers, chat apps). Leaving such an app empties the buffer at once.
* Shortcuts (Ctrl/Cmd/Alt held) are not typing and are ignored. Esc empties the buffer, F1 turns the
  monitor off.
* Memory only: nothing is written to disk and, unless `echo=True` is passed, nothing is printed.

Limit: the whitelist works per app, not per field, so a password box inside an allowed browser can still be
read. Keep browsers off the whitelist if that matters to you.
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
import threading
import time
from collections import deque
from typing import Callable, Deque, Optional

try:                                    # optional dependency: the rest of SAIP must work without it
    from pynput import keyboard
except ImportError:                     # pragma: no cover - depends on the environment
    keyboard = None

# ------------------------------------------------------------------ config
DEFAULT_CONFIG = {
    "typed_text_clip_chars": 200,          # how much of the newest text is handed out
    "typed_text_sentence_clip": 100,       # echo mode only: clip at the nearest sentence end
    "foreground_app_whitelist": [
        "code", "code-oss", "vscode",                    # VS Code
        "word", "ms-word", "word.exe",                   # Word
        "chrome", "google-chrome", "msedge",             # Chrome/Edge textareas
        "textedit", "notepad", "gedit",                  # Simple editors
        "emacs", "emacsclient",                          # Emacs
        "nano", "vim",                                   # Terminal editors
    ],
    "poll_interval_s": 1.0,        # how often the front app is re-checked
    "emit_interval_s": 5,          # echo mode only; matches TICK_S in features.py
    "max_buffer_chars": 500,       # cap internal buffer
}

MODIFIERS = {"ctrl", "ctrl_l", "ctrl_r", "cmd", "cmd_l", "cmd_r", "alt", "alt_l", "alt_r"}   # not alt_gr: it types
SEPARATORS = {"space": " ", "enter": " ", "tab": " "}
STOP_KEY = "f1"


# ------------------------------------------------------------------ helpers
def _contains(app_name: str, names) -> bool:
    """Substring match, case insensitive. Used for the block list, where over-matching is the safe error."""
    low = app_name.lower()
    return any(n.lower() in low for n in names)


def _is_listed(app_name: str, names) -> bool:
    """Whole-word match, case insensitive: 'word' matches 'Microsoft Word' but not '1Password', 'code' not 'Xcode'."""
    low = app_name.lower()
    words = set(re.findall(r"[a-z0-9._-]+", low))
    return any(n.lower() == low or n.lower() in words for n in names)


def _is_text_app(app_name: str) -> bool:
    """Is the foreground app on the whitelist?"""
    return _is_listed(app_name, DEFAULT_CONFIG["foreground_app_whitelist"])


def _key_to_char(key) -> Optional[str]:
    """The printable character a pynput key stands for, or None."""
    c = getattr(key, "char", None)
    return c if isinstance(c, str) and len(c) == 1 and c.isprintable() else None


def _get_foreground_app_name() -> Optional[str]:
    """Foreground app name (macOS) or window title (Linux), or None when it cannot be told."""
    try:
        system = platform.system()
        if system == "Darwin":
            result = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to name of first process whose frontmost is true'],
                capture_output=True, text=True, timeout=1)
            return result.stdout.strip() or None
        if system == "Linux":
            result = subprocess.run(["xdotool", "getwindowfocus", "getwindowname"],
                                    capture_output=True, text=True, timeout=1)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
    except Exception:
        pass
    return None


# ------------------------------------------------------------------ monitor class
class TypingMonitor:
    """System-wide typed-text buffer, active only while an allowed app is in front."""

    def __init__(
        self,
        max_buffer: int = DEFAULT_CONFIG["max_buffer_chars"],
        clip_chars: int = DEFAULT_CONFIG["typed_text_clip_chars"],
        whitelist=None,
        blocked=(),
        echo: bool = False,
        emit_interval: float = DEFAULT_CONFIG["emit_interval_s"],
        foreground: Callable[[], Optional[str]] = _get_foreground_app_name,
    ):
        self.clip_chars = clip_chars
        self.whitelist = tuple(DEFAULT_CONFIG["foreground_app_whitelist"] if whitelist is None else whitelist)
        self.blocked = tuple(blocked)
        self.echo = echo
        self.emit_interval = emit_interval
        self.foreground = foreground

        self._buffer: Deque[str] = deque(maxlen=max_buffer)
        self._lock = threading.Lock()
        self._held: set[str] = set()
        self._allowed = False
        self._running = False
        self._listener = None
        self._threads: list[threading.Thread] = []

    # ---- public API ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Start listening. Raises RuntimeError when `pynput` is not installed."""
        if self._running:
            return
        if keyboard is None:
            raise RuntimeError("typed-text monitoring needs the optional 'pynput' package: "
                               'pip install -e ".[typing]"')
        self._running = True
        self._clear()
        self.refresh_allowed()
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()
        self._spawn(self._poll_foreground)
        if self.echo:
            self._spawn(self._periodic_emit)

    def stop(self) -> None:
        """Stop listening and throw the buffer away."""
        self._running = False
        self._clear()
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.stop()
            if listener is not threading.current_thread():
                listener.join(timeout=3)
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(timeout=3)
        self._threads = []

    def get_current_text(self) -> str:
        """The newest `clip_chars` typed characters."""
        with self._lock:
            return "".join(self._buffer)[-self.clip_chars:]

    # ---- foreground check ------------------------------------------------------------
    def refresh_allowed(self) -> bool:
        """Re-read the front app. Leaving an allowed app (or entering a blocked one) empties the buffer."""
        app = self.foreground()
        allowed = bool(app) and _is_listed(app, self.whitelist) and not _contains(app, self.blocked)
        if not allowed:
            self._clear()
        self._allowed = allowed
        return allowed

    def _poll_foreground(self) -> None:
        while self._running:
            self.refresh_allowed()
            time.sleep(DEFAULT_CONFIG["poll_interval_s"])

    # ---- key handling ----------------------------------------------------------------
    def handle_key(self, key) -> Optional[bool]:
        """Process one key press. Returns False to stop the listener (the F1 toggle)."""
        name = getattr(key, "name", None)
        if name in MODIFIERS:
            self._held.add(name)
            return None
        if name == STOP_KEY:
            self._running = False
            self._clear()
            return False
        if not self._allowed:
            return None
        if name == "esc":
            self._clear()
        elif name == "backspace":
            with self._lock:
                if self._buffer:
                    self._buffer.pop()
        elif name in SEPARATORS:
            self._append(SEPARATORS[name])
        elif not self._held:                 # Ctrl/Cmd/Alt + letter is a shortcut, not text
            c = _key_to_char(key)
            if c is not None:
                self._append(c)
        return None

    def _on_press(self, key):
        if not self._running:
            return False
        return self.handle_key(key)

    def _on_release(self, key):
        self._held.discard(getattr(key, "name", None))
        return None if self._running else False

    def _append(self, c: str) -> None:
        with self._lock:
            self._buffer.append(c)

    def _clear(self) -> None:
        with self._lock:
            self._buffer.clear()

    def _spawn(self, target) -> None:
        t = threading.Thread(target=target, daemon=True)
        t.start()
        self._threads.append(t)

    # ---- echo mode (standalone use only) ---------------------------------------------
    def _periodic_emit(self) -> None:
        while self._running:
            time.sleep(self.emit_interval)
            if self._running:
                self._emit_typed_text()

    def _emit_typed_text(self) -> None:
        text = self.get_current_text()
        if not text.strip():
            return
        limit = DEFAULT_CONFIG["typed_text_sentence_clip"]
        if len(text) > limit:
            cut = text.rfind(". ", len(text) - limit)
            if cut > 0:
                text = text[: cut + 1]
        print(json.dumps({"typed_text": text, "timestamp": time.time(), "source": "typing_monitor_tier1"}),
              flush=True)


# ------------------------------------------------------------------ CLI entry point
def run_typing_monitor() -> None:
    """Standalone: print the typed text as JSON lines. Needs `pynput`; press F1 to stop."""
    print("SAIP typing monitor (Linux + macOS). Press F1 to stop. On macOS grant Input Monitoring.", flush=True)
    monitor = TypingMonitor(echo=True)
    monitor.start()
    try:
        while monitor.running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        print("\nMonitor stopped.", flush=True)


if __name__ == "__main__":
    run_typing_monitor()
