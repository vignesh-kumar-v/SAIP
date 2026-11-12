"""Which apps are installed, and which one is in front. One function per OS, each readable on its own.

`installed_*` return rows {"label": shown to you, "value": the name to put on the block list}. The value is the
name ActivityWatch and the typing monitor will see for that app where it can be told (a macOS bundle's
CFBundleName, a Linux StartupWMClass, a Windows .exe), so blocking it matches what is actually reported. Matching
in llm/privacy.py is also loose on purpose, so the label works as well.

The parsers take their folders as arguments, so tests can point them at fixture files.
"""
from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")      # direction marks some bundles carry

# ---------------------------------------------------------------------------------------------------- macOS
MAC_DIRS = ("/Applications", "/Applications/Utilities", "/System/Applications", "/System/Applications/Utilities",
            "~/Applications")


def installed_macos(dirs=MAC_DIRS) -> list[dict]:
    rows = []
    for d in dirs:
        root = Path(d).expanduser()
        if not root.is_dir():
            continue
        for app in _mac_bundles(root):
            label = app.stem
            value = label
            try:
                with (app / "Contents" / "Info.plist").open("rb") as f:
                    info = plistlib.load(f)
                value = INVISIBLE.sub("", str(info.get("CFBundleDisplayName") or info.get("CFBundleName") or label)) or label
                if info.get("LSUIElement") in (True, "1") and not info.get("CFBundleDisplayName"):
                    continue                          # menu-bar helpers without a window of their own
            except (OSError, plistlib.InvalidFileException, ValueError):
                pass
            rows.append({"label": label, "value": value})
    return rows


def _mac_bundles(root: Path):
    for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
        if p.suffix == ".app":
            yield p
        elif p.is_dir() and not p.name.startswith("."):    # one level of folders, e.g. "Microsoft Office"
            try:
                yield from (q for q in sorted(p.iterdir()) if q.suffix == ".app")
            except OSError:
                continue


FRONT_MAC = 'tell application "System Events" to name of first process whose frontmost is true'


def front_macos() -> str | None:
    try:
        r = subprocess.run(["osascript", "-e", FRONT_MAC], capture_output=True, text=True, timeout=1.5)
        return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------------------------------------------- Linux
def linux_dirs() -> list[Path]:
    data = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    dirs = [home / "applications"] + [Path(d) / "applications" for d in data if d]
    dirs += [Path("/var/lib/flatpak/exports/share/applications"),
             home / "flatpak" / "exports" / "share" / "applications", Path("/var/lib/snapd/desktop/applications")]
    return list(dict.fromkeys(dirs))


def parse_desktop(text: str) -> dict | None:
    """One .desktop file -> a row, or None for entries that are hidden or not applications."""
    entry, section = {}, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line
            continue
        if section != "[Desktop Entry]" or "=" not in line or line.startswith("#"):
            continue
        k, v = line.split("=", 1)
        entry.setdefault(k.strip(), v.strip())
    if entry.get("Type", "Application") != "Application" or entry.get("NoDisplay") == "true" or entry.get("Hidden") == "true":
        return None
    name = entry.get("Name")
    if not name:
        return None
    exe = ""
    if entry.get("Exec"):
        parts = [p for p in entry["Exec"].split() if not p.startswith("%") and "=" not in p]
        if parts and Path(parts[0]).name in ("env", "flatpak", "snap") and len(parts) > 1:
            parts = parts[-1:] if Path(parts[0]).name == "flatpak" else parts[1:]
        exe = Path(parts[0]).name if parts else ""
    return {"label": name, "value": entry.get("StartupWMClass") or exe or name}


def installed_linux(dirs=None) -> list[dict]:
    rows = []
    for d in dirs if dirs is not None else linux_dirs():
        d = Path(d)
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.desktop")):
            try:
                row = parse_desktop(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if row:
                rows.append(row)
    return rows


def front_linux() -> str | None:
    """The focused window's class (what aw-watcher-window reports as the app), else its title. X11 only."""
    try:
        wid = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True, timeout=1)
        if wid.returncode != 0 or not wid.stdout.strip():
            return None
        xp = subprocess.run(["xprop", "-id", wid.stdout.strip(), "WM_CLASS"], capture_output=True, text=True, timeout=1)
        m = re.findall(r'"([^"]*)"', xp.stdout)
        if m:
            return m[-1]
        name = subprocess.run(["xdotool", "getwindowname", wid.stdout.strip()], capture_output=True, text=True, timeout=1)
        return name.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------------------------------------------- Windows
def start_menu_dirs() -> list[Path]:
    out = []
    for base in (os.environ.get("ProgramData"), os.environ.get("APPDATA")):
        if base:
            out.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return out


def installed_windows(dirs=None, registry=None) -> list[dict]:
    """Start Menu shortcuts (by name) plus registry App Paths and uninstall entries (by .exe)."""
    rows = []
    for d in dirs if dirs is not None else start_menu_dirs():
        d = Path(d)
        if d.is_dir():
            for f in sorted(d.rglob("*.lnk")):
                stem = f.stem
                if re.search(r"(?i)\b(uninstall|readme|help|website|documentation|release notes)\b", stem):
                    continue
                rows.append({"label": stem, "value": stem})
    for label, exe in (registry if registry is not None else _registry_apps()):
        rows.append({"label": label, "value": exe})
    return rows


def _registry_apps() -> list[tuple[str, str]]:
    if not sys.platform.startswith("win"):
        return []
    import winreg
    found = []
    roots = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
             (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths")]
    for hive, path in roots:
        try:
            with winreg.OpenKey(hive, path) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    exe = winreg.EnumKey(k, i)
                    if exe.lower().endswith(".exe"):
                        found.append((Path(exe).stem, exe))
        except OSError:
            continue
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for path in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                     r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try:
                with winreg.OpenKey(hive, path) as k:
                    for i in range(winreg.QueryInfoKey(k)[0]):
                        try:
                            with winreg.OpenKey(k, winreg.EnumKey(k, i)) as sub:
                                name = winreg.QueryValueEx(sub, "DisplayName")[0]
                                icon = winreg.QueryValueEx(sub, "DisplayIcon")[0]
                        except OSError:
                            continue
                        exe = Path(str(icon).split(",")[0].strip('"')).name
                        if name and exe.lower().endswith(".exe"):
                            found.append((str(name), exe))
            except OSError:
                continue
    return found


def front_windows() -> str | None:
    """The .exe of the foreground window (what aw-watcher-window reports on Windows), e.g. 'chrome.exe'."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return Path(buf.value).name
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------------------------------- merging
def merge(installed: list[dict], seen: list[str], installed_only: bool = False) -> list[dict]:
    """Installed apps plus names ActivityWatch has recorded, one row per name, sorted by label.

    With installed_only, a recorded name that matches no installed app (loginwindow, helper processes) is left out;
    the ones that do match are still marked as seen.
    """
    from llm.privacy import norm
    rows: dict[str, dict] = {}
    for r in installed:
        k = norm(r["value"]) or norm(r["label"])
        if k and k not in rows:
            rows[k] = {"label": r["label"], "value": r["value"], "installed": True, "seen": False}
    for name in seen:
        k = norm(name)
        if not k:
            continue
        hit = rows.get(k) or next((r for r in rows.values() if norm(r["label"]) == k), None)
        if hit:
            hit["seen"] = True
        elif not installed_only:
            rows[k] = {"label": name, "value": name, "installed": False, "seen": True}
    return sorted(rows.values(), key=lambda r: r["label"].lower())
