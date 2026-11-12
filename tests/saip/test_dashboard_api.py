"""What the new dashboard relies on: settings written in place, app lists, launch at login, stuck episodes and
the API routes the setup wizard and settings page call."""
import json
import os
import plistlib
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from llm.config import load_settings
from saip import autostart, config_file, server
from saip import setup as setup_mod
from saip.events import Bus
from saip.platform import apps
from saip.runtime import Episodes
from test_app import FakeAW, FakePlatform, sandbox  # noqa: F401  (the fixture)

_real_time, _sleep = time.time, time.sleep


# ---------------------------------------------------------------------------------------------------- private.toml
def test_one_setting_changes_and_comments_stay(tmp_path):
    p = tmp_path / "private.toml"
    p.write_text('# top\n[llm]\nbase_url = "x"      # the endpoint\nmodel = "m"\n\n[agent]\nwatch_roots = []\n')
    config_file.set_value(p, "llm", "base_url", "http://h:8000/v1")
    config_file.set_value(p, "agent", "monitor_typed_content", True)
    config_file.set_value(p, "gate", "c0", 1.5)
    text = p.read_text()
    assert 'base_url = "http://h:8000/v1"      # the endpoint' in text and text.startswith("# top")
    s = load_settings(p)
    assert s.llm.base_url == "http://h:8000/v1" and s.agent.monitor_typed_content is True and s.gate.c0 == 1.5
    if not sys.platform.startswith("win"):
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_bad_values_are_refused_and_nothing_is_written(tmp_path):
    p = tmp_path / "private.toml"
    p.write_text('[llm]\nmodel = "m"\n')
    with pytest.raises(config_file.SettingError):
        config_file.write_settings(p, {"llm": {"model": "new", "samples": "ten"}})
    with pytest.raises(config_file.SettingError):
        config_file.write_settings(p, {"llm": {"nonsense": 1}})
    assert p.read_text() == '[llm]\nmodel = "m"\n'
    changed = config_file.write_settings(p, {"llm": {"private_apps": ["Signal", " Mail "], "api_key": None}})
    assert changed == ["llm.private_apps"] and load_settings(p).llm.private_apps == ("Signal", "Mail")


def test_the_page_never_sees_the_api_key(tmp_path):
    p = tmp_path / "private.toml"
    p.write_text('[llm]\napi_key = "sk-secret-value"\n')
    pub = config_file.public_settings(load_settings(p))
    assert "api_key" not in pub["llm"] and pub["llm"]["api_key_set"] is True
    assert "sk-secret" not in json.dumps(pub)


# ---------------------------------------------------------------------------------------------------- installed apps
def test_macos_bundles_are_named_as_macos_reports_them(tmp_path):
    for stem, info in (("Visual Studio Code", {"CFBundleName": "Code"}),
                       ("Google Chrome", {"CFBundleName": "Chrome", "CFBundleDisplayName": "Google Chrome"}),
                       ("Helper", {"CFBundleName": "Helper", "LSUIElement": True})):
        (tmp_path / f"{stem}.app" / "Contents").mkdir(parents=True)
        with (tmp_path / f"{stem}.app" / "Contents" / "Info.plist").open("wb") as f:
            plistlib.dump(info, f)
    (tmp_path / "Microsoft Office" / "Word.app").mkdir(parents=True)
    rows = apps.installed_macos([tmp_path])
    assert {"label": "Visual Studio Code", "value": "Code"} in rows
    assert {"label": "Google Chrome", "value": "Google Chrome"} in rows
    assert {"label": "Word", "value": "Word"} in rows and not any(r["label"] == "Helper" for r in rows)


def test_linux_desktop_entries(tmp_path):
    (tmp_path / "code.desktop").write_text("[Desktop Entry]\nType=Application\nName=Visual Studio Code\n"
                                           "Exec=/usr/share/code/code --unity-launch %F\nStartupWMClass=Code\n")
    (tmp_path / "ff.desktop").write_text("[Desktop Entry]\nName=Firefox\nExec=env MOZ=1 firefox %u\n")
    (tmp_path / "hidden.desktop").write_text("[Desktop Entry]\nName=Hidden\nNoDisplay=true\nExec=x\n")
    (tmp_path / "flat.desktop").write_text("[Desktop Entry]\nName=Signal\nExec=/usr/bin/flatpak run org.signal.Signal\n")
    rows = apps.installed_linux([tmp_path])
    assert {"label": "Visual Studio Code", "value": "Code"} in rows
    assert {"label": "Firefox", "value": "firefox"} in rows
    assert not any(r["label"] == "Hidden" for r in rows) and any(r["label"] == "Signal" for r in rows)


