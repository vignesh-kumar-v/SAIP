"""The one block list: apps whose typed text, screen text and names never reach the model.

`llm.private_apps` in private.toml is that list. The typing monitor, the screen reader and the activity summary all
ask `is_blocked`, so a name blocked once is blocked everywhere.

Matching is deliberately generous, because over-matching is the safe mistake here. Names are compared as words,
ignoring case, ".exe"/".app" and separators, and in both directions: "Chrome" blocks "Google Chrome", and a block
entry "Google Chrome" also blocks an app reported as "chrome.exe". Whole words only, so "Mail" does not block
"Gmail" and "Code" does not block "Xcode".
"""
from __future__ import annotations

import re

PRIVATE_LABEL = "Private app"

# Groups for the picker. Lower-case, already normalised (see `norm`).
PASSWORD_MANAGERS = frozenset({"1password", "1password 7", "bitwarden", "keepassxc", "keepass", "keychain access",
                               "dashlane", "lastpass", "enpass", "passwords", "keeper", "proton pass", "nordpass"})
MESSAGING = frozenset({"messages", "whatsapp", "signal", "telegram", "telegram desktop", "slack", "discord",
                       "microsoft teams", "teams", "ms teams", "messenger", "wechat", "line", "skype", "element",
                       "facetime", "viber", "beeper"})
MAIL = frozenset({"mail", "outlook", "microsoft outlook", "thunderbird", "spark", "spark desktop", "airmail",
                  "mimestream", "superhuman", "proton mail", "evolution", "geary", "mailspring", "canary mail"})
BROWSERS = frozenset({"safari", "google chrome", "chrome", "firefox", "arc", "microsoft edge", "msedge", "edge",
                      "brave browser", "brave", "opera", "vivaldi", "chromium", "zen", "zen browser", "orion",
                      "firefox developer edition", "librewolf", "google chrome canary"})
GROUPS = (("password", "Password managers", PASSWORD_MANAGERS), ("messaging", "Messaging", MESSAGING),
          ("mail", "Mail", MAIL), ("browser", "Browsers", BROWSERS))
SUGGESTED_GROUPS = ("password", "messaging", "mail")      # ticked by default in the setup wizard


def norm(name: str | None) -> str:
    """'Google-Chrome.exe' -> 'google chrome'."""
    s = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]", "", name or "").strip().lower()
    s = re.sub(r"\.(exe|app)$", "", s)
    s = re.sub(r"[\s._\-]+", " ", s)
    return s.strip()


def _within(needle: str, hay: str) -> bool:
    return bool(needle) and re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", hay) is not None


def is_blocked(app: str | None, blocked) -> bool:
    """Is this app (a name, or on Linux possibly a window title) on the block list?"""
    a = norm(app)
    if not a:
        return False
    for b in blocked:
        nb = norm(b)
        if nb and (a == nb or _within(nb, a) or _within(a, nb)):
            return True
    return False


def group_of(name: str) -> str:
    n = norm(name)
    for key, _label, names in GROUPS:
        if n in names or any(_within(x, n) for x in names if len(x) > 3):
            return key
    return "other"


def shown(app: str | None, blocked) -> str | None:
    """The app name a UI may show: blocked apps become 'Private app'."""
    if not app:
        return None
    return PRIVATE_LABEL if is_blocked(app, blocked) else app
