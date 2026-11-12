from __future__ import annotations

import os
import shutil
from pathlib import Path

from .base import Platform, run


class Linux(Platform):
    name = "Linux"
    level = "experimental"

    def start_activitywatch(self) -> bool:
        exe = shutil.which("aw-qt")
        return bool(exe) and run(["sh", "-c", f"nohup {exe} >/dev/null 2>&1 &"], timeout=20)[0] == 0

    def activitywatch_data_dir(self):
        return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "activitywatch"

    def permissions(self) -> dict:
        # No grants to click through, but Wayland sessions do not expose the focused window to watchers.
        return {"X11 session (window tracking)": os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland"}

    def permission_hint(self) -> str:
        return ("ActivityWatch's window watcher needs an X11 session: on Wayland it cannot see the focused "
                "window. Log in with an X11/Xorg session, or use a window-tracking extension for your desktop.")

    def can_ask(self) -> bool:
        return shutil.which("zenity") is not None

    def ask(self, title, message, buttons=("Never", "Not now", "Add"), timeout=120, default=True):
        cmd = ["zenity", "--question", "--title", title, "--text", message, "--ok-label", buttons[-1],
               "--cancel-label", buttons[1] if len(buttons) > 2 else buttons[0], "--timeout", str(int(timeout))]
        if len(buttons) > 2:
            cmd += ["--extra-button", buttons[0]]
        code, out = run(cmd, timeout=timeout + 20)
        if len(buttons) > 2 and out.strip() == buttons[0]:
            return buttons[0]
        return {0: buttons[-1], 1: buttons[1] if len(buttons) > 2 else buttons[0]}.get(code)

    def installed_apps(self):
        from .apps import installed_linux
        return installed_linux()

    def front_app(self):
        from .apps import front_linux
        return front_linux()

    def notify(self, title: str, message: str) -> bool:
        return shutil.which("notify-send") is not None and run(["notify-send", title, message], timeout=10)[0] == 0
