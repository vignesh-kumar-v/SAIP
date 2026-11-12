"""The event bus and the local API: what a UI is told, and how it answers."""
import json
import threading
import time
import urllib.error
import urllib.request

from saip import server
from saip.events import Bus, UiPlatform


def _base(httpd):
    """By number: on Windows "localhost" tries IPv6 first and waits about two seconds per request before falling back."""
    return f"http://127.0.0.1:{httpd.server_address[1]}"


def _wait(check, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(0.01)
    return False


def test_snapshot_keeps_ticks_apart_from_the_log():
    bus = Bus()
    bus.publish("tick", score=0.1)
    bus.publish("decision", decision="quiet")
    snap = bus.snapshot()
    assert [e["kind"] for e in snap["ticks"]] == ["tick"]
    assert [e["kind"] for e in snap["log"]] == ["decision"]
    assert snap["asks"] == []


def test_ask_returns_the_button_a_listener_pressed():
    bus, got = Bus(), {}
    q = bus.subscribe()
    t = threading.Thread(target=lambda: got.update(answer=bus.ask("SAIP", "Want a hand?", ("Not now", "Help me"), 5)))
    t.start()
    ask = q.get(timeout=2)
    assert ask["kind"] == "ask" and bus.snapshot()["asks"] == [ask]
    assert not bus.answer(ask["id"], "Maybe")            # not one of the buttons
    assert bus.answer(ask["id"], "Help me")
    t.join(2)
    assert got["answer"] == "Help me"
    assert not bus.answer(ask["id"], "Help me")          # already closed
    assert q.get(timeout=2)["kind"] == "ask-closed"


def test_ask_times_out_as_unanswered():
    bus = Bus()
    assert bus.ask("SAIP", "Want a hand?", ("Not now", "Help me"), 0.05) is None
    assert bus.snapshot()["asks"] == []


class _Plat:
    name = "fake"

    def __init__(self, can):
        self.can, self.asked = can, []

    def can_ask(self):
        return self.can

    def ask(self, title, message, buttons, timeout=120, default=True):
        self.asked.append(message)
        return buttons[0]


def test_ui_platform_falls_back_to_the_system_dialog_when_nothing_listens():
    bus, plat = Bus(), _Plat(True)
    ui = UiPlatform(plat, bus)
    assert ui.name == "fake" and ui.can_ask()
    assert ui.ask("SAIP", "hello", ("No", "Yes")) == "No" and plat.asked == ["hello"]
    assert UiPlatform(_Plat(False), bus).ask("SAIP", "hello", ("No", "Yes")) is None


def _get(base, path, headers=None):
    req = urllib.request.Request(base + path, headers=headers or {})
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.status, r.read()


def _post(base, path, body, headers=None):
    req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_server_serves_state_page_and_answers():
    bus = Bus()
    httpd = server.start(bus, 0, offers=lambda: {"summary": "none", "offers": []}, health=lambda: {"checks": []})
    base = _base(httpd)
    try:
        bus.publish("tick", score=0.2)
        assert json.loads(_get(base, "/api/state")[1])["ticks"][0]["score"] == 0.2
        assert b"<title>SAIP</title>" in _get(base, "/")[1]
        assert json.loads(_get(base, "/api/offers")[1]) == {"summary": "none", "offers": []}

        stream = urllib.request.urlopen(base + "/api/events", timeout=3)
        assert stream.readline() == b"event: snapshot\n"
        assert _wait(lambda: bus.listeners == 1)
        got = {}
        t = threading.Thread(target=lambda: got.update(a=UiPlatform(_Plat(True), bus).ask("SAIP", "Want a hand?", ("Not now", "Help me"), 5)))
        t.start()
        assert _wait(lambda: bus.snapshot()["asks"])
        ask_id = bus.snapshot()["asks"][0]["id"]
        assert _post(base, f"/api/asks/{ask_id}", {"button": "Help me"}, {"Origin": "https://example.com"})[0] == 403
        assert _post(base, f"/api/asks/{ask_id}", {"button": "Help me"}, {"Origin": base}) == (200, {"ok": True})
        t.join(2)
        assert got["a"] == "Help me"
        assert _post(base, f"/api/asks/{ask_id}", {"button": "Help me"})[0] == 409
        assert _post(base, "/api/asks/x", {})[0] == 400
        stream.close()
    finally:
        httpd.shutdown()


def test_server_refuses_other_hosts():
    httpd = server.start(Bus(), 0)
    base = _base(httpd)
    try:
        try:
            _get(base, "/api/state", {"Host": "evil.example"})
            raise AssertionError("should have been refused")
        except urllib.error.HTTPError as e:
            assert e.code == 403
    finally:
        httpd.shutdown()


# ---------------------------------------------------------------------------------------------------- tray
def test_pause_holds_offers_and_zero_lifts_it():
    bus = Bus()
    httpd = server.start(bus, 0)
    base = _base(httpd)
    try:
        status, body = _post(base, "/api/pause", {"minutes": 60})
        assert status == 200 and abs(body["quiet_until"] - (time.time() + 3600)) < 5
        assert bus.snapshot()["quiet_until"] == bus.quiet_until > time.time()
        assert _post(base, "/api/pause", {"minutes": 0})[1]["quiet_until"] == 0.0 == bus.quiet_until
        assert _post(base, "/api/pause", {"minutes": -1})[0] == 400
        assert _post(base, "/api/pause", {"minutes": 60}, {"Origin": "https://example.com"})[0] == 403
        assert [e["kind"] for e in bus.snapshot()["log"]] == ["quiet", "quiet"]
    finally:
        httpd.shutdown()


def test_tray_reads_a_tick_like_the_dashboard():
    from saip import tray
    assert tray.state_of(None) == tray.WAITING
    assert tray.state_of({"score": 0.07, "trigger": 0.19, "paused": False}) == tray.FLOW
    assert tray.state_of({"score": 0.30, "trigger": 0.19, "paused": False}) == tray.STUCK
    assert tray.state_of({"score": 0.30, "trigger": 0.19, "paused": True}) == tray.PAUSED
    tip = tray.tooltip(tray.FLOW, {"score": 0.07}, quiet_until=2000.0, now=1000.0)
    assert tip.startswith("SAIP: in flow (stuck score 0.07), offers paused until ")
    assert tray.tooltip(tray.OFFLINE, None, 0.0) == "SAIP: agent not running"


def _next(q, name, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        try:
            got = q.get(timeout=0.2)
        except Exception:
            continue
        if got[0] == name:
            return got[1]
    raise AssertionError(f"no '{name}' from the tray client")


def test_tray_client_follows_the_agent_answers_and_notices_it_stop():
    from saip import tray
    bus = Bus()
    bus.publish("tick", score=0.3, trigger=0.19, paused=False)
    httpd = server.start(bus, 0)
    client = tray.Client(_base(httpd), retry_s=0.05)
    client.start()
    try:
        assert "possibly stuck" in _next(client.out, "state") and client.state == tray.STUCK
        bus.publish("tick", score=0.05, trigger=0.19, paused=False)
        assert "in flow" in _next(client.out, "state")

        got = {}
        t = threading.Thread(target=lambda: got.update(a=bus.ask("SAIP", "Want a hand?", ("Not now", "Help me"), 5)))
        t.start()
        ask = _next(client.out, "ask")
        assert ask["message"] == "Want a hand?" and client.answer(ask["id"], "Not now")
        t.join(2)
        assert got["a"] == "Not now" and _next(client.out, "ask-closed") == ask["id"]

        assert client.pause(60) and "offers paused until" in _next(client.out, "state") and client.quiet
        assert client.pause(0) and _wait(lambda: not client.quiet)
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert not client.answer(1, "Help me")                      # nothing to talk to any more
    client.stop()


def test_tray_client_reports_offline_when_no_agent_is_running():
    from saip import tray
    client = tray.Client("http://127.0.0.1:1", retry_s=0.05)
    client.start()
    # generous: Windows takes about two seconds to refuse a connection to a closed port, Linux and macOS are instant
    assert _next(client.out, "state", seconds=15) == "SAIP: agent not running" and client.state == tray.OFFLINE
    client.stop()


def test_tray_icons_differ_by_state():
    import pytest
    pytest.importorskip("PIL")
    from saip import tray
    seen = {s: tray.icon_image(s).tobytes() for s in (tray.FLOW, tray.STUCK, tray.PAUSED, tray.OFFLINE)}
    assert len(set(seen.values())) == 4


def test_a_stopped_input_watcher_is_named():
    from saip.setup import check_data
    from test_app import NOW, FakePlatform, healthy_aw
    aw = healthy_aw()
    real = aw.events
    aw.events = lambda bid, *a, **k: [] if bid == "i" else real(bid, *a, **k)
    rs = {r.name: r for r in check_data(FakePlatform(None), aw, NOW)}
    assert rs["input watcher"].status == "failed" and "has stopped" in rs["input watcher"].detail


def test_a_refused_post_still_gets_its_answer_when_the_body_is_large():
    """The body is read before the refusal is sent; otherwise Windows resets the connection under the reply."""
    httpd = server.start(Bus(), 0)
    try:
        big = {"button": "x" * 20000}
        assert _post(_base(httpd), "/api/asks/1", big, {"Origin": "https://example.com"})[0] == 403
        assert _post(_base(httpd), "/api/asks/1", big, {"Host": "evil.example"})[0] == 403
    finally:
        httpd.shutdown()
