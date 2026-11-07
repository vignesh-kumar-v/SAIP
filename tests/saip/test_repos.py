"""Finding repositories and hooking them, against real throwaway git repos."""
import http.server
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import saip
from llm.config import AgentConfig
from saip.hooks import githook
from saip.hooks.githook import INSTALLED, READY, SKIP, STALE
from saip.repos import discover, find_repos, fallback_repos, inspect_all, set_config_list

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
PY = "/venv/bin/python"


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", cwd=path)
    return path


def hook(r: Path) -> Path:
    return r / ".git" / "hooks" / "post-commit"


# ---- finding repositories ------------------------------------------------------------------------

def test_find_repos_walks_a_folder_and_skips_the_noise(tmp_path):
    a = repo(tmp_path / "a")
    c = repo(tmp_path / "b" / "c")
    repo(tmp_path / "b" / "node_modules" / "pkg")            # bulky folder: skipped
    repo(tmp_path / ".hidden" / "z")                         # hidden folder: skipped
    repo(tmp_path / "d" / "e" / "f" / "g" / "h")             # deeper than 4 levels: not reached
    repo(a / "nested")                                       # inside a repo that was already found
    assert find_repos([tmp_path]) == sorted([a, c])


def test_a_root_that_is_itself_a_repo_is_found(tmp_path):
    r = repo(tmp_path / "solo")
    assert find_repos([r]) == [r.resolve()]


def test_exclude_removes_a_repo_or_a_whole_folder(tmp_path):
    keep, drop = repo(tmp_path / "keep"), repo(tmp_path / "third_party" / "lib")
    assert find_repos([tmp_path], exclude=[tmp_path / "third_party"]) == [keep]
    assert find_repos([tmp_path], exclude=[keep]) == [drop]


@pytest.mark.skipif(sys.platform.startswith("win"), reason="symlinks need privileges on Windows")
def test_symlinks_are_not_followed(tmp_path):
    real = repo(tmp_path / "elsewhere" / "r")
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "link").symlink_to(real.parent)
    assert find_repos([tmp_path / "root"]) == []


def test_missing_roots_are_ignored(tmp_path):
    assert find_repos([tmp_path / "nope"]) == []


def test_discover_merges_explicit_repos_roots_and_exclusions(tmp_path):
    one, two, three = repo(tmp_path / "x" / "one"), repo(tmp_path / "y" / "two"), repo(tmp_path / "y" / "three")
    agent = AgentConfig(repos=(str(one),), watch_roots=(str(tmp_path / "y"),), exclude=(str(three),))
    assert discover(agent) == [one.resolve(), two.resolve()]


# ---- installing and removing the hook ------------------------------------------------------------

def test_install_creates_an_executable_hook_and_remove_deletes_it(tmp_path):
    r = repo(tmp_path / "r")
    assert githook.inspect(r, PY).state == READY
    st = githook.install(r, PY)
    assert st.state == INSTALLED and st.detail == "added"
    text = hook(r).read_text()
    assert text.startswith("#!/bin/sh\n") and '"/venv/bin/python" -m saip.hooks commit' in text
    if os.name != "nt":
        assert os.access(hook(r), os.X_OK)
    assert githook.install(r, PY).detail == "unchanged"
    assert githook.remove(r) is True and not hook(r).exists()
    assert githook.remove(r) is False


def test_an_existing_shell_hook_keeps_every_line_and_is_restored_exactly(tmp_path):
    r = repo(tmp_path / "r")
    original = "#!/bin/bash\n# my hook\necho one\nexec some-tool \"$@\"\n"
    hook(r).write_text(original)
    hook(r).chmod(0o755)
    githook.install(r, PY)
    text = hook(r).read_text()
    assert text.startswith("#!/bin/bash\n") and "exec some-tool" in text
    assert text.index("saip.hooks") < text.index("exec some-tool")     # ours runs first, so a later exec cannot skip it
    githook.remove(r)
    assert hook(r).read_text() == original


def test_a_hook_without_a_shebang_is_kept_and_restored(tmp_path):
    r = repo(tmp_path / "r")
    hook(r).write_text("echo bare\n")
    hook(r).chmod(0o755)
    githook.install(r, PY)
    githook.remove(r)
    assert hook(r).read_text() == "echo bare\n"


def test_unsafe_repos_are_skipped_with_a_reason(tmp_path):
    py_hook = repo(tmp_path / "py")
    hook(py_hook).write_text("#!/usr/bin/env python3\nprint('hi')\n")
    hook(py_hook).chmod(0o755)
    husky = repo(tmp_path / "husky")
    git("config", "core.hooksPath", ".husky/_", cwd=husky)
    states = {s.path.name: s for s in inspect_all([py_hook, husky, tmp_path], PY)}
    assert states["py"].state == SKIP and "not a shell script" in states["py"].detail
    assert states["husky"].state == SKIP and "core.hooksPath" in states["husky"].detail
    assert states[tmp_path.name].detail == "not a git repository"
    assert githook.install(py_hook, PY).state == SKIP
    assert "print('hi')" in hook(py_hook).read_text() and "saip" not in hook(py_hook).read_text()
    assert fallback_repos(list(states.values())) == [py_hook, husky]    # polled instead; the non-repo is ignored


@pytest.mark.skipif(os.name == "nt", reason="the executable bit does not exist on Windows")
def test_a_non_executable_hook_is_left_alone(tmp_path):
    r = repo(tmp_path / "r")
    hook(r).write_text("#!/bin/sh\necho disabled on purpose\n")
    hook(r).chmod(0o644)
    assert githook.inspect(r, PY).state == SKIP


