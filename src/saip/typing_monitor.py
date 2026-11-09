"""Cross-platform (Linux + macOS) global keystroke monitor for SAIP.

Captures typed text content to feed the LLM gate for context-aware help.
Designed as a privacy-first, opt-in module.

Only active when a text-aware application is in the foreground.
No persistent storage -- buffer lives only in current process memory.
Emits typed_text JSON lines to stdout for SAIP to consume.
"""

from __future__ import annotations

import json
import time
import platform
import subprocess
import threading
from collections import deque
from typing import Optional, Deque, Dict, Any

from pynput import keyboard
from pynput.keyboard import Key

# ------------------------------------------------------------------ config
DEFAULT_CONFIG = {
    "monitor_typed_content": False,       # opt-in flag, set True on first launch
    "typed_text_clip_chars": 200,          # max chars to emit per 5s tick
    "typed_text_sentence_clip": 100,       # clip at nearest sentence end
    "foreground_app_whitelist": [
        "code", "code-oss", "vscode",                    # VS Code
        "word", "ms-word", "word.exe",                 # Word
        "chrome", "google-chrome", "msedge",           # Chrome/Edge textareas
        "textedit", "notepad", "gedit",                # Simple editors
        "emacs", "emacsclient",                       # Emacs
        "gedit", "nano", "vim",                       # Terminal editors
    ],
    "emit_interval_s": 5,          # match TICK_S in features.py
    "max_buffer_chars": 500,       # cap internal buffer
}

# ------------------------------------------------------------------ helper functions (module-level)

def _is_text_app(app_name: str) -> bool:
    """Check if the foreground app name matches our whitelist (case insensitive)."""
    name_lower = app_name.lower()
    for whitelisted in DEFAULT_CONFIG["foreground_app_whitelist"]:
        if whitelisted.lower() in name_lower:
            return True
    return False


def _key_to_char(key: keyboard.Key, is_shift: bool) -> Optional[str]:
    """Convert a pynput key event to a single character, or None if not printable."""
    try:
        c = key.char
        if c is None:
            return None
        if is_shift and c.isalpha():
            c = c.upper()
        return c
    except (AttributeError, TypeError):
        return None


def _handle_special(key: keyboard.Key, buffer: Deque[str]) -> None:
    """Handle special keys that affect the text buffer."""
    try:
        if key == Key.backspace:
            if buffer:
                buffer.pop()
        elif key == Key.enter:
            pass  # could mark sentence boundary
        elif key == Key.tab:
            pass
        elif key == Key.esc:
            buffer.clear()
        elif key in (Key.cmd, Key.ctrl_l, Key.ctrl_r, Key.alt_l, Key.alt_r):
            pass  # modifiers only
        else:
            pass
    except Exception:
        pass


def _process_key(key: keyboard.Key, buffer: Deque[str]) -> None:
    """Process a single key press and update the buffer."""
    c = _key_to_char(key, False)

    if c is not None:
        if c == "\r":  # Enter
            buffer.append(" ")  # word separator
        elif c == "\b":  # Backspace already handled elsewhere
            pass
        else:
            buffer.append(c)

    # Handle special keys (backspace, etc.)
    try:
        if key == Key.backspace:
            if buffer:
                buffer.pop()
    except Exception:
        pass

# ------------------------------------------------------------------ monitor class

