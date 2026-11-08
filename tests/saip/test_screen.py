from saip import screen
from saip.screen import SEP, Screen, is_local, parse, read_screen, redact, window_text

PAGE = "The results section argues that the gate lowers false alarms, but " * 3


class FakePlat:
    def __init__(self, raw):
        self.raw, self.calls = raw, 0

    def read_screen(self):
        self.calls += 1
        return self.raw


def out(app, cur, text):
    return SEP.join([app, str(cur), text])


def test_only_local_endpoints_are_allowed():
    assert is_local("http://localhost:11434/v1") and is_local("http://127.0.0.1:8000/v1")
    assert not is_local("https://api.openai.com/v1") and not is_local("http://192.168.1.5:11434/v1")
    assert not is_local("")


def test_a_remote_endpoint_never_triggers_a_read():
    p = FakePlat(out("Word", 0, PAGE))
    assert read_screen(p, "https://api.example.com/v1") is None and p.calls == 0


def test_text_is_read_for_a_local_endpoint():
    s = read_screen(FakePlat(out("Word", 10, PAGE)), "http://localhost:11434/v1")
    assert isinstance(s, Screen) and s.app == "Word" and "gate lowers" in s.text


def test_private_apps_and_blank_pages_are_skipped():
    assert parse(out("Mail", 0, PAGE), ("mail",), 1200) is None
    assert parse(out("Word", 0, "hi"), (), 1200) is None
    assert parse("garbage", (), 1200) is None


def test_a_password_field_returns_nothing():
    assert parse(out("Safari", 0, ""), (), 1200) is None


def test_window_is_centred_on_the_cursor_and_terminals_keep_the_tail():
    text = "".join(f"{i:04d} " for i in range(1000))
    mid = window_text(text, 2500, 200, tail=False)
    assert len(mid) <= 200 and "0500" in mid
    assert window_text(text, 2500, 200, tail=True).endswith("0999")
    assert window_text("short", 0, 200, False) == "short"


def test_terminal_apps_use_the_tail():
    text = "".join(f"line{i:04d}\n" for i in range(500))
    s = parse(out("Terminal", 10, text), (), 100)
    assert s and s.text.endswith("line0499")


def test_secrets_are_masked():
    t = "key sk-abcdefghijklmnop1234 and AKIAABCDEFGHIJKLMNOP and password: hunter2 done"
    r = redact(t)
    assert "sk-abc" not in r and "AKIA" not in r and "hunter2" not in r and "[hidden]" in r


def test_copy_without_a_tool_returns_false(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert screen.copy_to_clipboard("x") is False


def test_file_paths_are_not_mistaken_for_secrets():
    path = "~/Developer/CODING/COURSEWORK/CSE576_Topics_In_NLP/SAIP/src/llm/proposal_generation_module.py"
    assert redact(path) == path
    assert "[hidden]" in redact("token " + "aB3" * 15)