def test_windows_start_menu_and_registry(tmp_path):
    (tmp_path / "Slack").mkdir()
    (tmp_path / "Slack" / "Slack.lnk").write_bytes(b"")
    (tmp_path / "Uninstall Slack.lnk").write_bytes(b"")
    rows = apps.installed_windows([tmp_path], registry=[("Google Chrome", "chrome.exe")])
    assert {"label": "Slack", "value": "Slack"} in rows and {"label": "Google Chrome", "value": "chrome.exe"} in rows
    assert not any("Uninstall" in r["label"] for r in rows)


def test_the_picker_lists_installed_apps_only():
    rows = apps.merge([{"label": "Visual Studio Code", "value": "Code"}], ["Code", "loginwindow"], installed_only=True)
    assert rows == [{"label": "Visual Studio Code", "value": "Code", "installed": True, "seen": True}]


def test_installed_and_seen_apps_merge_by_name():
    rows = apps.merge([{"label": "Visual Studio Code", "value": "Code"}], ["Code", "loginwindow"])
    assert rows == [{"label": "loginwindow", "value": "loginwindow", "installed": False, "seen": True},
                    {"label": "Visual Studio Code", "value": "Code", "installed": True, "seen": True}]


# ---------------------------------------------------------------------------------------------------- launch at login
def test_launch_at_login_files_start_saip_with_its_config(tmp_path):
    cmd = autostart.command(tmp_path / "private.toml", python="/py/bin/python3")
    assert cmd[:5] == ["/py/bin/python3", "-m", "saip", "run", "--no-browser"] or sys.platform.startswith("win")
    assert cmd[-2:] == ["--config", str((tmp_path / "private.toml").resolve())]
    plist = plistlib.loads(autostart.mac_plist(cmd, tmp_path, tmp_path / "saip.log"))
    assert plist["ProgramArguments"] == cmd and plist["RunAtLoad"] is True and plist["Label"] == autostart.LABEL
    desktop = autostart.linux_desktop(["/my py/python", "-m", "saip"])
    assert 'Exec="/my py/python" -m saip' in desktop and "Type=Application" in desktop


@pytest.mark.skipif(sys.platform.startswith("win"), reason="the registry entry is not written in tests")
def test_launch_at_login_on_and_off(sandbox):
    assert not autostart.is_enabled()
    autostart.enable(sandbox / "private.toml")
    assert autostart.is_enabled() and setup_mod.load_manifest()["autostart"] is True
    undo = [u for u in setup_mod.plan_uninstall(FakePlatform(None), FakeAW(up=False)) if u.name == "launch at login"]
    assert undo and undo[0].do() == "removed" and not autostart.is_enabled()


# ---------------------------------------------------------------------------------------------------- episodes
def test_a_stuck_stretch_is_named_after_its_main_app():
    seen = []
    ep = Episodes(lambda kind, **d: seen.append((kind, d)))
    for t, s, app in [(0, .1, "Code"), (5, .3, "Code"), (10, .4, "Chrome"), (15, .5, "Chrome"), (20, .5, "Chrome"),
                      (25, .1, "Code")]:
        ep.feed(t, s, 0.2, False, app)
    assert [d["state"] for _, d in seen] == ["open", "closed"]
    closed = seen[-1][1]
    assert closed["app"] == "Chrome" and closed["start"] == 5 and closed["end"] == 20 and closed["peak"] == 0.5


def test_a_pause_closes_the_stretch():
    seen = []
    ep = Episodes(lambda kind, **d: seen.append(d))
    ep.feed(0, .5, .2, False, "Code")
    ep.feed(5, .5, .2, True, None)
    assert [d["state"] for d in seen] == ["open", "closed"]