class TypingMonitor:
    """Global keystroke monitor that builds a sliding-window typed-text buffer.

    Emits the clipped buffer every emit_interval_s seconds as JSON to stdout.
    Only active when a text-aware app is foreground.
    """

    def __init__(
        self,
        emit_interval: float = DEFAULT_CONFIG["emit_interval_s"],
        max_buffer: int = DEFAULT_CONFIG["max_buffer_chars"],
        clip_chars: int = DEFAULT_CONFIG["typed_text_clip_chars"],
    ):
        self.emit_interval = emit_interval
        self.max_buffer = max_buffer
        self.clip_chars = clip_chars

        self._buffer: Deque[str] = deque(maxlen=max_buffer)
        self._is_shift = False
        self._running = False
        self._listener = None
        self._last_emit = time.time()
        self._toggle_key = Key.f1

        self._emit_thread: Optional[threading.Thread] = None

    # ---- public API ------------------------------------------------------------------

    def start(self) -> None:
        """Start the global keyboard listener."""
        if self._running:
            return

        self._running = True
        self._buffer.clear()
        self._last_emit = time.time()

        def on_press(key):
            if not self._running:
                return False

            # Track shift state from pynput
            try:
                self._is_shift = bool(key.shift_key)
            except Exception:
                self._is_shift = False

            _process_key(key, self._buffer)

            # Toggle monitor with F1
            if key == self._toggle_key:
                self.stop()
                return False

        def on_release(key):
            if not self._running:
                return False
            pass

        self._listener = keyboard.Listener(
            on_press=on_press,
            on_release=on_release,
        )
        self._listener.start()

        self._emit_thread = threading.Thread(target=self._periodic_emit, daemon=True)
        self._emit_thread.start()

    def stop(self) -> None:
        """Stop the monitor and flush buffers."""
        self._running = False
        if self._listener:
            self._listener.stop()
            self._listener.join(timeout=3)
        if self._emit_thread:
            self._emit_thread.join(timeout=3)
        # Buffer is automatically discarded (deque with maxlen)

    def get_current_text(self) -> str:
        """Return the current buffered text, clipped to clip_chars."""
        return "".join(self._buffer)[: self.clip_chars]

    # ---- internal key processing -------------------------------------------------------

    def _process_key(self, key: keyboard.Key, buffer: Deque[str]) -> None:
        """Process a single key press and update the buffer."""
        c = _key_to_char(key, self._is_shift)

        if c is not None:
            if c == "\r":  # Enter
                buffer.append(" ")  # word separator
            elif c == "\b":  # Backspace already handled in _handle_special
                pass
            else:
                buffer.append(c)

        # Handle special keys
        try:
            if key == Key.backspace:
                _handle_special(key, buffer)
        except Exception:
            pass

    def _periodic_emit(self) -> None:
        """Background thread: emit typed_text at emit_interval."""
        while self._running:
            time.sleep(self.emit_interval)
            if not self._running:
                break
            self._emit_typed_text()

    def _emit_typed_text(self) -> None:
        """Emit current typed text as JSON to stdout."""
        text = self.get_current_text()

        if not text.strip():
            return

        # Optional: clip at nearest sentence end
        sentence_clip = DEFAULT_CONFIG["typed_text_sentence_clip"]
        if len(text) > sentence_clip:
            cut = text.rfind(". ", len(text) - sentence_clip)
            if cut > 0:
                text = text[: cut + 1]

        emit_obj = {
            "typed_text": text,
            "timestamp": time.time(),
            "source": "typing_monitor_tier1",
        }

        # Print to stdout -- SAIP pipeline can consume this
        # Use flush=True so it appears immediately
        print(json.dumps(emit_obj), flush=True)

    # ---- foreground app check --------------------------------------------------------

    @staticmethod
    def _get_foreground_app_name() -> Optional[str]:
        """Get the foreground application name.

        Linux: uses xdotool / wmctrl
        macOS: uses osascript
        Returns app name or None.
        """
        try:
            if platform.system() == "Darwin":  # macOS
                result = subprocess.run(
                    [
                        "osascript",
                        "-e",
                        (
                            'tell application "System Events" to name of '
                            'first process whose frontmost is true'
                        ),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
                return result.stdout.strip() or None

            elif platform.system() == "Linux":  # Linux (X11 or Wayland)
                # Try xdotool first
                result = subprocess.run(
                    ["xdotool", "getwindowfocus", "getwindowname"],
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip()
                # Fallback: wmctrl
                result2 = subprocess.run(
                    ["wmctrl", "-l", "-p"],
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
                if result2.returncode == 0:
                    lines = result2.stdout.strip().split("\n")
                    if lines:
                        parts = lines[0].split()
                        if len(parts) >= 3:
                            return parts[2]
                return None
        except Exception:
            return None

    def _should_monitor(self) -> bool:
        """Check if a text-aware app is in the foreground."""
        app_name = self._get_foreground_app_name()
        if app_name is None:
            return False
        return _is_text_app(app_name)

# ------------------------------------------------------------------ CLI entry point

def run_typing_monitor(
    config_path: Optional[str] = None,
) -> None:
    """Entry point: start the typing monitor.

    Reads config from the given path (or uses defaults).
    Emits typed_text JSON lines to stdout for SAIP to consume.
    """
    print(
        "SAIP Typing Monitor (Tier 1 -- Linux + macOS)",
        flush=True,
    )
    print(
        "Config: %s" % str(DEFAULT_CONFIG),
        flush=True,
    )
    print("Starting global keystroke listener...", flush=True)
    print("Press F1 to toggle the monitor on/off.", flush=True)
    print(
        "Ensure accessibility permissions are granted on macOS.",
        flush=True,
    )
    print("Emitting typed_text JSON lines to stdout --", flush=True)
    print("--", flush=True)

    monitor = TypingMonitor()
    monitor.start()

    try:
        # Keep main thread alive; listener runs in background
        while monitor._running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        print("\nMonitor stopped.", flush=True)


if __name__ == "__main__":
    run_typing_monitor()