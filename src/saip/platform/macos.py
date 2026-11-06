from __future__ import annotations

import re
import shutil
from pathlib import Path

from .base import Platform, run

PANES = {"Accessibility": "Privacy_Accessibility", "Input Monitoring": "Privacy_ListenEvent",
         "Screen Recording": "Privacy_ScreenCapture"}


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

    def ask(self, title, message, buttons=("Never", "Not now", "Add")):
        esc = lambda t: t.replace("\\", "\\\\").replace('"', '\\"')
        labels = ", ".join(f'"{esc(b)}"' for b in buttons)
        script = (f'display dialog "{esc(message)}" with title "{esc(title)}" buttons {{{labels}}} '
                  f'default button "{esc(buttons[-1])}" giving up after 120')
        code, out = run(["osascript", "-e", script], timeout=140)
        if code != 0 or "gave up:true" in out:
            return None
        m = re.search(r"button returned:(.*?)(?:, gave up:|$)", out)
        return m.group(1).strip() if m and m.group(1).strip() in buttons else None

    def notify(self, title: str, message: str) -> bool:
        esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
        return run(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'])[0] == 0