# ---------------------------------------------------------------------------------------------------- the API
def _call(base, path, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    h = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(base + path, data, {**h, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if r.headers.get("Content-Type", "").startswith("application/json") else raw)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


@pytest.fixture
def api_server(sandbox, monkeypatch):
    stopped = threading.Event()
    bus = Bus()
    api = server.Api(bus, plat=FakePlatform(sandbox / "home" / ".zshrc"), aw=FakeAW(up=False),
                     on_shutdown=stopped.set)
    httpd = server.start(bus, 0, api=api)
    yield f"http://127.0.0.1:{httpd.server_address[1]}", api, stopped
    httpd.shutdown()
    httpd.server_close()


def test_settings_round_trip_through_the_api(api_server, sandbox):
    base, api, _ = api_server
    status, body = _call(base, "/api/settings", {"llm": {"base_url": "http://h/v1", "model": "m", "api_key": "sk-x"},
                                                 "agent": {"monitor_typed_content": True}})
    assert status == 200 and body["ok"] and body["settings"]["llm"]["api_key_set"] is True
    cfg = Path(body["settings"]["path"])
    assert cfg.exists() and 'api_key = "sk-x"' in cfg.read_text()
    assert setup_mod.load_manifest()["config_created"] == str(cfg)
    got = _call(base, "/api/settings")[1]
    assert got["llm"]["model"] == "m" and "api_key" not in got["llm"]
    assert _call(base, "/api/settings", {"llm": {"samples": "lots"}})[0] == 400


def test_writes_must_be_json_from_this_machine(api_server):
    base, _, _ = api_server
    req = urllib.request.Request(base + "/api/settings", b"llm=1", {"Content-Type": "application/x-www-form-urlencoded"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 415
    assert _call(base, "/api/settings", {"llm": {}}, {"Origin": "https://evil.example"})[0] == 403


def test_setup_cannot_finish_without_a_model(api_server):
    base, _, _ = api_server
    status, body = _call(base, "/api/setup/finish", {})
    assert status == 200 and body["ok"] is False and "model" in body["detail"]
    _call(base, "/api/settings", {"llm": {"base_url": "http://h/v1", "model": "m"}})
    assert _call(base, "/api/setup/finish", {})[1]["ok"] is True
    assert _call(base, "/api/app")[1]["ready"] is True


def test_endpoint_test_explains_a_failure(api_server, monkeypatch):
    base, _, _ = api_server
    monkeypatch.setattr(setup_mod, "check_endpoint",
                        lambda s: setup_mod.Result("LLM endpoint", "failed", "HTTP 401 Unauthorized"))
    body = _call(base, "/api/setup/endpoint", {"base_url": "http://h/v1", "model": "m", "api_key": "k"})[1]
    assert body["ok"] is False and "API key" in body["hint"]
    monkeypatch.setattr(setup_mod, "check_endpoint",
                        lambda s: setup_mod.Result("LLM endpoint", "ok", f"{s.llm.base_url} answered"))
    body = _call(base, "/api/setup/endpoint", {"base_url": "http://h/v1", "model": "m"})[1]
    assert body["ok"] is True and "http://h/v1" in body["detail"]


def test_decisions_export_as_csv(api_server, sandbox, monkeypatch):
    from saip import offers
    base, _, _ = api_server
    monkeypatch.setattr(offers, "user_config_dir", lambda: sandbox / "cfg")
    offers.append_decision({"time": 1.0, "decision": "quiet", "score": 0.2, "app": "Code"})
    offers.append_decision({"time": 2.0, "decision": "offer", "score": 0.6, "app": "Google Chrome"})
    assert _call(base, "/api/decisions")[1]["total"] == 2
    req = urllib.request.Request(base + "/api/export?what=decisions&format=csv")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert "attachment" in r.headers["Content-Disposition"]
        lines = r.read().decode().splitlines()
    assert lines[0] == "time,decision,score,app" and lines[2] == "2.0,offer,0.6,Google Chrome"
    assert _call(base, "/api/export?what=passwords&format=csv")[0] == 400


def test_shutdown_and_unknown_routes(api_server):
    base, _, stopped = api_server
    assert _call(base, "/api/shutdown", {})[1] == {"ok": True}
    assert stopped.wait(2)
    assert _call(base, "/api/nothing")[0] == 404


def test_pages_fall_back_to_the_app_and_never_leave_the_build(api_server, monkeypatch, tmp_path):
    base, _, _ = api_server
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>SAIP</title>app")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("no")
    monkeypatch.setattr(server, "DIST", dist)
    assert b"app" in _call(base, "/settings")[1] and b"app" in _call(base, "/welcome")[1]
    assert _call(base, "/assets/app.js")[1] == b"console.log(1)"
    assert b"no" not in _call(base, "/../secret.txt")[1]


def test_the_demo_walks_through_setup_without_touching_anything():
    from saip.demo import DemoApi
    api = DemoApi(Bus(), fresh=True)
    assert api.app()["ready"] is False and api.finish()["ok"] is False
    api.save_settings({"llm": {"base_url": "http://x/v1", "model": "m", "api_key": "k"}})
    assert api.test_endpoint("http://x/v1", "m", None)["ok"] and api.finish()["ok"]
    assert api.app()["ready"] is True


# ---------------------------------------------------------------------------------------------------- the tray
def test_tray_menu_reads_the_agent_state():
    from saip import tray

    class Item:
        def __init__(self, text, action=None, **kw):
            self.text, self.action, self.kw = text, action, kw

    class Menu:
        SEPARATOR = "-"

        def __init__(self, *items):
            self.items = items

    fake = type("pystray", (), {"Menu": Menu, "MenuItem": Item})
    client = tray.Client("http://127.0.0.1:1")
    client.state, client.tick = tray.STUCK, {"score": 0.4, "trigger": 0.2, "app": "Code"}
    client.app = {"monitor_typed_content": True, "autostart": False}
    menu = tray.build_menu(fake, client, 5610, None)
    items = [i for i in menu.items if i != "-"]
    assert items[0].text(None) == "SAIP: possibly stuck (stuck score 0.40) in Code"
    names = [i.text for i in items[1:]]
    assert names[:2] == ["Open dashboard", "Pause offers"] and "Quit SAIP" in names
    typing = next(i for i in items if i.text == "Typed-text monitor")
    assert typing.kw["checked"](None) is True
    assert next(i for i in items if i.text == "Launch at login").kw["checked"](None) is False


def test_tray_draws_off_the_main_thread_only_where_that_is_safe(monkeypatch):
    from saip import tray
    monkeypatch.setattr(sys, "platform", "linux")
    ran = []
    tray._on_main(lambda: ran.append(1))
    assert ran == [1]


def test_the_mark_is_drawn_for_every_state():
    pytest.importorskip("PIL")
    from saip import icons
    for template in (True, False):
        imgs = {s: icons.render(s, 32, template=template).tobytes() for s in (icons.FLOW, icons.STUCK, icons.PAUSED,
                                                                             icons.OFFLINE)}
        assert len(set(imgs.values())) == 4
    assert icons.svg(icons.STUCK).startswith("<svg") and "<circle" in icons.svg(icons.STUCK)


# ---------------------------------------------------------------------------------------------------- the agent
def test_the_agent_publishes_ticks_with_the_app_and_logs_each_decision(sandbox, monkeypatch):
    import time as _time

    from saip import offers, runtime
    from test_app import NOW, healthy_aw
    monkeypatch.setattr(offers, "user_config_dir", lambda: sandbox / "cfg")
    monkeypatch.setattr(runtime, "user_config_dir", lambda: sandbox / "cfg")
    monkeypatch.setattr(_time, "time", lambda: NOW)
    (sandbox / "private.toml").write_text('[llm]\nprivate_apps = ["Signal"]\n[agent]\ntick_s = 0.05\nassess_interval_s = 0\n')
    bus = Bus()
    rt = runtime.Runtime(bus, FakePlatform(None), healthy_aw(),
                         runtime.RunOptions(mock_llm=True, trigger=0.0, no_dialog=True))
    rt.start()
    try:
        deadline = _real_time() + 10
        while _real_time() < deadline and not (bus.snapshot()["ticks"] and rt.inspector):
            _sleep(0.05)
    finally:
        rt.stop()
    snap = bus.snapshot()
    tick = snap["ticks"][-1]
    assert tick["app"] and "circling" in tick["features"]["items"] and tick["trigger"] == 0.0
    decisions = [e for e in snap["log"] if e["kind"] == "decision"]
    assert decisions and decisions[0]["app"] == tick["app"]
    assert offers.read_decisions()[0]["decision"] == decisions[0]["decision"]
    entry = rt.inspector[0]
    assert entry["context"].startswith("Activity over the last") and not rt.running
    assert any(e["kind"] == "app" for e in snap["log"])


def test_repositories_you_have_not_decided_on_are_reported_then_settled(sandbox):
    import subprocess
    (sandbox / "work").mkdir()
    for n in ("a", "b"):
        subprocess.run(["git", "init", "-q", str(sandbox / "work" / n)], check=True)
    (sandbox / "private.toml").write_text(f'[agent]\nwatch_roots = [{json.dumps(str(sandbox / "work"))}]\n')
    assert len(setup_mod.repo_overview()["undecided"]) == 2
    setup_mod.repo_decline(str(sandbox / "work"))
    assert setup_mod.repo_overview()["undecided"] == []
    setup_mod.record_decisions(forget=[k for k in setup_mod.decisions()[1]])
    assert len(setup_mod.repo_overview()["undecided"]) == 2
    setup_mod.repo_add(str(sandbox / "work"))
    assert setup_mod.repo_overview()["undecided"] == []


# ---------------------------------------------------------------------------------------------------- chat, excerpts, notifications
def test_a_reply_cut_at_the_token_limit_is_retried_then_trimmed():
    from llm.client import Choice
    from llm.text import complete_text, whole_sentences
    calls = []

    class Cut:
        def complete(self, messages, *, max_tokens=1, temperature=0.0, **_):
            calls.append(max_tokens)
            return [Choice("First point. Second point that never", finish_reason="length")]

    assert complete_text(Cut(), [], max_tokens=50, temperature=0) == "First point."
    assert calls == [50, 100]

    class Whole:
        def complete(self, messages, *, max_tokens=1, temperature=0.0, **_):
            return [Choice("All  done.", finish_reason="stop")]

    assert complete_text(Whole(), [], max_tokens=50, temperature=0) == "All done."
    assert whole_sentences("No full stop here") == "No full stop here"
    assert whole_sentences('He said "stop." Then') == 'He said "stop."'


def test_screen_excerpts_start_and_end_on_clean_boundaries_and_mark_the_cursor():
    from saip.screen import CURSOR, CUT, window_text
    t = ("The results show that the method improves recall on all benchmarks. In addition we find that the gain is "
         "largest when the training set is small, which suggests that the model generalises from fewer examples. "
         "However the effect disappears when the labels are noisy.")
    cur = t.index("suggests that the model") + len("suggests that the model")
    out = window_text(t, cur, 120, tail=False)
    assert out.startswith(CUT) and out.endswith(CUT) and f"model{CURSOR}" in out
    inner = out[len(CUT):-len(CUT)].strip().replace(CURSOR, "")
    assert inner in t and not inner.startswith(("esults", "he ")) and inner[0].isupper() or inner[0] == "t"
    assert window_text("hello world", 5, 50, tail=False) == f"hello{CURSOR} world"
    assert window_text(t, 0, 60, tail=True).startswith(CUT)


def test_typed_text_starts_on_a_whole_word():
    from saip.typing_monitor import TypingMonitor
    m = TypingMonitor(foreground=lambda: "Code", clip_chars=14)
    m.refresh_allowed()
    for ch in "the quick brown fox":
        m.handle_key(type("K", (), {"char": ch, "name": None})())
    assert m.get_current_text() == "brown fox"


def test_the_prompts_say_the_excerpt_is_a_window():
    from llm import chat, helper, proposal
    for prompt in (helper.SYSTEM, chat.SYSTEM, proposal.SYSTEM):
        assert "[…]" in prompt and "▌" in prompt


def test_conversations_start_after_help_and_the_model_answers(sandbox):
    from saip.chats import Chats
    seen = {}

    class Model:
        def chat(self, messages, tools=None, **_):
            seen["messages"] = list(messages)
            return {"content": "Try naming the one thing you expected.", "tool_calls": []}

    fresh = {"text": "Activity: 3 switches, just now."}
    rt = type("RT", (), {"chats": Chats(), "client": Model(), "last_context": "Activity: 14 switches.",
                         "look": staticmethod(lambda: None),
                         "snapshot_context": staticmethod(lambda: fresh["text"])})()
    api = server.Api(Bus(), plat=FakePlatform(None), aw=FakeAW(up=False), runtime=rt)
    cid = rt.chats.create("Talk it through", "Activity: 14 switches.", {"app": "TextEdit", "text": "a draft▌"}, first="Here is a start.")
    assert [m["role"] for m in api.chat_get(cid)["messages"]] == ["assistant"]
    out = api.chat_send(cid, "I am not sure what to do next")
    assert [m["role"] for m in out["messages"]] == ["assistant", "user", "assistant"]
    sent = seen["messages"]
    assert sent[0]["role"] == "system" and "Activity: 14 switches." in sent[1]["content"] and "a draft▌" in sent[1]["content"]
    assert sent[-1] == {"role": "user", "content": "I am not sure what to do next"}
    assert "context" not in out and out["has_screen"] is True
    assert api.chats()["chats"][0]["id"] == cid and api.chats()["available"] is True
    assert api.chat_new("Another")["messages"] == []
    live = api.chat_new("From the page")["id"]
    assert rt.chats.get(live)["context"] == "Activity: 3 switches, just now."
    fresh["text"] = "Activity: 9 switches, a minute later."
    api.chat_send(live, "what was I doing?")
    assert "9 switches, a minute later" in seen["messages"][1]["content"]
    assert "14 switches" in rt.chats.get(cid)["context"]                # an offer's chat keeps what the offer saw
    for bad in (lambda: api.chat_send(999, "hi"), lambda: api.chat_send(cid, "  ")):
        with pytest.raises(server.ApiError):
            bad()
    rt.client = None
    with pytest.raises(server.ApiError):
        api.chat_send(cid, "hello?")


def test_chats_work_over_http(api_server):
    base, api, _ = api_server
    assert _call(base, "/api/chats")[0] == 409                    # no agent attached to this server
    from saip.demo import DemoApi
    demo = DemoApi(Bus())
    httpd = server.start(demo.bus, 0, api=demo)
    b = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        cid = _call(b, "/api/chats", {"title": "Hi"})[1]["id"]
        out = _call(b, f"/api/chats/{cid}", {"message": "help me"})[1]
        assert [m["role"] for m in out["messages"]] == ["user", "assistant"]
        assert _call(b, f"/api/chats/{cid}")[1]["title"] == "Hi" and _call(b, "/api/chats/x")[0] == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_every_offer_sends_a_notification_unless_switched_off(sandbox, monkeypatch):
    import time as _time

    from saip import offers, runtime
    from test_app import NOW, healthy_aw
    monkeypatch.setattr(offers, "user_config_dir", lambda: sandbox / "cfg")
    monkeypatch.setattr(runtime, "user_config_dir", lambda: sandbox / "cfg")
    monkeypatch.setattr(_time, "time", lambda: NOW)
    from llm.client import Choice

    class AlwaysYes:
        def complete(self, messages, *, max_tokens=1, temperature=0.0, logprobs=False, n=1, **_):
            if n > 1:
                return [Choice("Yes") for _ in range(n)]
            return [Choice("Yes", {"Yes": -0.001, "No": -9.0} if logprobs else None)]

    monkeypatch.setattr(runtime, "MockClient", AlwaysYes)

    def run(setting):
        (sandbox / "private.toml").write_text(f"[agent]\ntick_s = 0.05\nassess_interval_s = 0\nnotify_offers = {setting}\n"
                                              '[llm]\nbase_url = "http://x/v1"\nmodel = "m"\n')
        sent = []
        plat = FakePlatform(None)
        plat.notify = lambda title, message: sent.append((title, message)) or True
        plat.can_ask = lambda: False
        rt = runtime.Runtime(Bus(), plat, healthy_aw(), runtime.RunOptions(mock_llm=True, trigger=0.0, no_dialog=True,
                                                                            config=str(sandbox / "private.toml")))
        rt.start()
        try:
            end = _real_time() + 10
            while _real_time() < end and not sent and setting == "true":
                _sleep(0.05)
            _sleep(0.4)
        finally:
            rt.stop()
        return sent

    on, off = run("true"), run("false")
    assert on and on[0][0] == "SAIP" and on[0][1]
    assert off == []


def test_status_and_stop_talk_to_a_running_saip(api_server, capsys):
    import argparse

    from saip import cli
    base, _, stopped = api_server
    port = int(base.rsplit(":", 1)[1])
    assert cli.cmd_status(argparse.Namespace(ui_port=port)) == 0 and "SAIP is running" in capsys.readouterr().out
    assert cli.cmd_status(argparse.Namespace(ui_port=1)) == 3
    assert cli.cmd_stop(argparse.Namespace(ui_port=1)) == 0 and "not running" in capsys.readouterr().out
    cli.cmd_stop(argparse.Namespace(ui_port=port))               # the fixture's server stays up; the request must arrive
    assert stopped.wait(2)
