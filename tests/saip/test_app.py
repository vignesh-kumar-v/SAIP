"""Setup, doctor, the live loop's pieces, and the platform adapters. Nothing here touches the real shell,
the real ActivityWatcher or the real config: every dependency is a fake and every path is a tmp_path."""
import json
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path

import pytest

import llm.config as llm_config
import saip.setup as setup_mod
from llm.assess import Assessment
from llm.config import AgentConfig, GateParams
from llm.gate import decide, threshold
from saip import platform as platform_pkg
from saip.agent import NotReady, Orchestrator, Tick, fetch_trace, score_now
from saip.aw import AWError
from saip.hooks import githook
from saip.hooks.shells import BEGIN, has_block
from saip.platform.base import Platform
from saip.platform.linux import Linux
from saip.platform.macos import MacOS
from saip.platform.windows import Windows
from saip.setup import Result, check_data, doctor, run_setup, uninstall
from saip.watchers import GitWatcher
from synth.generator import generate_day
from synth.writer import write_day

G = GateParams()


class FakeAW:
    """Serves buckets and events from a dict; records what is written."""

    def __init__(self, buckets=None, events=None, up=True):
        self._buckets, self._events, self.up = buckets or {}, events or {}, up
        self.inserted, self.created, self.deleted = [], [], []

    def ping(self):
        return self.up

    def buckets(self):
        if not self.up:
            raise AWError("down")
        return self._buckets

    def events(self, bid, start, end, limit=-1):
        out = []
        for e in self._events.get(bid, []):
            t = datetime.fromisoformat(e["timestamp"]).timestamp()
            if t + e["duration"] >= start and t <= end:
                out.append(e)
        return out[:limit] if limit > 0 else out

    def ensure_bucket(self, bid, btype, client, host=None):
        self.created.append((bid, btype))

    def insert(self, bid, events):
        self.inserted.append((bid, events))

    def delete_bucket(self, bid):
        self.deleted.append(bid)


