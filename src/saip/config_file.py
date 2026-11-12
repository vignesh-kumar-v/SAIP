"""Editing private.toml in place: change one setting, keep every other line and comment as it was.

The dashboard's settings page and the setup wizard write through `write_settings`, which checks every value
against the setting's type (from the dataclasses in llm/config.py), writes it, and loads the whole file again
before keeping the change. The file holds the API key, so on macOS and Linux it is kept readable by you only.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import fields
from pathlib import Path

from llm.config import AgentConfig, GateParams, LLMConfig, load_settings

SECTIONS = {"llm": LLMConfig, "gate": GateParams, "agent": AgentConfig}
SECRET = {("llm", "api_key")}


class SettingError(ValueError):
    pass


def _literal(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_literal(v) for v in value) + "]"
    raise SettingError(f"cannot write {type(value).__name__} to private.toml")


def _comment_at(line: str) -> int | None:
    """Index of a trailing '# comment' outside any string, or None."""
    quote = None
    i = 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#":
            return i
        i += 1
    return None


def set_value(path: Path, section: str, key: str, value) -> None:
    """Set `key = value` inside `[section]`, keeping a trailing comment on that line and everything else."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    new = f"{key} = {_literal(value)}"
    lines = text.split("\n")
    start = next((i for i, l in enumerate(lines) if l.strip() == f"[{section}]"), None)
    if start is None:
        lines += ([""] if text and not text.endswith("\n\n") else []) + [f"[{section}]", new, ""]
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        for i in range(start + 1, end):
            if re.match(rf"\s*{re.escape(key)}\s*=", lines[i]):
                at = _comment_at(lines[i])
                tail = lines[i][at:] if at is not None else ""
                lines[i] = new + ("      " + tail if tail else "")
                break
        else:
            lines.insert(start + 1, new)
    write_private(path, "\n".join(lines))


def write_private(path: Path, text: str) -> None:
    """Write the file so that only you can read it (it may hold an API key)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if not sys.platform.startswith("win"):
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def coerce(section: str, key: str, value):
    """Check a value against the setting's type and return it in that type."""
    cls = SECTIONS.get(section)
    if cls is None:
        raise SettingError(f"unknown section [{section}]")
    field = {f.name: f for f in fields(cls)}.get(key)
    if field is None:
        raise SettingError(f"unknown setting '{key}' in [{section}]")
    default = getattr(cls(), key)
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise SettingError(f"{section}.{key} must be true or false")
        return value
    if isinstance(default, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingError(f"{section}.{key} must be a number")
        if value < 0:
            raise SettingError(f"{section}.{key} cannot be negative")
        return int(value) if isinstance(default, int) and float(value).is_integer() else float(value)
    if isinstance(default, str):
        if not isinstance(value, str):
            raise SettingError(f"{section}.{key} must be text")
        return value.strip()
    if isinstance(default, tuple):
        if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
            raise SettingError(f"{section}.{key} must be a list of names")
        return [v.strip() for v in value if v.strip()]
    raise SettingError(f"{section}.{key} cannot be changed here")


def write_settings(path: Path, changes: dict) -> list[str]:
    """Apply {"llm": {...}, "agent": {...}, "gate": {...}} to the file. Returns the keys changed.

    Nothing is written unless every value is valid, and the file is put back if it no longer loads afterwards.
    """
    plan = []
    for section, table in (changes or {}).items():
        if not isinstance(table, dict):
            raise SettingError(f"[{section}] must be a table")
        for key, value in table.items():
            if (section, key) in SECRET and value is None:
                continue                                    # the page sends null for "leave the key as it is"
            plan.append((section, key, coerce(section, key, value)))
    before = path.read_text(encoding="utf-8") if path.exists() else None
    try:
        for section, key, value in plan:
            set_value(path, section, key, value)
        load_settings(path)
    except Exception as e:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            write_private(path, before)
        raise SettingError(f"settings not saved: {e}") from e
    return [f"{s}.{k}" for s, k, _ in plan]


def public_settings(settings) -> dict:
    """The settings as the page may see them: everything except the API key, which is reported as set or not."""
    out = {}
    for section, cls in SECTIONS.items():
        obj = getattr(settings, {"llm": "llm", "gate": "gate", "agent": "agent"}[section])
        table = {}
        for f in fields(cls):
            v = getattr(obj, f.name)
            if (section, f.name) in SECRET:
                table["api_key_set"] = bool(v)
                continue
            table[f.name] = list(v) if isinstance(v, tuple) else v
        out[section] = table
    out["path"] = str(settings.path) if settings.path else None
    return out
