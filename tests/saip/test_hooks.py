from pathlib import Path

import pytest

from saip.hooks.emit import command_event, emit_command, main
from saip.hooks.shells import BEGIN, END, has_block, install_block, remove_block, snippet, strip_block


# ---- the event the hook sends --------------------------------------------------------------------

def test_failure_event_has_a_stable_signature_and_no_command_text():
    a = command_event(2, "pytest", 100.0, 103.5)
    b = command_event(2, "pytest", 500.0, 501.0)
    c = command_event(2, "cargo", 500.0, 501.0)
    assert a["data"]["error_sig"] == b["data"]["error_sig"] != c["data"]["error_sig"]
    assert a["duration"] == pytest.approx(3.5)
    assert set(a["data"]) == {"exit_code", "cmd_kind", "error_sig", "error_type"}
    assert "pytest" not in str(a)                       # the program name is hashed, never stored


def test_success_and_ctrl_c_are_not_failures():
    assert command_event(0, "ls", 1.0, 2.0)["data"]["error_sig"] is None
    assert command_event(130, "sleep", 1.0, 2.0)["data"]["error_sig"] is None


def test_git_is_labelled_like_the_synthetic_data():
    assert command_event(0, "git", 1.0, 2.0)["data"]["cmd_kind"] == "git"
    assert command_event(0, "make", 1.0, 2.0)["data"]["cmd_kind"] == "shell"


class FakeAW:
    def __init__(self):
        self.made, self.sent = [], []

    def ensure_bucket(self, bid, btype, client, host=None):
        self.made.append((bid, btype))

    def insert(self, bid, events):
        self.sent.append((bid, events))


def test_emit_creates_the_bucket_the_loader_matches_by_type():
    aw = FakeAW()
    assert emit_command(aw, command_event(1, "x", 1.0, 2.0))
    assert aw.made[0][1] == "app.terminal.command" and aw.sent[0][0].startswith("aw-watcher-terminal_")


def test_emitter_never_raises_even_with_bad_arguments_or_no_server(capsys):
    assert main(["command"]) == 0                                              # missing --exit-code
    assert main(["command", "--exit-code", "1", "--url", "http://127.0.0.1:9"]) == 0   # nothing listening


# ---- the installed block -------------------------------------------------------------------------

@pytest.mark.parametrize("shell", ["zsh", "bash", "powershell"])
def test_snippets_call_the_emitter_with_the_given_python(shell):
    text = snippet(shell, "/opt/venv/bin/python")
    assert text.startswith(BEGIN) and text.rstrip().endswith(END)
    assert "/opt/venv/bin/python" in text and "saip.hooks" in text and "--exit-code" in text


def test_unknown_shell_is_refused():
    with pytest.raises(ValueError, match="fish"):
        snippet("fish", "py")


def test_install_adds_updates_and_is_idempotent(tmp_path):
    rc = tmp_path / ".zshrc"
    rc.write_text("export A=1\nalias ll='ls -l'\n")
    assert install_block(rc, "zsh", "/py1") == "added"
    assert install_block(rc, "zsh", "/py1") == "unchanged"
    assert install_block(rc, "zsh", "/py2") == "updated"
    text = rc.read_text()
    assert text.count(BEGIN) == 1 and "/py2" in text and "/py1" not in text
    assert text.startswith("export A=1\nalias ll='ls -l'\n")


def test_uninstall_restores_the_file_exactly(tmp_path):
    original = "export A=1\n\n# my stuff\nalias g=git\n"
    rc = tmp_path / ".bashrc"
    rc.write_text(original)
    install_block(rc, "bash", "/py")
    assert has_block(rc.read_text())
    assert remove_block(rc) is True
    assert rc.read_text() == original
    assert remove_block(rc) is False


def test_install_creates_a_missing_profile_and_uninstall_leaves_it_empty(tmp_path):
    rc = tmp_path / "sub" / "profile.ps1"
    assert install_block(rc, "powershell", "C:/py.exe") == "added"
    remove_block(rc)
    assert rc.read_text() == ""


def test_user_content_after_the_block_is_preserved(tmp_path):
    rc = tmp_path / ".zshrc"
    install_block(rc, "zsh", "/py")
    rc.write_text(rc.read_text() + "export LATER=1\n")
    assert strip_block(rc.read_text()) == "export LATER=1\n"
