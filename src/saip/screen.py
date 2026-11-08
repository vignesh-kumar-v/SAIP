"""Reading the text in front of the user, so an offer can be about their work and not only their behaviour.

This is the part thunlp/ProactiveAgent gets from raw ActivityWatch events (window titles, typed keys). We read the
focused text field through the OS instead of logging keys: it shows what is on the page, it works for any app
that exposes text (Word, Notes, an editor, a terminal) and nothing is recorded between offers. It runs only when
an offer is about to be written, never on a timer.

Guards, all on by design:
* opt-in (`read_screen` in private.toml or `saip run --read-screen`)
* the model endpoint must be on this machine (localhost); otherwise nothing is read
* apps on the private list, and password fields, are never read
* obvious secrets are masked, and only a window of text around the cursor is kept
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}
TERMINALS = ("terminal", "iterm", "ghostty", "kitty", "wezterm", "alacritty", "warp", "hyper", "tabby")
SEP = "<<SAIP>>"
MIN_CHARS = 20                       # less than this is a blank page, not context

_SECRETS = [
    re.compile(r"\b(sk|pk|rk|ghp|gho|xox[abp])[-_][A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b\s*[:=]\s*\S+"),
    # a long unbroken mix of letters and digits (a token); slashes are excluded so file paths survive
    re.compile(r"\b(?=[A-Za-z0-9+_\-]*\d)(?=[A-Za-z0-9+_\-]*[A-Za-z])[A-Za-z0-9+_\-]{40,}\b"),
]


@dataclass(frozen=True)
class Screen:
    app: str
    text: str


def is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in LOCAL_HOSTS or host.endswith(".localhost")


def redact(text: str) -> str:
    for pat in _SECRETS:
        text = pat.sub("[hidden]", text)
    return text


def window_text(text: str, cursor: int, limit: int, tail: bool) -> str:
    """At most `limit` characters: the end of a terminal, or the stretch around the cursor in a document."""
    text = text.replace("\r", "\n")
    if len(text) <= limit:
        return text.strip()
    if tail or cursor <= 0 or cursor > len(text):
        return text[-limit:].strip()
    start = max(0, min(cursor - limit // 2, len(text) - limit))
    return text[start:start + limit].strip()


def parse(output: str, private_apps: tuple, limit: int) -> Screen | None:
    parts = output.split(SEP, 2)
    if len(parts) != 3:
        return None
    app, cur, text = parts[0].strip(), parts[1].strip(), parts[2]
    if not app or any(p.lower() == app.lower() for p in private_apps):
        return None
    try:
        cursor = int(float(cur))
    except ValueError:
        cursor = 0
    body = redact(window_text(text, cursor, limit, tail=app.lower().startswith(TERMINALS)))
    return Screen(app, body) if len(body) >= MIN_CHARS else None


def copy_to_clipboard(text: str) -> bool:
    import shutil
    import subprocess
    cmd = next(([c] for c in ("pbcopy", "clip", "wl-copy") if shutil.which(c)), None)
    if cmd is None and shutil.which("xclip"):
        cmd = ["xclip", "-selection", "clipboard"]
    if cmd is None:
        return False
    try:
        subprocess.run(cmd, input=text.encode("utf-8"), timeout=5, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def read_screen(plat, endpoint: str, private_apps: tuple = (), limit: int = 1200) -> Screen | None:
    """The focused text in the front app, or None (not allowed, nothing readable, or not supported here)."""
    if not is_local(endpoint):
        return None
    raw = plat.read_screen()
    if not raw:
        return None
    found = parse(raw, private_apps, limit)
    if found is None:
        app = raw.split(SEP, 1)[0].strip() or "the front app"
        plat.screen_error = (f"{app} was in front but gave no usable text (it was blank, is on the private list, "
                             f"or the app does not expose its text)")
    return found
