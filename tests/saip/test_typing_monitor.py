"""The typed-text monitor, driven with fake keys so it needs neither `pynput` nor a real keyboard."""
import pytest

from saip import typing_monitor
from saip.typing_monitor import TypingMonitor


class Key:
    """Stands in for a pynput key: printable keys have `char`, special keys have `name`."""
    def __init__(self, char=None, name=None):
        self.char, self.name = char, name


def letters(s):
    return [Key(char=c) for c in s]


def monitor(app="Code", **kw):
    state = {"app": app}
    m = TypingMonitor(foreground=lambda: state["app"], **kw)
    m.state = state
    m.refresh_allowed()
    return m


def type_(m, keys):
    for k in keys:
        m.handle_key(k)


def test_words_are_built_from_letters_and_spaces():
    m = monitor()
    type_(m, letters("hello") + [Key(name="space")] + letters("world"))
    assert m.get_current_text() == "hello world"


def test_backspace_removes_and_escape_clears():
    m = monitor()
    type_(m, letters("helloo") + [Key(name="backspace")])
    assert m.get_current_text() == "hello"
    type_(m, [Key(name="esc")])
    assert m.get_current_text() == ""


def test_shortcuts_are_not_typing():
    m = monitor()
    type_(m, [Key(name="cmd"), Key(char="c")])
    m._on_release(Key(name="cmd"))
    type_(m, letters("ok"))
    assert m.get_current_text() == "ok"


def test_only_the_newest_characters_are_handed_out():
    m = monitor(clip_chars=5)
    type_(m, letters("abcdefghij"))
    assert m.get_current_text() == "fghij"


def test_any_app_that_is_not_blocked_is_recorded():
    m = monitor(app="Finder")
    type_(m, letters("notes"))
    assert m.get_current_text() == "notes"


def test_blocked_apps_are_never_recorded():
    m = monitor(app="Google Chrome", blocked=("Chrome",))
    type_(m, letters("secret"))
    assert m.get_current_text() == ""


def test_entering_a_blocked_app_empties_the_buffer():
    m = monitor(blocked=("1Password",))
    type_(m, letters("draft"))
    m.state["app"] = "1Password 7"
    assert m.refresh_allowed() is False
    assert m.get_current_text() == ""
    m.state["app"] = "Code"
    m.refresh_allowed()
    assert m.get_current_text() == ""


def test_key_presses_are_counted_even_where_nothing_is_kept():
    m = monitor(app="Signal", blocked=("Signal",))
    type_(m, letters("abc"))
    assert m.get_current_text() == "" and m.presses == 3


def test_unknown_foreground_app_means_not_recording():
    m = monitor(app=None)
    type_(m, letters("x"))
    assert m.get_current_text() == ""


def test_f1_stops_the_listener_and_drops_the_text():
    m = monitor()
    m._running = True
    type_(m, letters("abc"))
    assert m.handle_key(Key(name="f1")) is False
    assert not m.running and m.get_current_text() == ""


def test_start_without_pynput_explains_how_to_install(monkeypatch):
    monkeypatch.setattr(typing_monitor, "keyboard", None)
    with pytest.raises(RuntimeError, match="pynput"):
        TypingMonitor().start()


def test_nothing_is_printed_unless_echo_is_on(capsys):
    m = monitor()
    type_(m, letters("quiet words"))
    assert capsys.readouterr().out == ""
    m._emit_typed_text()
    assert "typed_text" in capsys.readouterr().out
