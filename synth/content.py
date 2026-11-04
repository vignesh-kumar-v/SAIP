"""Static window definitions for the synthetic day generator.

Generated content (projects, errors, pages) lives in `world.py`.
Everything here is fabricated; nothing comes from the user's real logs.
"""
from __future__ import annotations

CHAT_WINDOWS = [
    {"app": "Claude", "title": "Claude"},
    {"app": "Google Chrome", "title": "ChatGPT", "url": "https://chatgpt.com/"},
    {"app": "Google Chrome", "title": "Claude", "url": "https://claude.ai/new"},
]

# Sensitive apps the exclusion filter must drop.
SENSITIVE_WINDOWS = [
    {"app": "‎WhatsApp", "title": "‎WhatsApp"},
    {"app": "FaceTime", "title": "FaceTime"},
    {"app": "Messages", "title": "Messages"},
]
SENSITIVE_APPS = {w["app"] for w in SENSITIVE_WINDOWS}


def is_sensitive(win: dict) -> bool:
    return win.get("app") in SENSITIVE_APPS


# Background windows that steal focus for a second or two (cmd-tab, notifications, Spotlight).
NOISE_WINDOWS = [
    {"app": "Finder", "title": "Downloads"},
    {"app": "Finder", "title": "Documents"},
    {"app": "Slack", "title": "general - ASU CSE - Slack"},
    {"app": "Mail", "title": "Inbox — iCloud (2,418 messages, 6 unread)"},
    {"app": "Notification Center", "title": ""},
    {"app": "Spotlight", "title": "Spotlight"},
    {"app": "System Settings", "title": "Wi-Fi"},
    {"app": "Activity Monitor", "title": "Activity Monitor"},
    {"app": "Calendar", "title": "Calendar"},
    {"app": "Spotify", "title": "Spotify"},
    {"app": "Messages", "title": "Messages"},                       # sensitive: must be filtered
    {"app": "‎WhatsApp", "title": "‎WhatsApp"},           # sensitive
]

SPINNER = ["⠋", "⠙", "⠹", "⠸"]                  # terminal title spinner frames

LOCK_WIN = {"app": "loginwindow", "title": ""}


def editor_win(project: str, fname: str) -> dict:
    return {"app": "Code", "title": f"{fname} — {project}"}


def browser_win(app: str, url: str, title: str) -> dict:
    return {"app": app, "title": title, "url": url}
