"""Start SAIP when you log in, the usual way for each OS. Off unless you turn it on.

    macOS    ~/Library/LaunchAgents/dev.saip.agent.plist   (a per-user LaunchAgent)
    Windows  HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run, value "SAIP"
    Linux    ~/.config/autostart/saip.desktop               (the XDG autostart folder)

The command it starts is `python -m saip run --no-browser`, with the config file passed by path because the
login session does not start in this folder. `saip uninstall` removes the entry.
"""
from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

LABEL = "dev.saip.agent"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "SAIP"


def command(config: Path | None = None, python: str | None = None) -> list[str]:
    python = python or sys.executable
    if sys.platform.startswith("win"):                       # no console window at login
        w = Path(python).with_name("pythonw.exe")
        python = str(w) if w.exists() else python
    cmd = [python, "-m", "saip", "run", "--no-browser"]
    if config:
        cmd += ["--config", str(Path(config).resolve())]
    return cmd


def mac_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def linux_path(home: Path | None = None) -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", (home or Path.home()) / ".config")) if home is None else home / ".config"
    return base / "autostart" / "saip.desktop"


def mac_plist(cmd: list[str], workdir: Path, log: Path) -> bytes:
    return plistlib.dumps({"Label": LABEL, "ProgramArguments": cmd, "RunAtLoad": True,
                           "WorkingDirectory": str(workdir), "StandardOutPath": str(log),
                           "StandardErrorPath": str(log), "ProcessType": "Interactive"})


def _quote(arg: str) -> str:
    return '"' + arg.replace('"', '\\"') + '"' if any(c in arg for c in ' \t"') else arg


def linux_desktop(cmd: list[str]) -> str:
    return ("[Desktop Entry]\nType=Application\nName=SAIP\nComment=Proactive assistant: notices when you are stuck\n"
            f"Exec={' '.join(_quote(c) for c in cmd)}\nX-GNOME-Autostart-enabled=true\nTerminal=false\n")


def windows_value(cmd: list[str]) -> str:
    return " ".join(f'"{c}"' if " " in c else c for c in cmd)


def is_enabled() -> bool:
    if sys.platform == "darwin":
        return mac_path().exists()
    if sys.platform.startswith("win"):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, RUN_VALUE)
                return True
        except OSError:
            return False
    return linux_path().exists()


def enable(config: Path | None = None) -> str:
    from llm.config import user_config_dir
    cmd = command(config)
    workdir = Path(config).resolve().parent if config else Path.home()
    if sys.platform == "darwin":
        p = mac_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        user_config_dir().mkdir(parents=True, exist_ok=True)
        p.write_bytes(mac_plist(cmd, workdir, user_config_dir() / "saip.log"))
        where = str(p)
    elif sys.platform.startswith("win"):
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, windows_value(cmd))
        where = "HKCU\\" + RUN_KEY
    else:
        p = linux_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(linux_desktop(cmd), encoding="utf-8")
        where = str(p)
    from .setup import update_manifest
    update_manifest(autostart=True)
    return where


def disable() -> bool:
    if sys.platform == "darwin":
        p = mac_path()
        existed = p.exists()
        p.unlink(missing_ok=True)
        return existed
    if sys.platform.startswith("win"):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, RUN_VALUE)
                return True
        except OSError:
            return False
    p = linux_path()
    existed = p.exists()
    p.unlink(missing_ok=True)
    return existed


def where() -> str:
    if sys.platform == "darwin":
        return str(mac_path())
    if sys.platform.startswith("win"):
        return "HKCU\\" + RUN_KEY + " (SAIP)"
    return str(linux_path())
