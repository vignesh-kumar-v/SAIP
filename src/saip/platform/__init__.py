"""Pick the adapter for the operating system we are running on."""
from __future__ import annotations

import sys

from .base import Platform
from .linux import Linux
from .macos import MacOS
from .windows import Windows


def current() -> Platform:
    if sys.platform == "darwin":
        return MacOS()
    if sys.platform.startswith("win"):
        return Windows()
    return Linux()


__all__ = ["Platform", "MacOS", "Windows", "Linux", "current"]
