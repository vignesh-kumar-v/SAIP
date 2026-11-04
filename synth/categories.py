"""App categories, so features can say "in a meeting" or "in a work surface" without
hard-coding one app (an IDE) as the meaning of flow."""
from __future__ import annotations

from .content import SENSITIVE_APPS

EDITOR_APPS = {"Code", "Xcode", "PyCharm", "Sublime Text"}
OFFICE_APPS = {"Microsoft Word", "Microsoft Excel", "Numbers", "Pages", "Keynote"}
WEB_WORK_MARKERS = ("docs.google.com/document", "overleaf.com/project", "colab.research.google.com",
                    ":8888/lab", "docs.google.com/spreadsheets")
MEDIA_MARKERS = ("youtube.com", "netflix.com", "spotify.com")
CHAT_TITLES = {"Claude", "ChatGPT"}
WORK_CATEGORIES = {"editor", "office", "web_work", "terminal_editor"}


def category(win: dict) -> str:
    """Coarse category of a window event's data: {app, title, url?}."""
    app, title, url = win.get("app", ""), win.get("title", ""), win.get("url") or ""
    if app in EDITOR_APPS:
        return "editor"
    if app == "Terminal":
        if "Claude Code" in title:
            return "agent"                       # supervising an AI coding agent
        if "- VIM" in title or "NVIM" in title:
            return "terminal_editor"             # vim / tmux, possibly over ssh
        return "terminal"
    if app in OFFICE_APPS:
        return "office"
    if app == "Claude" or (app in ("Google Chrome", "Safari") and title in CHAT_TITLES):
        return "chat"
    if app == "zoom.us":
        return "meeting"
    if app in SENSITIVE_APPS:
        return "messaging"
    if app in ("Google Chrome", "Safari"):
        if any(m in url for m in WEB_WORK_MARKERS) or title.endswith(".ipynb - JupyterLab"):
            return "web_work"
        if any(m in url for m in MEDIA_MARKERS):
            return "media"
        return "browser"
    if app in ("Mail", "Slack"):
        return "comms"
    if app in ("Preview", "Notes"):
        return "reader"
    if app == "loginwindow":
        return "lock"
    return "system"
