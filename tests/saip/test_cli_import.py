"""Regressions that CI missed once: the `saip` command must import without the optional `pynput`, and the
shipped example config must load."""
import sys
from pathlib import Path

from llm.config import load_settings

ROOT = Path(__file__).resolve().parents[2]


def test_cli_imports_without_pynput(monkeypatch):
    monkeypatch.setitem(sys.modules, "pynput", None)          # makes `import pynput` raise ImportError
    for name in [n for n in sys.modules if n == "saip.cli" or n == "saip.typing_monitor"]:
        monkeypatch.delitem(sys.modules, name)
    import importlib
    import saip.cli                                            # must not need pynput
    tm = importlib.import_module("saip.typing_monitor")        # imported fresh: the monitor loads, listener missing
    assert tm.keyboard is None


def test_example_config_loads():
    s = load_settings(ROOT / "private.example.toml")
    assert s.agent.monitor_typed_content is False
    assert s.agent.typed_text_max_chars == 200
