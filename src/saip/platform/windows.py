from __future__ import annotations

import os
import shutil
from pathlib import Path

from .base import Platform, run

TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$n = $t.GetElementsByTagName('text'); $n.Item(0).AppendChild($t.CreateTextNode('%s')) | Out-Null; $n.Item(1).AppendChild($t.CreateTextNode('%s')) | Out-Null
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('SAIP').Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""


class Windows(Platform):
    name = "Windows"
    level = "experimental"

    def shell(self) -> str:
        return "powershell"

    def shell_profile(self, shell):
        if shell != "powershell":
            return None
        for exe in ("pwsh", "powershell"):
            if shutil.which(exe):
                code, out = run([exe, "-NoProfile", "-Command", "$PROFILE"], timeout=20)
                if code == 0 and out:
                    return Path(out.splitlines()[-1].strip())
        docs = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents"
        return docs / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1"

    def activitywatcher_installed(self) -> bool:
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ActivityWatch"
        return local.exists() or super().activitywatcher_installed()

    def install_activitywatch(self):
        if not shutil.which("winget"):
            return False, f"winget not found: download ActivityWatch from {self.activitywatch_url}"
        code, out = run(["winget", "install", "--id", "ActivityWatch.ActivityWatch", "-e",
                         "--accept-package-agreements", "--accept-source-agreements"], timeout=900)
        return code == 0, out[-400:]

    def start_activitywatch(self) -> bool:
        exe = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ActivityWatch" / "aw-qt.exe"
        path = str(exe) if exe.exists() else shutil.which("aw-qt")
        return bool(path) and run(["cmd", "/c", "start", "", path], timeout=20)[0] == 0

    def uninstall_activitywatch(self):
        if not shutil.which("winget"):
            return False, "winget not found: remove ActivityWatch from Settings > Apps"
        code, out = run(["winget", "uninstall", "--id", "ActivityWatch.ActivityWatch", "-e"], timeout=300)
        return code == 0, out[-300:]

    def activitywatch_data_dir(self):
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "activitywatch"

    def can_ask(self) -> bool:
        return shutil.which("powershell") is not None

    def ask(self, title, message, buttons=("Never", "Not now", "Add"), timeout=120, default=True):
        # Two buttons map to Yes / No; three map to Yes = last, No = middle, Cancel = first. A message box has no
        # time limit, so the call is cut off after `timeout` and counts as unanswered.
        esc = lambda t: t.replace("'", "''")
        if len(buttons) == 2:
            kind, mapping = "YesNo", {"Yes": buttons[1], "No": buttons[0]}
            hint = f"Yes = {buttons[1]}   No = {buttons[0]}"
        else:
            kind, mapping = "YesNoCancel", {"Yes": buttons[-1], "No": buttons[1], "Cancel": buttons[0]}
            hint = f"Yes = {buttons[-1]}   No = {buttons[1]}   Cancel = {buttons[0]}"
        script = ("Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show("
                  f"'{esc(message)}`n`n{esc(hint)}','{esc(title)}','{kind}')")
        code, out = run(["powershell", "-NoProfile", "-Command", script], timeout=timeout + 20)
        return mapping.get(out.strip()) if code == 0 else None

    def installed_apps(self):
        from .apps import installed_windows
        return installed_windows()

    def front_app(self):
        from .apps import front_windows
        return front_windows()

    def notify(self, title: str, message: str) -> bool:
        esc = lambda s: s.replace("'", "''")
        return run(["powershell", "-NoProfile", "-Command", TOAST % (esc(title), esc(message))], timeout=20)[0] == 0
