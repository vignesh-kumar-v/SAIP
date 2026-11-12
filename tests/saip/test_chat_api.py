"""The chat over the API: tools and approvals in the transcript, and going back (rewind) with undo."""
import json
from types import SimpleNamespace

import pytest

from llm.client import LLMError
from saip import chat_tools, server
from saip.chats import Chats
from saip.demo import DemoApi
from saip.events import Bus
from test_app import FakeAW, FakePlatform, sandbox  # noqa: F401  (the fixture)
from test_dashboard_api import _call


def say(text):
    return {"content": text, "tool_calls": []}


def call(name, args, cid="c1", text=""):
    return {"content": text, "tool_calls": [{"id": cid, "name": name, "arguments": args}]}


class Model:
    """Replays queued replies (or raises one that is an exception) and keeps what it was sent."""

    def __init__(self, *replies):
        self.replies, self.sent = list(replies), []

    def chat(self, messages, tools=None, **kw):
        self.sent.append(list(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def rig(sandbox, monkeypatch):  # noqa: F811
    root = sandbox / "work"
    root.mkdir()
    (sandbox / "private.toml").write_text(f'[agent]\nwatch_roots = ["{root}"]\n')
    copied = []
    monkeypatch.setattr(chat_tools, "copy_to_clipboard", lambda t: copied.append(t) or True)
    rt = SimpleNamespace(chats=Chats(), client=Model(), last_context="Activity: 3 switches.",
                         look=lambda: None, snapshot_context=lambda: "Activity: 3 switches.",
                         opts=SimpleNamespace(config=str(sandbox / "private.toml")))
    api = server.Api(Bus(), plat=FakePlatform(None), aw=FakeAW(up=False), runtime=rt)
    cid = rt.chats.create("Chat", "Activity: 3 switches.", None)
    return SimpleNamespace(api=api, rt=rt, cid=cid, root=root, copied=copied)


def kinds(view):
    return [i["kind"] for i in view["items"]]


def propose(rig, name, args, answer="Done, then.", text="Here is my proposal."):
    rig.rt.client.replies += [call(name, args, text=text), say(answer)]


def write_args(rig, name="notes.md", content="new text\n"):
    return {"path": str(rig.root / name), "content": content}


def test_a_read_tool_then_an_answer(rig):
    (rig.root / "todo.md").write_text("- ship it\n")
    rig.rt.client.replies += [call("read_file", {"path": str(rig.root / "todo.md")}), say("Ship it first.")]
    view = rig.api.chat_send(rig.cid, "what is on my list?")
    assert kinds(view) == ["user", "tool", "assistant"]
    tool = view["items"][1]
    assert tool["tool"]["name"] == "read_file" and tool["tool"]["ok"] is True and "todo.md" in tool["tool"]["label"]
    assert view["items"][2]["text"] == "Ship it first."
    assert [m["role"] for m in view["messages"]] == ["user", "assistant"]
    assert "write_file" in view["tools"] and "read_file" in view["tools"]
    raw = json.dumps(view)
    assert "Activity: 3 switches." not in raw and "ship it" not in raw and "tool_call_id" not in raw
    assert not any(k.startswith("_") for i in view["items"] for k in i)
    assert rig.api.chats()["chats"][0]["count"] == 2


def test_a_proposed_write_waits_for_approval(rig):
    propose(rig, "write_file", write_args(rig), answer="Saved it.")
    view = rig.api.chat_send(rig.cid, "write me a note")
    assert kinds(view) == ["user", "action", "assistant"]
    act = view["items"][1]["action"]
    assert act["state"] == "pending" and act["name"] == "write_file" and act["exists"] is False
    assert act["content"] == "new text\n" and act["path"].endswith("notes.md") and act["undoable"] is False
    assert "_call" not in json.dumps(view) and "c1" not in json.dumps(view)
    with pytest.raises(server.ApiError) as e:
        rig.api.chat_send(rig.cid, "and another thing")
    assert e.value.code == 409 and "approve or decline" in e.value.message
    assert not (rig.root / "notes.md").exists()

    out = rig.api.chat_action(rig.cid, view["items"][1]["id"], True)
    assert (rig.root / "notes.md").read_text() == "new text\n"
    act = out["items"][1]["action"]
    assert act["state"] == "approved" and act["undoable"] is True and act["detail"].startswith("Wrote ")
    assert out["items"][-1] == {**out["items"][-1], "kind": "assistant", "text": "Saved it."}
    sent = rig.rt.client.sent[-1]
    assert sent[-2]["role"] == "assistant" and sent[-2]["tool_calls"][0]["id"] == "c1"
    assert sent[-1]["role"] == "tool" and sent[-1]["tool_call_id"] == "c1" and sent[-1]["content"].startswith("Done: Wrote ")
    assert "written_sha" not in json.dumps(out) and "previous" not in json.dumps(out)
    with pytest.raises(server.ApiError) as e:
        rig.api.chat_action(rig.cid, view["items"][1]["id"], True)
    assert e.value.code == 409


def test_declining_writes_nothing_and_tells_the_model(rig):
    propose(rig, "write_file", write_args(rig), answer="Okay, not saving.")
    view = rig.api.chat_send(rig.cid, "write me a note")
    out = rig.api.chat_action(rig.cid, view["items"][1]["id"], False)
    assert not (rig.root / "notes.md").exists()
    assert out["items"][1]["action"]["state"] == "declined" and out["items"][1]["action"]["undoable"] is False
    assert out["items"][-1]["text"] == "Okay, not saving."
    assert rig.rt.client.sent[-1][-1] == {"role": "tool", "tool_call_id": "c1", "content": "The person declined this action."}
    rig.rt.client.replies.append(say("Anything else?"))
    assert rig.api.chat_send(rig.cid, "no")["items"][-1]["text"] == "Anything else?"


def test_an_action_that_fails_says_why(rig):
    propose(rig, "write_file", {"path": str(rig.root / "sub" / "a.md"), "content": "x"}, answer="That failed.")
    (rig.root / "sub").mkdir()
    view = rig.api.chat_send(rig.cid, "write a file")
    (rig.root / "sub").rmdir()
    out = rig.api.chat_action(rig.cid, view["items"][1]["id"], True)
    act = out["items"][1]["action"]
    assert act["state"] == "failed" and "does not exist" in act["detail"] and act["undoable"] is False
    assert rig.rt.client.sent[-1][-1]["content"].startswith("Failed: ")


def test_a_model_failure_after_approval_keeps_the_action(rig):
    propose(rig, "write_file", write_args(rig))
    rig.rt.client.replies[-1] = LLMError("down")
    view = rig.api.chat_send(rig.cid, "write a file")
    out = rig.api.chat_action(rig.cid, view["items"][1]["id"], True)
    assert out["items"][1]["action"]["state"] == "approved" and kinds(out) == ["user", "action", "assistant"]
    assert (rig.root / "notes.md").exists()
    rig.rt.client.replies.append(say("Back again."))
    assert rig.api.chat_send(rig.cid, "hello")["items"][-1]["text"] == "Back again."


def test_unknown_or_wrong_actions(rig):
    rig.rt.client.replies.append(say("Hi."))
    view = rig.api.chat_send(rig.cid, "hello")
    for args in ((999, 1, True), (rig.cid, 999, True), (rig.cid, view["items"][0]["id"], True)):
        with pytest.raises(server.ApiError) as e:
            rig.api.chat_action(*args)
        assert e.value.code == 404


def test_the_preview_of_the_rewind_lists_what_would_be_undone(rig):
    (rig.root / "old.md").write_text("before\n")
    propose(rig, "write_file", write_args(rig))
    first = rig.api.chat_send(rig.cid, "write a note")
    rig.api.chat_action(rig.cid, first["items"][1]["id"], True)
    propose(rig, "write_file", write_args(rig, "old.md", "after\n"))
    second = rig.api.chat_send(rig.cid, "change the old one")
    rig.api.chat_action(rig.cid, second["items"][-2]["id"] if second["items"][-1]["kind"] == "assistant" else second["items"][-1]["id"], True)
    view = rig.api.chat_get(rig.cid)
    users = [i["id"] for i in view["items"] if i["kind"] == "user"]
    prev = rig.api.chat_rewind_preview(rig.cid, users[0])
    assert prev["item"] == users[0] and prev["drops"] == len(view["items"])
    assert [(u["path"].rsplit("/", 1)[1], u["effect"]) for u in prev["undo"]] == [("old.md", "restore"), ("notes.md", "delete")]
    assert prev["cannot_undo"] == []
    later = rig.api.chat_rewind_preview(rig.cid, users[1])
    assert [u["effect"] for u in later["undo"]] == ["restore"] and later["drops"] == len(view["items"]) - users[1] + 1


def test_the_preview_names_what_cannot_be_undone(rig):
    chats = rig.rt.chats
    chats.record(rig.cid, [{"role": "user", "content": "hi"}], [{"kind": "user", "text": "look it up"}], 3)
    chats.add_item(rig.cid, "tool", "Searched the web: cats", tool={"name": "web_search", "label": "Searched the web: cats", "ok": True})
    chats.add_item(rig.cid, "tool", "Opened example.com", tool={"name": "fetch_page", "label": "Opened example.com", "ok": True})
    chats.add_item(rig.cid, "tool", "Read a.md", tool={"name": "read_file", "label": "Read a.md", "ok": True})
    aid = chats.add_item(rig.cid, "action", "Copy", action={"name": "copy_to_clipboard", "title": "Copy", "path": None,
                                                           "content": "x", "diff": None, "exists": False,
                                                           "state": "pending", "detail": None}, _call="k", _args={"text": "x"})
    chats.add_item(rig.cid, "action", "Copy", action={"name": "copy_to_clipboard", "title": "Copy", "path": None,
                                                     "content": "y", "diff": None, "exists": False,
                                                     "state": "declined", "detail": None}, _call="k2", _args={})
    chats.set_action(rig.cid, aid, "approved", "Copied to the clipboard")
    prev = rig.api.chat_rewind_preview(rig.cid, 1)
    assert prev["undo"] == [] and prev["drops"] == 6
    assert prev["cannot_undo"] == ["A web search: Searched the web: cats", "A page that was opened: Opened example.com",
                                   "A copy to the clipboard."]


def test_rewind_removes_files_and_the_models_memory(rig):
    (rig.root / "old.md").write_text("before\n")
    rig.rt.client.replies.append(say("Hello."))
    rig.api.chat_send(rig.cid, "hi")
    propose(rig, "write_file", write_args(rig))
    view = rig.api.chat_send(rig.cid, "write a note")
    rig.api.chat_action(rig.cid, view["items"][-2]["id"], True)
    propose(rig, "write_file", write_args(rig, "old.md", "after\n"))
    view = rig.api.chat_send(rig.cid, "change the old one")
    rig.api.chat_action(rig.cid, view["items"][-2]["id"], True)
    assert (rig.root / "notes.md").exists() and (rig.root / "old.md").read_text() == "after\n"
    target = [i["id"] for i in view["items"] if i["kind"] == "user"][1]
    out = rig.api.chat_rewind(rig.cid, target)
    assert [m.split()[0] for m in out["undone"]] == ["Restored", "Deleted"] and out["skipped"] == []
    assert not (rig.root / "notes.md").exists() and (rig.root / "old.md").read_text() == "before\n"
    assert [i["text"] for i in out["chat"]["items"]] == ["hi", "Hello."]
    rig.rt.client.replies.append(say("Fresh start."))
    rig.api.chat_send(rig.cid, "something else")
    texts = [m.get("content") for m in rig.rt.client.sent[-1]]
    assert texts[-3:] == ["hi", "Hello.", "something else"]
    assert not any(m.get("tool_calls") or m["role"] == "tool" for m in rig.rt.client.sent[-1])
    assert "write a note" not in texts and "change the old one" not in texts


def test_a_file_edited_after_it_was_written_is_left_alone(rig):
    propose(rig, "write_file", write_args(rig))
    view = rig.api.chat_send(rig.cid, "write a note")
    rig.api.chat_action(rig.cid, view["items"][1]["id"], True)
    (rig.root / "notes.md").write_text("my own edit\n")
    out = rig.api.chat_rewind(rig.cid, view["items"][0]["id"])
    assert out["undone"] == [] and len(out["skipped"]) == 1 and "changed after SAIP wrote it" in out["skipped"][0]
    assert (rig.root / "notes.md").read_text() == "my own edit\n" and out["chat"]["items"] == []


def test_rewinding_with_a_message_edits_the_question(rig):
    rig.rt.client.replies += [say("First answer."), say("Second answer.")]
    view = rig.api.chat_send(rig.cid, "the old words")
    out = rig.api.chat_rewind(rig.cid, view["items"][0]["id"], "the new words")
    texts = [i["text"] for i in out["chat"]["items"]]
    assert texts == ["the new words", "Second answer."]
    assert "the old words" not in json.dumps(rig.rt.chats.get(rig.cid)["wire"])
    assert rig.rt.client.sent[-1][-1] == {"role": "user", "content": "the new words"}
    with pytest.raises(server.ApiError) as e:
        rig.api.chat_rewind(rig.cid, out["chat"]["items"][0]["id"], "  ")
    assert e.value.code == 400 and len(rig.api.chat_get(rig.cid)["items"]) == 2


def test_rewind_errors(rig):
    rig.rt.client.replies.append(say("Hi."))
    view = rig.api.chat_send(rig.cid, "hello")
    with pytest.raises(server.ApiError) as e:
        rig.api.chat_rewind(rig.cid, view["items"][1]["id"])
    assert e.value.code == 400
    with pytest.raises(server.ApiError) as e:
        rig.api.chat_rewind_preview(rig.cid, view["items"][1]["id"])
    assert e.value.code == 400
    for fn in (lambda: rig.api.chat_rewind(999, 1), lambda: rig.api.chat_rewind(rig.cid, 999),
               lambda: rig.api.chat_rewind_preview(rig.cid, 999), lambda: rig.api.chat_get(999)):
        with pytest.raises(server.ApiError) as e:
            fn()
        assert e.value.code == 404
    assert len(rig.api.chat_get(rig.cid)["items"]) == 2


def test_a_failed_send_leaves_the_transcript_alone(rig):
    rig.rt.client.replies += [say("One."), LLMError("boom"), say("Two.")]
    rig.api.chat_send(rig.cid, "first")
    before = rig.rt.chats.get(rig.cid)
    with pytest.raises(server.ApiError) as e:
        rig.api.chat_send(rig.cid, "second")
    assert e.value.code == 502
    after = rig.rt.chats.get(rig.cid)
    assert after["items"] == before["items"] and after["wire"] == before["wire"]
    assert rig.api.chat_send(rig.cid, "second")["items"][-1]["text"] == "Two."


def test_an_empty_reply_gets_a_plain_line_and_tools_off_is_remembered(rig):
    rig.rt.client.replies.append(say(""))
    view = rig.api.chat_send(rig.cid, "hello")
    assert view["items"][-1]["text"] == "I could not write a reply this time. Try again."
    rig.rt.client.replies.append(say("ok"))
    assert rig.api.chat_get(rig.cid)["tools"]
    rig.rt.tools_unsupported = True
    assert rig.api.chat_get(rig.cid)["tools"] == []
    rig.rt.client = None
    assert rig.api.chat_get(rig.cid)["tools"] == []


def test_the_help_text_is_part_of_what_the_model_sees(rig):
    cid = rig.rt.chats.create("Task", "ctx", None, first="help text")
    rig.rt.client.replies.append(say("Sure."))
    view = rig.api.chat_send(cid, "thanks")
    assert [i["kind"] for i in view["items"]] == ["assistant", "user", "assistant"]
    sent = rig.rt.client.sent[-1]
    assert sent[-2:] == [{"role": "assistant", "content": "help text"}, {"role": "user", "content": "thanks"}]


def test_the_demo_chat_works_over_http(tmp_path, sandbox):  # noqa: F811
    demo = DemoApi(Bus())
    httpd = server.start(demo.bus, 0, api=demo)
    b = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        cid = _call(b, "/api/chats", {"title": "Hi"})[1]["id"]
        view = _call(b, f"/api/chats/{cid}", {"message": "write a file for me"})[1]
        assert kinds(view) == ["user", "tool", "action", "assistant"] and "write_file" in view["tools"]
        act = view["items"][2]
        assert act["action"]["state"] == "pending" and act["action"]["path"] == "~/Developer/demo/notes.md"
        assert _call(b, f"/api/chats/{cid}", {"message": "again"})[0] == 409
        assert _call(b, f"/api/chats/{cid}/actions/{act['id']}", {})[0] == 400
        assert _call(b, f"/api/chats/{cid}/actions/x", {"approve": True})[0] == 404
        assert _call(b, f"/api/chats/{cid}/actions/999", {"approve": True})[0] == 404
        done = _call(b, f"/api/chats/{cid}/actions/{act['id']}", {"approve": True})[1]
        assert done["items"][2]["action"]["state"] == "approved" and done["items"][2]["action"]["undoable"] is True
        assert done["items"][2]["action"]["detail"] == "Demo: nothing was written."
        assert _call(b, f"/api/chats/{cid}/actions/{act['id']}", {"approve": True})[0] == 409
        user = view["items"][0]["id"]
        prev = _call(b, f"/api/chats/{cid}/rewind?item={user}")[1]
        assert prev["undo"] == [{"path": "~/Developer/demo/notes.md", "effect": "delete"}] and prev["drops"] == 5
        assert prev["cannot_undo"] == [] and _call(b, f"/api/chats/{cid}/rewind")[0] == 400
        assert _call(b, f"/api/chats/{cid}/rewind?item={act['id']}")[0] == 400
        out = _call(b, f"/api/chats/{cid}/rewind", {"item": user, "message": "search for cats"})[1]
        assert out["undone"] == ["Demo: nothing to undo on disk."] and out["skipped"] == []
        assert [i["text"] for i in out["chat"]["items"]][0] == "search for cats"
        assert out["chat"]["items"][1]["tool"]["name"] == "web_search"
        assert _call(b, f"/api/chats/{cid}/rewind", {"item": "1"})[0] == 400
        assert _call(b, "/api/chats/99/rewind?item=1")[0] == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert not (tmp_path / "Developer").exists() and not (tmp_path / "home" / "Developer").exists()


def test_the_demo_settings_take_a_search_key_without_keeping_it():
    demo = DemoApi(Bus())
    assert demo.settings()["llm"]["chat_tools"] is True and demo.settings()["llm"]["search_api_key_set"] is False
    out = demo.save_settings({"llm": {"search_provider": "brave", "search_api_key": "k-secret"}})
    assert out["settings"]["llm"]["search_api_key_set"] is True and "k-secret" not in json.dumps(out)


def test_help_after_an_offer_may_read_first_but_never_proposes(tmp_path):
    import dataclasses

    from llm.config import AgentConfig, LLMConfig, Settings
    from llm.helper import give_help_with_tools
    from saip.chat_tools import READ_TOOLS, Toolbox
    from saip.chats import Chats
    (tmp_path / "notes.md").write_text("The parser drops the last header.")
    settings = Settings(LLMConfig(chat_tools=True), agent=AgentConfig(watch_roots=(str(tmp_path),)))
    box = Toolbox(settings).reading_only()
    assert box.names and all(n in READ_TOOLS for n in box.names) and "write_file" not in box.names

    class Model:
        def __init__(self):
            self.replies = [{"content": "", "tool_calls": [{"id": "c1", "name": "read_file", "arguments": {"path": "notes.md"}}],
                             "finish_reason": "tool_calls"},
                            {"content": "Your parser loses the last header: check the loop bound.", "tool_calls": [],
                             "finish_reason": "stop"}]
            self.sent = []

        def chat(self, messages, tools=None, **kw):
            self.sent.append((list(messages), tools))
            return self.replies.pop(0)

    m = Model()
    text, looked = give_help_with_tools(m, "Explain the bug", "Activity: 9 switches.", None, box.schemas(), box.run)
    assert text.startswith("Your parser loses") and [e["name"] for e in looked] == ["read_file"] and looked[0]["ok"]
    assert "The parser drops the last header." in m.sent[1][0][-1]["content"]
    assert all(t["function"]["name"] in READ_TOOLS for t in m.sent[0][1])
    chats = Chats()
    cid = chats.create("Explain the bug", "ctx", None, first=text, looked=looked)
    assert [i["kind"] for i in Chats.view(chats.get(cid))["items"]] == ["tool", "assistant"]

    class Plain:
        def complete(self, messages, **kw):
            from llm.client import Choice
            return [Choice("Plain help.", finish_reason="stop")]

    assert give_help_with_tools(Plain(), "t", "c", None, [], None) == ("Plain help.", [])
