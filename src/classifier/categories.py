"""What kind of thing is in front of the user, from a window event's {app, title, url?}.

Only the category reaches the score. Raw titles are used for telling windows apart, never as features.
"""
from __future__ import annotations

import re

EDITOR_APPS = {"Code", "Cursor", "Windsurf", "Xcode", "PyCharm", "IntelliJ IDEA", "Sublime Text"}
TERMINAL_APPS = {"Terminal", "iTerm2", "Warp", "Alacritty", "kitty", "Ghostty"}
OFFICE_APPS = {"Microsoft Word", "Microsoft Excel", "Microsoft PowerPoint", "Numbers", "Pages", "Keynote"}
BROWSERS = {"Google Chrome", "Safari", "Firefox", "Arc", "Microsoft Edge", "Brave Browser"}
CHAT_APPS = {"Claude", "ChatGPT", "Gemini"}
CHAT_URLS = ("claude.ai", "chatgpt.com", "gemini.google.com")
WEB_WORK_URLS = ("docs.google.com/document", "docs.google.com/spreadsheets", "overleaf.com/project",
                 "colab.research.google.com", ":8888/lab")
MEDIA_URLS = ("youtube.com", "netflix.com", "spotify.com")
MEETING_APPS = {"zoom.us", "Microsoft Teams", "Webex"}
LOCK_APP = "loginwindow"
AGENT_GLYPHS = ("◐", "◑", "✳")      # Claude Code's terminal title while working and waiting

# Time here without typing can mean staring at a problem. Reading a PDF or watching a video cannot.
WORK_SURFACES = frozenset({"editor", "terminal", "terminal_editor", "office", "web_work", "browser"})
AI_TOOLS = frozenset({"agent", "chat"})

_TITLE_NOISE = re.compile(r"^(?:\(\d+\)\s*|●\s*|[◐◑✳⠋⠙⠹⠸]\s*)+")


def category(win: dict) -> str:
    app, title, url = win.get("app") or "", win.get("title") or "", win.get("url") or ""
    if app == LOCK_APP:
        return "lock"
    if app in EDITOR_APPS:
        return "editor"
    if app in TERMINAL_APPS:
        low = title.lower()
        if "claude" in low or "codex" in low or any(g in title for g in AGENT_GLYPHS):
            return "agent"
        return "terminal_editor" if "- VIM" in title or "NVIM" in title else "terminal"
    if app in OFFICE_APPS:
        return "office"
    if app in CHAT_APPS or any(m in url for m in CHAT_URLS):
        return "chat"
    if app in MEETING_APPS:
        return "meeting"
    if app in BROWSERS:
        if any(m in url for m in WEB_WORK_URLS):
            return "web_work"
        return "media" if any(m in url for m in MEDIA_URLS) else "browser"
    return "other"


def window_key(win: dict) -> tuple[str, str]:
    """Identity of a window, ignoring unsaved dots, notification counts and spinner frames."""
    title = win.get("title") or ""
    for glyph in AGENT_GLYPHS:
        title = title.replace(glyph, "")
    return win.get("app") or "", _TITLE_NOISE.sub("", title).strip()
