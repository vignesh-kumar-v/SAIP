"""Reading the text in front of the user, so an offer can be about their work and not only their behaviour.

This is the part thunlp/ProactiveAgent gets from raw ActivityWatch events (window titles, typed keys). We read the
focused text field through the OS instead of logging keys: it shows what is on the page, it works for any app
that exposes text (Word, Notes, an editor, a terminal) and nothing is recorded between offers. It runs only when
an offer is about to be written, never on a timer.

Kept on by design:
* opt-in (`read_screen` in private.toml or `saip run --read-screen`)
* apps on the private list, and password fields, are never read
* obvious secrets are masked, and only a window of text around the cursor is kept
The model endpoint may be remote: this is a research tool and the text goes to whichever endpoint you configure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from llm.privacy import is_blocked
from llm.sanitize import clean

TERMINALS = ("terminal", "iterm", "ghostty", "kitty", "wezterm", "alacritty", "warp", "hyper", "tabby")
SEP = "<<SAIP>>"
MIN_CHARS = 20                       # less than this is a blank page, not context

# Tokens that look like secrets wherever they appear (screen text, typed text and code alike).
TOKEN_SECRETS = [
    re.compile(r"\b(sk|pk|rk|ghp|gho|xox[abp])[-_][A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # a long unbroken mix of letters and digits (a token); slashes are excluded so file paths survive
    re.compile(r"\b(?=[A-Za-z0-9+_\-]*\d)(?=[A-Za-z0-9+_\-]*[A-Za-z])[A-Za-z0-9+_\-]{40,}\b"),
]
# "password: ..." in what the person sees or types. Too eager for code (it would hide `api_key: str`), so the chat
# tools mask code with their own, narrower rules instead.
_SECRETS = [*TOKEN_SECRETS[:2], re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b\s*[:=]\s*\S+"),
            TOKEN_SECRETS[2]]


@dataclass(frozen=True)
class Screen:
    app: str
    text: str


def redact(text: str) -> str:
    for pat in _SECRETS:
        text = pat.sub("[hidden]", text)
    return text


CUT, CURSOR = "[…]", "▌"
_BREAK = re.compile(r"\n|[.!?][\"')\]]*\s")


def _clean_start(text: str, start: int, cursor: int, room: int) -> int:
    """Move a cut start forward to the next line or sentence start (else the next space), never past the cursor."""
    stop = min(start + room, cursor if cursor > start else start + room, len(text))
    m = _BREAK.search(text, start, stop)
    if m:
        return m.end()
    sp = text.find(" ", start, stop)
    return sp + 1 if sp != -1 else start


def _clean_end(text: str, end: int, cursor: int, room: int) -> int:
    """Move a cut end back to the last line or sentence end (else the last space), never before the cursor."""
    lo = max(end - room, cursor if 0 < cursor < end else end - room, 0)
    ends = [m.end() for m in _BREAK.finditer(text, lo, end)]
    if ends:
        return ends[-1]
    sp = text.rfind(" ", lo, end)
    return sp if sp != -1 else end


def window_text(text: str, cursor: int, limit: int, tail: bool) -> str:
    """At most about `limit` characters: the end of a terminal, or the stretch around the cursor in a document.

    The excerpt starts and ends at a line, sentence or word boundary, never mid-word; "[…]" marks each side that was
    cut, and "▌" marks the cursor, so the model can tell where the text was trimmed from where the person stopped.
    """
    text = text.replace("\r", "\n")
    has_cursor = not tail and 0 < cursor <= len(text)          # 0 is what macOS reports when it does not know
    if len(text) <= limit:
        out = text[:cursor] + CURSOR + text[cursor:] if has_cursor and text.strip() else text
        return out.strip()
    limit = max(20, limit - 2 * (len(CUT) + 1) - len(CURSOR))      # room for the markers, so the result stays within it
    if tail or cursor <= 0 or cursor > len(text):
        start, end = len(text) - limit, len(text)
        start = _clean_start(text, start, -1, limit // 4)
        out = text[start:end].strip()
        return (CUT + " " + out) if start > 0 else out
    start = max(0, min(cursor - limit // 2, len(text) - limit))
    end = min(len(text), start + limit)
    if start > 0:
        start = _clean_start(text, start, cursor, limit // 4)
    if end < len(text):
        end = _clean_end(text, end, cursor, limit // 4)
    cur = cursor - start
    body = text[start:end]
    body = body[:cur] + CURSOR + body[cur:]
    return ((CUT + " ") if start > 0 else "") + body.strip() + ((" " + CUT) if end < len(text) else "")


def parse(output: str, private_apps: tuple, limit: int) -> Screen | None:
    parts = output.split(SEP, 2)
    if len(parts) != 3:
        return None
    app, cur, text = parts[0].strip(), parts[1].strip(), parts[2]
    if not app or is_blocked(app, private_apps):
        return None
    try:
        cursor = int(float(cur))
    except ValueError:
        cursor = 0
    body = redact(window_text(clean(text), cursor, limit, tail=app.lower().startswith(TERMINALS)))
    return Screen(app, body) if len(body) >= MIN_CHARS else None


def typed_for_model(text: str | None) -> str | None:
    """Typed text as the model sees it: obvious secrets masked, nothing else filtered. None when there is none."""
    if not text or not text.strip():
        return None
    return redact(clean(text)).strip() or None


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


def read_screen(plat, private_apps: tuple = (), limit: int = 1200) -> Screen | None:
    """The focused text in the front app, or None (private app, nothing readable, or not supported here)."""
    raw = plat.read_screen()
    if not raw:
        return None
    found = parse(raw, private_apps, limit)
    if found is None:
        app = raw.split(SEP, 1)[0].strip() or "the front app"
        plat.screen_error = (f"{app} was in front but gave no usable text (it was blank, is on the private list, "
                             f"or the app does not expose its text)")
    return found
