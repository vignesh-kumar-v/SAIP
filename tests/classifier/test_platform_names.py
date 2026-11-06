"""The same activity must score the same whichever OS reported it."""
import pytest

from classifier.categories import category


@pytest.mark.parametrize("win, expected", [
    # macOS (what the synthetic data uses)
    ({"app": "Code"}, "editor"), ({"app": "Terminal", "title": "zsh"}, "terminal"),
    ({"app": "Google Chrome", "url": "https://example.com"}, "browser"), ({"app": "zoom.us"}, "meeting"),
    ({"app": "loginwindow"}, "lock"),
    # Windows process names
    ({"app": "Code.exe"}, "editor"), ({"app": "WindowsTerminal.exe", "title": "PowerShell"}, "terminal"),
    ({"app": "chrome.exe", "url": "https://example.com"}, "browser"),
    ({"app": "msedge.exe", "url": "https://youtube.com/watch"}, "media"), ({"app": "WINWORD.EXE"}, "office"),
    ({"app": "Zoom.exe"}, "meeting"), ({"app": "LockApp.exe"}, "lock"), ({"app": "pycharm64.exe"}, "editor"),
    # Linux window classes
    ({"app": "code"}, "editor"), ({"app": "gnome-terminal-server"}, "terminal"), ({"app": "konsole"}, "terminal"),
    ({"app": "google-chrome", "url": "https://example.com"}, "browser"), ({"app": "firefox"}, "browser"),
    ({"app": "soffice"}, "office"), ({"app": "i3lock"}, "lock"),
])
def test_same_category_on_every_os(win, expected):
    assert category(win) == expected


def test_agent_detection_works_in_any_terminal():
    assert category({"app": "WindowsTerminal.exe", "title": "claude — repo"}) == "agent"
    assert category({"app": "konsole", "title": "◐ working"}) == "agent"


def test_chat_sites_in_any_browser():
    assert category({"app": "chrome.exe", "url": "https://claude.ai/chat/1"}) == "chat"
    assert category({"app": "firefox", "url": "https://chatgpt.com/"}) == "chat"


def test_unknown_apps_stay_other_and_empty_names_do_not_crash():
    assert category({"app": "SomethingNew.exe"}) == "other"
    assert category({}) == "other"
