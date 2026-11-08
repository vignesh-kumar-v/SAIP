from __future__ import annotations

import re
import shutil
from pathlib import Path

from .base import Platform, run

PANES = {"Accessibility": "Privacy_Accessibility", "Input Monitoring": "Privacy_ListenEvent",
         "Screen Recording": "Privacy_ScreenCapture"}


def dialog_script(title: str, message: str, buttons: tuple, timeout: int, default: bool) -> str:
    esc = lambda t: t.replace("\\", "\\\\").replace('"', '\\"')
    labels = ", ".join(f'"{esc(b)}"' for b in buttons)
    script = f'display dialog "{esc(message)}" with title "{esc(title)}" buttons {{{labels}}}'
    if default:
        script += f' default button "{esc(buttons[-1])}"'
    return script + f" giving up after {int(timeout)}"


# Needs Accessibility for the app that runs saip (Terminal, iTerm, ...): System Settings > Privacy & Security.
READ_SCRIPT = """
tell application "System Events"
    set p to first application process whose frontmost is true
    set pname to name of p
    try
        name of window 1 of p
    on error
        return "NO_ACCESS"
    end try
    set cur to 0
    set txt to ""
    try
        set el to value of attribute "AXFocusedUIElement" of p
        if (value of attribute "AXRole" of el) is "AXSecureTextField" then return pname & "<<SAIP>>0<<SAIP>>"
        try
            set txt to (value of attribute "AXValue" of el) as text
        end try
        try
            set cur to item 1 of (value of attribute "AXSelectedTextRange" of el)
        end try
    end try
    return pname & "<<SAIP>>" & cur & "<<SAIP>>" & txt
end tell
"""


class MacOS(Platform):
    name = "macOS"
    level = "full"

    def shell_profile(self, shell):
        if shell == "bash":
            return Path.home() / ".bash_profile"
        return super().shell_profile(shell)

    def activitywatcher_installed(self) -> bool:
        return Path("/Applications/ActivityWatch.app").exists() or super().activitywatcher_installed()

    def install_activitywatch(self):
        if not shutil.which("brew"):
            return False, f"Homebrew not found: download ActivityWatch from {self.activitywatch_url}"
        code, out = run(["brew", "install", "--cask", "activitywatch"], timeout=900)
        return code == 0, out[-400:]

    def start_activitywatch(self) -> bool:
        return run(["open", "-a", "ActivityWatch"])[0] == 0

    def uninstall_activitywatch(self):
        if not shutil.which("brew"):
            return False, "Homebrew not found: drag ActivityWatch out of /Applications yourself"
        run(["osascript", "-e", 'quit app "ActivityWatch"'], timeout=20)
        code, out = run(["brew", "uninstall", "--cask", "activitywatch"], timeout=300)
        return code == 0, out[-300:]

    def activitywatch_data_dir(self):
        return Path.home() / "Library" / "Application Support" / "activitywatch"

    def permissions(self) -> dict:
        # Our own process needs none: it only talks to ActivityWatcher over localhost and reads git. The grants
        # that matter belong to ActivityWatch's watchers, which `saip doctor` checks through the data they produce.
        return {}

    def open_permission_pane(self, name: str) -> bool:
        pane = PANES.get(name)
        if not pane:
            return False
        return run(["open", f"x-apple.systempreferences:com.apple.preference.security?{pane}"])[0] == 0

    def permission_hint(self) -> str:
        return ("ActivityWatch needs Accessibility (window titles) and Input Monitoring (key and click counts). "
                "Turn ActivityWatch on in both lists in System Settings > Privacy & Security.")

    def can_ask(self) -> bool:
        return True

    def ask(self, title, message, buttons=("Never", "Not now", "Add"), timeout=120, default=True):
        code, out = run(["osascript", "-e", dialog_script(title, message, buttons, timeout, default)],
                        timeout=timeout + 20)
        if code != 0 or "gave up:true" in out:
            return None
        m = re.search(r"button returned:(.*?)(?:, gave up:|$)", out)
        return m.group(1).strip() if m and m.group(1).strip() in buttons else None

    def read_screen(self):
        code, out = run(["osascript", "-e", READ_SCRIPT], timeout=15)
        if code != 0 or out == "NO_ACCESS":
            self.screen_error = ("Accessibility is off for the app running saip. Turn it on for your terminal in "
                                 "System Settings > Privacy & Security > Accessibility, then restart saip.")
            return None
        self.screen_error = ""
        return out

    def notify(self, title: str, message: str) -> bool:
        esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
        return run(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'])[0] == 0