class FakePlatform(Platform):
    name, level = "FakeOS", "full"

    def __init__(self, profile, installed=True, install_ok=True, shell="zsh"):
        self.profile, self.installed, self.install_ok, self._shell = profile, installed, install_ok, shell
        self.started = self.installs = 0

    def shell(self):
        return self._shell

    def shell_profile(self, shell):
        return self.profile if shell in ("zsh", "bash", "powershell") else None

    def activitywatcher_installed(self):
        return self.installed

    def install_activitywatch(self):
        self.installs += 1
        if self.install_ok:
            self.installed = True
        return self.install_ok, "boom" if not self.install_ok else "ok"

    def start_activitywatch(self):
        self.started += 1
        return True

    def uninstall_activitywatch(self):
        self.uninstalled = getattr(self, "uninstalled", 0) + 1
        return True, "ok"

    def permission_hint(self):
        return "grant it in Settings"

    def activitywatch_data_dir(self):
        return Path("/fake/activitywatch-data")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point every per-user path at tmp_path and start from an empty directory."""
    for mod in (llm_config, setup_mod):
        monkeypatch.setattr(mod, "user_config_dir", lambda: tmp_path / "cfg")
    monkeypatch.chdir(tmp_path)
    for v in ("SAIP_CONFIG", "SAIP_LLM_BASE_URL", "SAIP_LLM_MODEL", "SAIP_LLM_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    return tmp_path


def quiet(*_):
    pass


def make_repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)


def tree_snapshot(root):
    """Every file under root (path, size, bytes), so 'exactly as before' can be asserted."""
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# ---- setup ---------------------------------------------------------------------------------------

def test_first_run_sets_everything_up_and_a_second_run_changes_nothing(sandbox):
    profile = sandbox / ".zshrc"
    profile.write_text("export KEEP=1\n")
    plat, aw = FakePlatform(profile), FakeAW()
    first = {r.name: r.status for r in run_setup(plat, aw, yes=True, out=quiet, python="/venv/py")}
    assert first["ActivityWatcher"] == "ok" and first["config"] == "changed" and first["terminal hook"] == "changed"
    assert has_block(profile.read_text()) and "/venv/py" in profile.read_text()
    assert (sandbox / "cfg" / "private.toml").exists() and setup_mod.is_set_up()
    before = profile.read_text()
    second = {r.name: r.status for r in run_setup(plat, aw, yes=True, out=quiet, python="/venv/py")}
    assert second["config"] == "ok" and second["terminal hook"] == "ok" and profile.read_text() == before


def test_setup_asks_before_touching_the_shell_profile(sandbox):
    profile = sandbox / ".zshrc"
    answers = iter(["", "", "none"])                  # endpoint url, model, projects folder (skipped)
    results = {r.name: r for r in run_setup(FakePlatform(profile), FakeAW(),
                                            ask=lambda q: "n" if "[y/N]" in q else next(answers), out=quiet)}
    assert results["terminal hook"].status == "skipped" and not profile.exists()


def test_setup_writes_the_endpoint_it_was_given_and_it_loads_back(sandbox):
    answers = iter(["http://gpu:8000/v1", "my-model", "none"])
    run_setup(FakePlatform(sandbox / ".zshrc"), FakeAW(), yes=False, ask=lambda q: "y" if "[y/N]" in q else next(answers),
              out=quiet)
    s = llm_config.load_settings()
    assert s.llm.base_url == "http://gpu:8000/v1" and s.llm.model == "my-model"


def test_setup_starts_an_installed_but_stopped_activitywatcher(sandbox, monkeypatch):
    monkeypatch.setattr(setup_mod, "_wait_for", lambda check, seconds, step=1.0: True)
    plat = FakePlatform(sandbox / ".zshrc")
    r = {x.name: x for x in run_setup(plat, FakeAW(up=False), yes=True, out=quiet)}
    assert plat.started == 1 and r["ActivityWatcher"].status == "changed"


def test_setup_installs_activitywatch_only_when_confirmed(sandbox, monkeypatch):
    monkeypatch.setattr(setup_mod, "_wait_for", lambda check, seconds, step=1.0: False)
    declined = FakePlatform(sandbox / "a", installed=False)
    r = {x.name: x for x in run_setup(declined, FakeAW(up=False), ask=lambda q: "n", out=quiet)}
    assert declined.installs == 0 and r["ActivityWatcher"].status == "needs-you"
    accepted = FakePlatform(sandbox / "b", installed=False)
    run_setup(accepted, FakeAW(up=False), yes=True, out=quiet)
    assert accepted.installs == 1


def test_a_failed_install_is_reported_not_hidden(sandbox):
    plat = FakePlatform(sandbox / ".zshrc", installed=False, install_ok=False)
    r = {x.name: x for x in run_setup(plat, FakeAW(up=False), yes=True, out=quiet)}
    assert r["ActivityWatcher"].status == "failed" and "boom" in r["ActivityWatcher"].detail


def test_unsupported_shell_asks_the_user_instead_of_failing(sandbox):
    r = {x.name: x for x in run_setup(FakePlatform(None, shell="fish"), FakeAW(), yes=True, out=quiet)}
    assert r["terminal hook"].status == "needs-you" and "fish" in r["terminal hook"].detail


def test_uninstall_with_nothing_installed_says_so(sandbox):
    out = []
    assert uninstall(FakePlatform(sandbox / ".zshrc"), FakeAW(), yes=True, out=out.append) == []
    assert "Nothing to undo" in out[0]


def test_declining_the_uninstall_prompt_changes_nothing(sandbox):
    profile = sandbox / ".zshrc"
    plat = FakePlatform(profile)
    run_setup(plat, FakeAW(), yes=True, out=quiet)
    before = profile.read_text()
    assert uninstall(plat, FakeAW(), ask=lambda q: "n", out=quiet) == []
    assert profile.read_text() == before and setup_mod.is_set_up()


def test_uninstall_keeps_a_config_that_existed_before_setup(sandbox):
    mine = sandbox / "private.toml"
    mine.write_text('[llm]\nmodel = "mine"\n')
    plat = FakePlatform(sandbox / ".zshrc")
    run_setup(plat, FakeAW(), yes=True, out=quiet)
    uninstall(plat, FakeAW(), yes=True, out=quiet)
    assert mine.read_text() == '[llm]\nmodel = "mine"\n'


def test_keep_config_flag_keeps_what_setup_wrote(sandbox):
    plat = FakePlatform(sandbox / ".zshrc")
    run_setup(plat, FakeAW(), yes=True, out=quiet)
    uninstall(plat, FakeAW(), yes=True, keep_config=True, out=quiet)
    assert (sandbox / "cfg" / "private.toml").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_uninstall_puts_everything_back_as_if_saip_never_existed(sandbox, monkeypatch):
    monkeypatch.setattr(setup_mod, "_wait_for", lambda check, seconds, step=1.0: True)
    profile = sandbox / ".zshrc"                                       # does not exist yet: setup will create it
    code = sandbox / "code"
    plain, mine = code / "plain", code / "has-hook"
    for r in (plain, mine):
        make_repo(r)
    user_hook = "#!/bin/sh\necho my own hook\n"
    (mine / ".git" / "hooks" / "post-commit").write_text(user_hook)
    (mine / ".git" / "hooks" / "post-commit").chmod(0o755)
    snapshot = tree_snapshot(code)

    aw = FakeAW({"aw-watcher-terminal_host": {"type": "app.terminal.command"},
                 "aw-watcher-window_host": {"type": "currentwindow"}}, up=False)
    monkeypatch.setattr(setup_mod, "_wait_for", lambda check, seconds, step=1.0: setattr(aw, "up", True) or True)
    plat = FakePlatform(profile, installed=False)
    run_setup(plat, aw, yes=True, roots=[str(code)], python="/venv/py", out=quiet)
    assert plat.installs == 1 and profile.exists() and (sandbox / "cfg" / "private.toml").exists()
    assert githook.has_hook(plain) and githook.has_hook(mine)
    assert "my own hook" in (mine / ".git" / "hooks" / "post-commit").read_text()

    out = []
    results = uninstall(plat, aw, yes=True, out=out.append)
    assert all(r.status == "changed" for r in results), [r.line() for r in results]
    assert not profile.exists()                                         # setup created it, so it is gone
    assert not (sandbox / "cfg").exists()                               # config, marker and folder all gone
    assert tree_snapshot(code) == snapshot                              # every repo is byte-for-byte as before
    assert (mine / ".git" / "hooks" / "post-commit").read_text() == user_hook
    assert aw.deleted == ["aw-watcher-terminal_host"]                   # our bucket only, never the window watcher
    assert plat.uninstalled == 1                                        # setup installed ActivityWatcher, so it goes
    assert any("recorded activity" in r.detail for r in results)       # but the user's recorded data is left alone


def test_the_saip_package_itself_is_removed_last_when_asked(sandbox):
    plat = FakePlatform(sandbox / ".zshrc")
    run_setup(plat, FakeAW(), yes=True, out=quiet)
    calls = []
    results = uninstall(plat, FakeAW(), yes=True, out=quiet, pip=lambda: (calls.append(1), (True, ""))[1])
    assert calls and results[-1].name == "saip command"


# ---- doctor / data checks ------------------------------------------------------------------------

NOW = 1_800_000_000.0


def ev(t, data, dur=5.0):
    return {"timestamp": datetime.fromtimestamp(t).astimezone().isoformat(), "duration": dur, "data": data}


def healthy_aw(title="main.py", presses=3):
    b = {"w": {"type": "currentwindow"}, "a": {"type": "afkstatus"}, "i": {"type": "os.hid.input"}}
    e = {"w": [ev(NOW - 30, {"app": "Code", "title": title})], "a": [ev(NOW - 30, {"status": "not-afk"})],
         "i": [ev(NOW - 30, dict(presses=presses, clicks=0, scrollX=0, scrollY=0))]}
    return FakeAW(b, e)


def statuses(rs):
    return {r.name: r.status for r in rs}


def test_check_data_healthy():
    assert set(statuses(check_data(FakePlatform(None), healthy_aw(), NOW)).values()) == {"ok"}


def test_missing_window_titles_points_at_the_permission():
    rs = {r.name: r for r in check_data(FakePlatform(None), healthy_aw(title=""), NOW)}
    assert rs["window watcher"].status == "needs-you" and "grant it" in rs["window watcher"].detail


def test_all_zero_input_points_at_the_permission():
    rs = {r.name: r for r in check_data(FakePlatform(None), healthy_aw(presses=0), NOW)}
    assert rs["input watcher"].status == "needs-you"


def test_missing_watchers_are_failures():
    rs = statuses(check_data(FakePlatform(None), FakeAW({}, {}), NOW))
    assert rs["window watcher"] == "failed" and rs["input watcher"] == "failed" and rs["AFK watcher"] == "failed"


def test_unreachable_activitywatcher_is_one_clear_failure():
    assert statuses(check_data(FakePlatform(None), FakeAW(up=False), NOW)) == {"data": "failed"}


def test_doctor_reports_hook_state_without_changing_anything(sandbox):
    profile = sandbox / ".zshrc"
    out = []
    rs = doctor(FakePlatform(profile), healthy_aw(), out=out.append)
    assert statuses(rs)["terminal hook"] == "needs-you" and not profile.exists() and out


# ---- the live loop's pieces ----------------------------------------------------------------------

def day_aw(tmp_path):
    write_day(generate_day(1001, date(2026, 11, 4), user=1), tmp_path / "day")
    export = json.loads((tmp_path / "day" / "aw-export.json").read_text())["buckets"]
    buckets = {bid: {"type": b["type"]} for bid, b in export.items()}
    events = {bid: b["events"] for bid, b in export.items()}
    end = max(datetime.fromisoformat(e["timestamp"]).timestamp() + e["duration"] for e in events[next(iter(events))])
    return FakeAW(buckets, events), end


def test_fetch_and_score_a_generated_day_through_the_fake_server(tmp_path):
    aw, end = day_aw(tmp_path)
    mid = end - 3 * 3600
    trace = fetch_trace(aw, mid, 90 * 60)
    assert trace.hooks and trace.streams["window"] and trace.streams["input"]
    tick = score_now(trace, mid)
    assert 0.0 <= tick.stuck_score <= 1.0 and tick.theta_trig in (0.1, 0.19)


def test_fetch_trace_reports_what_is_missing():
    with pytest.raises(NotReady, match="window"):
        fetch_trace(FakeAW({"w": {"type": "currentwindow"}}, {"w": []}), NOW, 600)


def test_stale_data_counts_as_paused():
    aw = healthy_aw()
    aw._events["w"] = [ev(NOW - 3600, {"app": "Code", "title": "x"}, 60.0)]
    aw._events["i"] = [ev(NOW - 3600, dict(presses=2, clicks=0, scrollX=0, scrollY=0))]
    aw._events["a"] = [ev(NOW - 3700, {"status": "not-afk"}, 3600.0)]
    tick = score_now(fetch_trace(aw, NOW - 3000, 7200), NOW)
    assert tick.paused and tick.reason == "stale"


def assessment(action):
    if action == "speak":
        d = decide(0.9, 0.9, 0.99, G)
    elif action == "wait":
        d = decide(0.2, 0.6, threshold(0.2, 0.6, G) - 0.05, G)
    else:
        d = decide(0.5, 0.5, 0.01, G)
    assert d.action == action
    return Assessment(d, 0.5, 0.5, False, 2)


def tick(s=0.6, paused=False, idle=1.0, front=("Code", "a.py"), theta=0.1):
    return Tick(0.0, s, paused, "afk" if paused else None, theta, idle, front)


CFG = AgentConfig()


def make(*actions):
    calls = []
    seq = iter(actions)

    def assess():
        calls.append(1)
        return assessment(next(seq))
    return calls, assess


def test_below_the_trigger_the_llm_is_never_called():
    calls, assess = make("speak")
    assert Orchestrator(CFG).step(0.0, tick(s=0.05), assess) is None and not calls


def test_paused_user_is_never_assessed():
    calls, assess = make("speak")
    assert Orchestrator(CFG).step(0.0, tick(paused=True), assess) is None and not calls


def test_speak_then_cooldown_then_speak_again():
    calls, assess = make("speak", "speak")
    o = Orchestrator(CFG)
    assert o.step(0.0, tick(), assess).kind == "offer"
    assert o.step(100.0, tick(), assess) is None                      # inside the 300 s cooldown
    assert o.step(301.0, tick(), assess).kind == "offer" and len(calls) == 2


def test_assessments_are_rate_limited():
    calls, assess = make("quiet", "quiet", "quiet")
    o = Orchestrator(CFG)
    o.step(0.0, tick(), assess)
    assert o.step(10.0, tick(), assess) is None                       # under assess_interval_s
    assert o.step(31.0, tick(), assess).kind == "quiet" and len(calls) == 2


def test_a_deferred_offer_is_rechecked_at_a_natural_break_and_spoken():
    calls, assess = make("wait", "speak")
    o = Orchestrator(CFG)
    assert o.step(0.0, tick(idle=1.0), assess).kind == "deferred" and o.pending_since == 0.0
    assert o.step(5.0, tick(idle=1.0), assess) is None                # still typing: no re-check, interval not due
    ev = o.step(10.0, tick(idle=6.0), assess)                          # hands off the keyboard: a break
    assert ev.kind == "offer" and o.pending_since is None


def test_a_window_switch_is_also_a_natural_break():
    calls, assess = make("wait", "speak")
    o = Orchestrator(CFG)
    o.step(0.0, tick(front=("Code", "a.py")), assess)
    assert o.step(5.0, tick(front=("Safari", "docs")), assess).kind == "offer"


def test_a_pending_offer_expires():
    calls, assess = make("wait", "quiet")
    o = Orchestrator(CFG)
    o.step(0.0, tick(), assess)
    o.step(700.0, tick(idle=1.0), assess)                              # past pending_expiry_s
    assert o.pending_since is None


def test_a_recheck_that_stays_below_the_bar_keeps_the_offer_pending():
    calls, assess = make("wait", "quiet")
    o = Orchestrator(CFG)
    o.step(0.0, tick(), assess)
    ev = o.step(10.0, tick(idle=6.0), assess)
    assert ev.kind == "recheck-quiet" and o.pending_since == 0.0


# ---- git watcher ---------------------------------------------------------------------------------

def test_git_watcher_posts_one_event_when_head_moves():
    heads = iter(["a1", "a1", "b2", "b2"])
    aw = FakeAW()
    g = GitWatcher(aw, ["/repo"], head=lambda r: next(heads))
    assert [g.poll(100.0), g.poll(105.0), g.poll(110.0), g.poll(115.0)] == [0, 0, 1, 0]
    bid, events = aw.inserted[0]
    assert bid.startswith("aw-watcher-fs_") and events[0]["data"] == {"kind": "commit"}
    assert aw.created[0][1] == "app.fs.activity"


def test_git_watcher_ignores_non_repos_and_survives_a_dead_server():
    g = GitWatcher(FakeAW(), ["/not/a/repo"], head=lambda r: None)
    assert g.poll(1.0) == 0 and g.poll(2.0) == 0

    class Down(FakeAW):
        def ensure_bucket(self, *a, **k):
            raise AWError("down")
    heads = iter(["a", "b"])
    assert GitWatcher(Down(), ["/r"], head=lambda r: next(heads)).poll(1.0) == 0


# ---- platform adapters ---------------------------------------------------------------------------

def test_adapter_is_chosen_by_os(monkeypatch):
    for plat, cls in (("darwin", MacOS), ("win32", Windows), ("linux", Linux)):
        monkeypatch.setattr(platform_pkg.sys, "platform", plat)
        assert isinstance(platform_pkg.current(), cls)


def test_only_macos_is_marked_tested():
    assert (MacOS.level, Windows.level, Linux.level) == ("full", "experimental", "experimental")


def test_shell_profiles_per_os(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("ZDOTDIR", raising=False)
    assert MacOS().shell_profile("zsh") == tmp_path / ".zshrc"
    assert MacOS().shell_profile("bash") == tmp_path / ".bash_profile"
    assert Linux().shell_profile("bash") == tmp_path / ".bashrc"
    assert Linux().shell_profile("fish") is None
    assert Windows().shell() == "powershell"


def test_linux_warns_about_wayland(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert list(Linux().permissions().values()) == [False]
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert list(Linux().permissions().values()) == [True]


def test_notify_quotes_awkward_text(monkeypatch):
    seen = []
    import saip.platform.macos as m
    monkeypatch.setattr(m, "run", lambda cmd, timeout=0: (seen.append(cmd), (0, ""))[1])
    assert MacOS().notify('He said "hi"', "back\\slash")
    assert '\\"hi\\"' in seen[0][-1] and "\\\\slash" in seen[0][-1]


def test_result_line_is_readable():
    assert "[FAILED ] x: y" in Result("x", "failed", "y").line()


# ---- asking about new repositories ---------------------------------------------------------------

from saip.hooks.githook import READY as _READY, SKIP as _SKIP, RepoState  # noqa: E402
from saip.prompts import ADD, LATER, NEVER, RepoPrompter, question  # noqa: E402


class AskPlatform(FakePlatform):
    """A platform whose dialog returns scripted answers."""

    def __init__(self, answers, dialog=True):
        super().__init__(None)
        self.answers, self.dialog, self.asked = list(answers), dialog, []

    def can_ask(self):
        return self.dialog

    def ask(self, title, message, buttons=()):
        self.asked.append((title, message, buttons))
        return self.answers.pop(0)


def state(tmp, state=_READY, detail=""):
    return RepoState(tmp, state, detail)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_answering_add_hooks_the_repo_and_remembers_it(sandbox):
    r = sandbox / "proj"
    make_repo(r)
    seen = []
    p = RepoPrompter(AskPlatform([ADD]), "/venv/py", on_accepted=lambda s, hooked: seen.append(hooked), out=quiet)
    assert p.handle(state(r)) == ADD
    assert githook.has_hook(r) and seen == [True]
    assert str(r.resolve()) in setup_mod.decisions()[0]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_answering_never_leaves_the_repo_alone_and_remembers_the_refusal(sandbox):
    r = sandbox / "proj"
    make_repo(r)
    p = RepoPrompter(AskPlatform([NEVER]), "/venv/py", out=quiet)
    p.handle(state(r))
    assert not githook.has_hook(r) and str(r.resolve()) in setup_mod.decisions()[1]


def test_not_now_or_a_dismissed_dialog_asks_again_only_next_run(sandbox):
    r = sandbox / "proj"
    r.mkdir()
    p = RepoPrompter(AskPlatform([LATER, None]), "/venv/py", out=quiet)
    assert p.handle(state(r)) == LATER
    assert p.handle(state(r)) == LATER                       # a timed-out dialog counts as "not now"
    assert not p.submit(state(r))                            # snoozed for this run
    assert setup_mod.decisions() == (set(), set())           # nothing was recorded either way


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_a_repo_that_cannot_be_hooked_is_offered_as_polled(sandbox):
    r = sandbox / "husky"
    make_repo(r)
    subprocess.run(["git", "config", "core.hooksPath", ".husky/_"], cwd=r, check=True)
    plat = AskPlatform([ADD])
    got = []
    st = githook.inspect(r, "/venv/py")
    RepoPrompter(plat, "/venv/py", on_accepted=lambda s, hooked: got.append(hooked), out=quiet).handle(st)
    assert "checking" in question(st)[1] or "check it" in question(st)[1] or "instead" in question(st)[1]
    assert got == [False] and not githook.has_hook(r)


def test_terminal_fallback_when_the_system_cannot_show_a_dialog(sandbox):
    r = sandbox / "proj"
    r.mkdir()
    replies = iter(["n", "never"])
    p = RepoPrompter(AskPlatform([], dialog=False), "/venv/py", terminal=lambda q: next(replies), out=quiet)
    assert p.ask(state(r)) == LATER and p.ask(state(r)) == NEVER


def test_policy_ignore_never_asks_and_policy_add_never_asks_either(sandbox):
    plat = AskPlatform([])
    ignore = RepoPrompter(plat, "/venv/py", policy="ignore", out=quiet)
    assert not ignore.submit(state(sandbox / "a")) and plat.asked == []
    r = sandbox / "auto"
    if shutil.which("git"):
        make_repo(r)
        RepoPrompter(plat, "/venv/py", policy="add", out=quiet).handle(state(r))
        assert githook.has_hook(r) and plat.asked == []


def test_questions_are_asked_one_at_a_time_in_the_background(sandbox):
    plat = AskPlatform([NEVER, NEVER])
    dirs = []
    for n in ("a", "b"):
        d = sandbox / n
        d.mkdir()
        dirs.append(d)
    p = RepoPrompter(plat, "/venv/py", out=quiet)
    p.start()
    assert all(p.submit(state(d)) for d in dirs) and not p.submit(state(dirs[0]))      # no duplicate while queued
    import time
    for _ in range(50):
        if len(plat.asked) == 2:
            break
        time.sleep(0.05)
    p.stop()
    assert len(plat.asked) == 2


def test_mac_dialog_reads_the_button_and_treats_give_up_as_none(monkeypatch):
    import saip.platform.macos as m
    outputs = iter([(0, "button returned:Add, gave up:false"), (0, "button returned:, gave up:true"), (1, "User canceled.")])
    scripts = []
    monkeypatch.setattr(m, "run", lambda cmd, timeout=0: (scripts.append(cmd[-1]), next(outputs))[1])
    mac = MacOS()
    assert mac.can_ask() and mac.ask("t", 'say "hi"', ("Never", "Not now", "Add")) == "Add"
    assert mac.ask("t", "m") is None and mac.ask("t", "m") is None
    assert 'buttons {"Never", "Not now", "Add"}' in scripts[0] and 'default button "Add"' in scripts[0]
    assert 'say \\"hi\\"' in scripts[0]


def test_setup_records_which_repos_you_accepted_or_refused(sandbox, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    code = sandbox / "code"
    for n in ("a", "b"):
        make_repo(code / n)
    plat = FakePlatform(sandbox / ".zshrc")
    run_setup(plat, FakeAW(), yes=True, roots=[str(code)], python="/venv/py", out=quiet)
    accepted, declined = setup_mod.decisions()
    assert accepted == {str((code / n).resolve()) for n in ("a", "b")} and declined == set()
    # refusing the list at setup is remembered as a refusal
    uninstall(plat, FakeAW(), yes=True, out=quiet)
    answers = iter(["http://h/v1", "m", str(code)])
    run_setup(plat, FakeAW(), ask=lambda q: "n" if "[y/N]" in q else next(answers), out=quiet, python="/venv/py")
    accepted, declined = setup_mod.decisions()
    assert accepted == set() and len(declined) == 2
