"""What differs between operating systems. Everything else in the app is shared.

Each OS adapter answers the same questions: is ActivityWatcher installed and how to install or start
it, where does the shell read its start-up file, what permissions are needed, and how to show a
notification. `level` says how far the adapter has been exercised: "full" means tested on a real machine,
"experimental" means written to the same interface but not yet run on one.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def run(cmd: list[str], timeout: float = 120.0) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)


class Platform:
    name = "unknown"
    level = "experimental"
    activitywatch_url = "https://activitywatch.net/downloads/"

    # ---- shell ----
    def shell(self) -> str:
        """The user's interactive shell: zsh, bash, powershell, or whatever $SHELL names."""
        return Path(os.environ.get("SHELL", "")).name or "bash"

    def shell_profile(self, shell: str) -> Path | None:
        home = Path.home()
        return {"zsh": Path(os.environ.get("ZDOTDIR", home)) / ".zshrc", "bash": home / ".bashrc"}.get(shell)

    # ---- ActivityWatcher ----
    def activitywatch_installed(self) -> bool:
        return shutil.which("aw-qt") is not None or shutil.which("aw-server") is not None

    def install_activitywatch(self) -> tuple[bool, str]:
        return False, f"install ActivityWatch from {self.activitywatch_url}, then run this again"

    def start_activitywatch(self) -> bool:
        return False

    def uninstall_activitywatch(self) -> tuple[bool, str]:
        return False, "remove ActivityWatch with your system's package manager"

    def activitywatch_data_dir(self) -> Path | None:
        return None

    # ---- permissions ----
    def permissions(self) -> dict:
        """Permission name -> True / False / None (unknown). Empty means none are needed."""
        return {}

    def open_permission_pane(self, name: str) -> bool:
        return False

    def permission_hint(self) -> str:
        return ""

    # ---- questions ----
    def can_ask(self) -> bool:
        """Whether this system can pop up a dialog with buttons (otherwise the question is asked in the terminal)."""
        return False

    def ask(self, title: str, message: str, buttons: tuple = ("Never", "Not now", "Add"), timeout: int = 120,
            default: bool = True) -> str | None:
        """Show a dialog and return the button pressed, or None if it timed out, was dismissed or is unsupported.

        `buttons` run from the "no" side to the "yes" side. `default=False` leaves every button un-highlighted, so a
        stray Return key from someone typing cannot answer for them.
        """
        return None

    # ---- reading the screen ----
    screen_error = ""            # why the last read_screen returned None, for the user

    def read_screen(self) -> str | None:
        """The front app's name, the cursor position and its focused text, as `app<<SAIP>>cursor<<SAIP>>text`.

        None when this system cannot read it. Only called when the user has opted in (see saip/screen.py).
        """
        return None

    # ---- notifications ----
    def notify(self, title: str, message: str) -> bool:
        return False

    # ---- apps ----
    def installed_apps(self) -> list[dict]:
        """Installed applications as {"label", "value"} rows (see platform/apps.py). Empty when unknown."""
        return []

    def front_app(self) -> str | None:
        """The name of the app in front, or None when it cannot be told."""
        return None
