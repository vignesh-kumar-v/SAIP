"""What kind of thing is in front of the user, from a window event's {app, title, url?}.

Only the category reaches the score. Raw titles are used for telling windows apart, never as features.
"""
from __future__ import annotations

import re

def _n(app: str) -> str:
    """Normalise an app name across systems: macOS says "Google Chrome", Windows "chrome.exe", Linux "google-chrome"."""
    a = app.lower().strip()
    return a[:-4] if a.endswith(".exe") else a


def _names(*apps: str) -> frozenset:
    return frozenset(_n(a) for a in apps)


# Each set lists macOS names first, then Windows process names, then Linux window classes. The Windows and
# Linux entries are best effort and have not been checked on real machines: an app that is missing falls
# into "other" and simply stops counting as work, so extend these as real exports show new names.
EDITOR_APPS = _names("Code", "Cursor", "Windsurf", "Xcode", "PyCharm", "IntelliJ IDEA", "Sublime Text",
                     "pycharm64.exe", "idea64.exe", "devenv.exe", "sublime_text.exe", "notepad++.exe",
                     "jetbrains-pycharm", "jetbrains-idea", "sublime_text", "codium", "vscodium", "gedit", "kate")
TERMINAL_APPS = _names("Terminal", "iTerm2", "Warp", "Alacritty", "kitty", "Ghostty",
                       "WindowsTerminal.exe", "wt.exe", "powershell.exe", "pwsh.exe", "cmd.exe", "conhost.exe",
                       "gnome-terminal", "gnome-terminal-server", "konsole", "xterm", "terminator", "tilix",
                       "xfce4-terminal", "foot", "wezterm", "wezterm-gui")
OFFICE_APPS = _names("Microsoft Word", "Microsoft Excel", "Microsoft PowerPoint", "Numbers", "Pages", "Keynote",
                     "WINWORD.EXE", "EXCEL.EXE", "POWERPNT.EXE", "soffice", "soffice.bin", "libreoffice",
                     "libreoffice-writer", "libreoffice-calc")
BROWSERS = _names("Google Chrome", "Safari", "Firefox", "Arc", "Microsoft Edge", "Brave Browser",
                  "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
                  "google-chrome", "chromium", "chromium-browser", "brave-browser", "microsoft-edge", "navigator")
CHAT_APPS = _names("Claude", "ChatGPT", "Gemini", "Claude.exe", "ChatGPT.exe")
CHAT_URLS = ("claude.ai", "chatgpt.com", "gemini.google.com")
WEB_WORK_URLS = ("docs.google.com/document", "docs.google.com/spreadsheets", "overleaf.com/project",
                 "colab.research.google.com", ":8888/lab")
MEDIA_URLS = ("youtube.com", "netflix.com", "spotify.com")
MEETING_APPS = _names("zoom.us", "Microsoft Teams", "Webex", "Zoom.exe", "Teams.exe", "ms-teams.exe", "webex.exe",
                      "zoom", "teams-for-linux")
LOCK_APPS = _names("loginwindow", "LockApp.exe", "LogonUI.exe", "gnome-screensaver", "xscreensaver", "i3lock",
                   "swaylock", "slock")
AGENT_GLYPHS = ("◐", "◑", "✳")      # Claude Code's terminal title while working and waiting

# Time here without typing can mean staring at a problem. Reading a PDF or watching a video cannot.
WORK_SURFACES = frozenset({"editor", "terminal", "terminal_editor", "office", "web_work", "browser"})
AI_TOOLS = frozenset({"agent", "chat"})

_TITLE_NOISE = re.compile(r"^(?:\(\d+\)\s*|●\s*|[◐◑✳⠋⠙⠹⠸]\s*)+")


def category(win: dict) -> str:
    raw = win.get("app") or ""
    app, title, url = _n(raw), win.get("title") or "", win.get("url") or ""
    if app in LOCK_APPS:
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