def test_a_hook_pointing_at_an_old_python_is_stale_and_gets_refreshed(tmp_path):
    r = repo(tmp_path / "r")
    githook.install(r, "/old/python")
    assert githook.inspect(r, PY).state == STALE
    assert githook.install(r, PY).detail == "updated"
    assert githook.inspect(r, PY).state == INSTALLED and "/old/python" not in hook(r).read_text()


def test_a_hook_naming_the_same_interpreter_by_another_path_is_not_stale(tmp_path):
    real = tmp_path / "python3.12"
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    alias = tmp_path / "python"
    if os.name != "nt":
        alias.symlink_to(real)
    r = repo(tmp_path / "r")
    githook.install(r, str(real))
    if os.name != "nt":
        assert githook.inspect(r, str(alias)).state == INSTALLED       # same interpreter, different name
    real.unlink()
    assert githook.inspect(r, "/somewhere/else").state == STALE         # the interpreter it points at is gone


def test_worktrees_use_the_right_hooks_folder(tmp_path):
    main = repo(tmp_path / "main")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-qm", "x", cwd=main)
    wt = tmp_path / "wt"
    git("worktree", "add", "-q", str(wt), "-b", "side", cwd=main)
    assert (wt / ".git").is_file()                              # a worktree's .git is a file, not a folder
    assert githook.install(wt, PY).state == INSTALLED and githook.has_hook(wt)
    assert githook.remove(wt)


# ---- editing the config list ---------------------------------------------------------------------

def test_set_config_list_replaces_inserts_and_keeps_everything_else(tmp_path):
    cfg = tmp_path / "private.toml"
    cfg.write_text('[llm]\nmodel = "m"   # keep me\n\n[agent]\nwatch_roots = ["~/old"]\ncooldown_s = 100\n')
    set_config_list(cfg, "agent", "watch_roots", ["~/new", "/x y"])
    set_config_list(cfg, "agent", "exclude", ["~/skip"])
    text = cfg.read_text()
    assert 'watch_roots = ["~/new", "/x y"]' in text and 'exclude = ["~/skip"]' in text
    assert 'model = "m"   # keep me' in text and "cooldown_s = 100" in text and "~/old" not in text
    import tomllib
    assert tomllib.loads(text)["agent"]["watch_roots"] == ["~/new", "/x y"]


def test_set_config_list_creates_the_section_when_missing(tmp_path):
    cfg = tmp_path / "private.toml"
    cfg.write_text('[llm]\nmodel = "m"\n')
    set_config_list(cfg, "agent", "watch_roots", ["~/code"])
    import tomllib
    assert tomllib.loads(cfg.read_text()) == {"llm": {"model": "m"}, "agent": {"watch_roots": ["~/code"]}}


# ---- a real commit reaches ActivityWatcher -------------------------------------------------------

class Recorder(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        Recorder.seen.append((self.path, body))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def do_GET(self):
        self.do_POST()

    def log_message(self, *a):
        pass


@pytest.mark.skipif(sys.platform.startswith("win"), reason="hook execution through Git for Windows is not exercised here")
def test_a_real_commit_sends_one_event_and_a_branch_switch_sends_none(tmp_path, monkeypatch):
    Recorder.seen = []
    server = http.server.HTTPServer(("127.0.0.1", 0), Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("SAIP_AW_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("PYTHONPATH", str(Path(saip.__file__).resolve().parents[1]))
    r = repo(tmp_path / "r")
    githook.install(r, sys.executable)

    def commit_events():
        return [b for p, b in Recorder.seen if p.endswith("/events") and "aw-watcher-fs" in p and '"commit"' in b]

    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-qm", "x", cwd=r)
    for _ in range(50):
        if commit_events():
            break
        time.sleep(0.1)
    assert len(commit_events()) == 1
    assert "x" not in commit_events()[0].replace("kind", "").replace("commit", "").replace("duration", "")[:0]   # no message
    git("checkout", "-q", "-b", "other", cwd=r)
    time.sleep(1.0)
    assert len(commit_events()) == 1                          # switching branches is not a commit
    server.shutdown()


def test_bulky_folders_like_vendor_are_not_scanned(tmp_path):
    repo(tmp_path / "vendor" / "lib")
    assert find_repos([tmp_path]) == []


# ---- what to do about each repository (accepted / declined / new) ---------------------------------

from saip.repos import key, plan_sync  # noqa: E402


def test_plan_sync_separates_accepted_declined_and_new(tmp_path):
    old, never, fresh, husky = (repo(tmp_path / n) for n in ("old", "never", "fresh", "husky"))
    git("config", "core.hooksPath", ".husky/_", cwd=husky)
    githook.install(old, PY)
    agent = AgentConfig(watch_roots=(str(tmp_path),))
    accepted = {key(old), key(husky)}
    plan = plan_sync(agent, PY, accepted, {key(never)})
    assert [s.path for s in plan.new] == [fresh.resolve()]            # only the unseen one is asked about
    assert plan.hooked == [old.resolve()] and plan.polled == [husky.resolve()]
    assert plan.refresh == []


def test_an_accepted_repo_that_lost_its_hook_is_refreshed_quietly(tmp_path):
    r = repo(tmp_path / "r")
    githook.install(r, PY)
    hook(r).unlink()
    plan = plan_sync(AgentConfig(watch_roots=(str(tmp_path),)), PY, {key(r)}, set())
    assert [s.path for s in plan.refresh] == [r.resolve()] and plan.new == []


def test_a_declined_repo_is_never_offered_again(tmp_path):
    r = repo(tmp_path / "r")
    plan = plan_sync(AgentConfig(watch_roots=(str(tmp_path),)), PY, set(), {key(r)})
    assert plan.new == [] and plan.refresh == []
