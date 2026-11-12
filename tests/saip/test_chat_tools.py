"""The chat model's tools: file guard rails, approval and undo, git reads, web search and page fetch, the tool loop
and the client's tool-call parsing. No network: openers, resolvers and the HTTP call are all faked."""
import io
import json
import shutil
import subprocess
import urllib.error
from dataclasses import replace
from types import SimpleNamespace

import pytest

from llm import client as client_mod
from llm.chat import TOOLS_NOTE, base_messages, chat_turn
from llm.client import LLMError, OpenAIChatClient, ToolsUnsupported
from llm.config import AgentConfig, LLMConfig, Settings
from saip import config_file, websearch
from saip.chat_tools import OUTPUT_LIMIT, READ_LIMIT, ToolError, Toolbox, safe_path, undo_write


def settings(root=None, **llm):
    agent = AgentConfig(watch_roots=(str(root),) if root else ())
    return Settings(llm=replace(LLMConfig(), **llm), agent=agent)


# ---------------------------------------------------------------------------------------------------- safe_path
def test_safe_path_accepts_files_under_a_root(tmp_path):
    (tmp_path / "a.py").write_text("x")
    assert safe_path("a.py", [str(tmp_path)]) == (tmp_path / "a.py").resolve()
    assert safe_path(str(tmp_path / "a.py"), [str(tmp_path)]) == (tmp_path / "a.py").resolve()


def _symlink(link, target) -> bool:
    """Make a symlink where the system allows it. Windows refuses without Developer Mode or admin rights; the
    symlink case is then left out and the other cases are still checked."""
    try:
        link.symlink_to(target)
        return True
    except OSError:
        return False


def test_safe_path_refuses_what_it_should(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "ok.md").write_text("x")
    (root / "pic.png").write_text("x")
    (root / "sub").mkdir()
    (root / ".git").mkdir()
    (root / ".git" / "config.txt").write_text("x")
    (tmp_path / "secret.txt").write_text("s")
    linked = _symlink(root / "link.txt", tmp_path / "secret.txt")
    roots = [str(root)]
    for bad in ("../secret.txt", str(tmp_path / "secret.txt"), *(["link.txt"] if linked else []), ".git/config.txt",
                "pic.png", "sub", "missing.md"):
        with pytest.raises(ToolError):
            safe_path(bad, roots)
    with pytest.raises(ToolError, match="nowhere I may read or write"):
        safe_path("ok.md", [])


def test_safe_path_for_write(tmp_path):
    (tmp_path / "docs").mkdir()
    assert safe_path("docs/new.md", [str(tmp_path)], for_write=True) == (tmp_path / "docs" / "new.md").resolve()
    with pytest.raises(ToolError):
        safe_path("nope/new.md", [str(tmp_path)], for_write=True)
    with pytest.raises(ToolError):
        safe_path("docs", [str(tmp_path)], for_write=True)
    if _symlink(tmp_path / "l.md", tmp_path / "docs"):
        with pytest.raises(ToolError):
            safe_path("l.md", [str(tmp_path)], for_write=True)


# ---------------------------------------------------------------------------------------------------- write / undo
def test_write_file_needs_approval_then_writes_and_undoes(tmp_path):
    box = Toolbox(settings(tmp_path))
    (tmp_path / "n.md").write_text("one\ntwo\n")
    out = box.run("write_file", {"path": "n.md", "content": "one\nthree\n"})
    assert out["kind"] == "approve" and (tmp_path / "n.md").read_text() == "one\ntwo\n"
    prev = out["preview"]
    assert prev["exists"] and "-two" in prev["diff"] and "+three" in prev["diff"] and prev["title"].startswith("Write ")
    done = box.perform("write_file", {"path": "n.md", "content": "one\nthree\n"})
    assert done["ok"] and (tmp_path / "n.md").read_text() == "one\nthree\n"
    assert undo_write(done["undo"]) == (True, f"Restored {box_path(tmp_path / 'n.md')}")
    assert (tmp_path / "n.md").read_text() == "one\ntwo\n"


def box_path(p):
    from saip.repos import tilde
    return tilde(p)


def test_undo_deletes_a_new_file_and_leaves_a_changed_one(tmp_path):
    box = Toolbox(settings(tmp_path))
    first = box.perform("write_file", {"path": "new.md", "content": "hi"})
    assert box.run("write_file", {"path": "new.md", "content": "hi"})["preview"]["diff"] is not None
    assert undo_write(first["undo"])[0] and not (tmp_path / "new.md").exists()
    assert undo_write(first["undo"]) == (False, f"{box_path(tmp_path / 'new.md')} is already gone")
    second = box.perform("write_file", {"path": "new.md", "content": "hi"})
    (tmp_path / "new.md").write_text("edited by hand")
    ok, msg = undo_write(second["undo"])
    assert not ok and "changed after SAIP wrote it" in msg and (tmp_path / "new.md").read_text() == "edited by hand"


def test_write_file_checks_arguments_before_asking(tmp_path):
    box = Toolbox(settings(tmp_path))
    assert box.run("write_file", {"path": "../x.md", "content": "a"})["kind"] == "error"
    assert box.run("write_file", {"path": "x.md", "content": "a" * 200_001})["kind"] == "error"
    assert box.run("write_file", {"path": "x.png", "content": "a"})["kind"] == "error"
    assert box.perform("write_file", {"path": "../x.md", "content": "a"})["ok"] is False


def test_clipboard_is_an_approval_and_performs(tmp_path, monkeypatch):
    box = Toolbox(settings(tmp_path))
    out = box.run("copy_to_clipboard", {"text": "abc"})
    assert out["kind"] == "approve" and out["preview"]["content"] == "abc" and out["preview"]["path"] is None
    seen = []
    monkeypatch.setattr("saip.chat_tools.copy_to_clipboard", lambda t: seen.append(t) or True)
    assert box.perform("copy_to_clipboard", {"text": "abc"}) == {"ok": True, "detail": "Copied to the clipboard",
                                                                  "undo": None} and seen == ["abc"]


# ---------------------------------------------------------------------------------------------------- reads
def test_read_file_is_capped(tmp_path):
    (tmp_path / "big.txt").write_text("a" * (READ_LIMIT + 5000))
    (tmp_path / "small.txt").write_text("hello")
    box = Toolbox(settings(tmp_path))
    big = box.run("read_file", {"path": "big.txt"})
    assert big["kind"] == "read" and big["result"].endswith(" […]") and len(big["result"]) <= OUTPUT_LIMIT + 4
    small = box.run("read_file", {"path": "small.txt"})
    assert small["result"] == "hello" and small["label"].startswith("Read ")
    assert box.run("read_file", {"path": "../nope.txt"})["kind"] == "error"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_log_and_diff_on_a_real_repo(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    git = ["git", "-c", "user.email=a@b.c", "-c", "user.name=T"]
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "f.txt").write_text("one\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "first commit"], cwd=repo, check=True)
    (repo / "f.txt").write_text("one\ntwo\n")
    box = Toolbox(settings(tmp_path))
    log = box.run("git_log", {})
    assert log["kind"] == "read" and "first commit" in log["result"] and "recent commits" in log["label"]
    diff = box.run("git_diff", {"repo": "proj"})
    assert diff["kind"] == "read" and "+two" in diff["result"] and "f.txt" in diff["result"]
    assert box.run("git_log", {"repo": str(tmp_path.parent)})["kind"] == "error"
    assert box.run("git_log", {"repo": str(tmp_path)})["kind"] == "error"         # a root that is not a repo


def test_screen_and_summary_tools():
    rt = SimpleNamespace(look=lambda: SimpleNamespace(app="TextEdit", text="draft"), snapshot_context=lambda: "ctx")
    box = Toolbox(settings(read_screen=True), rt)
    assert "TextEdit" in box.run("read_screen", {})["label"] and box.run("activity_summary", {})["result"] == "ctx"
    blank = SimpleNamespace(look=lambda: None, snapshot_context=lambda: "")
    assert Toolbox(settings(read_screen=True), blank).run("read_screen", {})["result"] == \
        "nothing readable in the app in front"


def test_toolbox_names_follow_the_settings(tmp_path):
    rt = SimpleNamespace()
    assert Toolbox(settings(tmp_path, chat_tools=False), rt).names == []
    assert Toolbox(settings()).names == ["copy_to_clipboard"]
    assert Toolbox(settings(read_screen=True), rt).names == ["activity_summary", "read_screen", "copy_to_clipboard"]
    full = Toolbox(settings(tmp_path, web_search=True), rt).names
    assert {"git_log", "git_diff", "read_file", "write_file", "web_search", "fetch_page"} <= set(full)
    assert "read_screen" not in full
    box = Toolbox(settings(tmp_path))
    assert [s["function"]["name"] for s in box.schemas()] == box.names
    assert box.run("web_search", {"query": "x"})["kind"] == "error"
    assert box.run("nonsense", {})["kind"] == "error"


# ---------------------------------------------------------------------------------------------------- web
PUBLIC = lambda host, port: [(2, 1, 6, "", ("93.184.216.34", port))]       # noqa: E731


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://localhost/x", "http://127.0.0.1/", "http://10.0.0.5/",
                                 "http://169.254.169.254/latest", "http://[::1]/", "ftp://example.com/", "http:///x"])
def test_check_url_refuses_unsafe_addresses(url):
    with pytest.raises(websearch.SearchError):
        websearch.check_url(url, PUBLIC)


def test_check_url_uses_the_resolved_address():
    with pytest.raises(websearch.SearchError):
        websearch.check_url("http://sneaky.example/", lambda h, p: [(2, 1, 6, "", ("192.168.1.2", p))])
    assert websearch.check_url("https://example.com/a", PUBLIC) == "https://example.com/a"


DDG = """<div class="result"><h2><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fqwen&rut=x">
Qwen <b>tool</b> calling</a></h2><a class="result__snippet" href="x">How to call <b>tools</b> with Qwen.</a></div>
<div class="result"><a class="result__a" href="https://plain.org/page">Plain</a>
<a class="result__snippet">Second one</a></div>"""


def test_parse_duckduckgo_unwraps_links():
    rows = websearch.parse_duckduckgo(DDG, 6)
    assert rows[0] == {"title": "Qwen tool calling", "url": "https://example.com/qwen",
                       "snippet": "How to call tools with Qwen."}
    assert rows[1]["url"] == "https://plain.org/page" and rows[1]["snippet"] == "Second one"
    assert len(websearch.parse_duckduckgo(DDG, 1)) == 1


def test_search_providers_through_a_fake_opener():
    seen = []

    def opener(req, timeout):
        seen.append(req)
        if "brave" in req.full_url:
            return json.dumps({"web": {"results": [{"title": "B", "url": "https://b.io", "description": "bd"}]}}).encode()
        if "tavily" in req.full_url:
            return json.dumps({"results": [{"title": "T", "url": "https://t.io", "content": "tc"}]}).encode()
        return DDG.encode()
    assert websearch.search("q", opener=opener)[0]["url"] == "https://example.com/qwen"
    assert "duckduckgo.com/html" in seen[-1].full_url and "Mozilla" in seen[-1].get_header("User-agent")
    brave = websearch.search("q", "brave", "K", opener=opener)
    assert brave == [{"title": "B", "url": "https://b.io", "snippet": "bd"}]
    assert seen[-1].get_header("X-subscription-token") == "K"
    tav = websearch.search("q", "tavily", "K2", limit=3, opener=opener)
    assert tav[0]["snippet"] == "tc" and json.loads(seen[-1].data) == {"api_key": "K2", "query": "q", "max_results": 3}
    with pytest.raises(websearch.SearchError):
        websearch.search("q", "brave", "", opener=opener)


def test_search_failures_are_search_errors():
    def down(req, timeout):
        raise urllib.error.URLError("no route")
    with pytest.raises(websearch.SearchError):
        websearch.search("q", opener=down)
    with pytest.raises(websearch.SearchError):
        websearch.search("q", "brave", "K", opener=lambda r, t: b"not json")
    box = Toolbox(settings(web_search=True), search_opener=down)
    assert box.run("web_search", {"query": "q"})["kind"] == "error"


def test_fetch_text_strips_scripts_and_caps_length():
    html = ("<html><head><style>p{color:red}</style><script>var secret=1;</script></head><body><p>Hello   world</p>"
            "<noscript>no js</noscript><svg><text>icon</text></svg><p>" + "word " * 5000 + "</p></body></html>")
    out = websearch.fetch_text("https://example.com/", opener=lambda r, t: html.encode(), resolve=PUBLIC)
    assert out.startswith("Hello world") and "secret" not in out and "color:red" not in out and "icon" not in out
    assert out.endswith(" […]") and len(out) == 12_000 + 4
    assert websearch.fetch_text("https://example.com/", opener=lambda r, t: b"plain text", resolve=PUBLIC) == "plain text"


def test_fetch_text_rechecks_every_redirect():
    def redirect(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": "http://127.0.0.1/admin"}, io.BytesIO())
    with pytest.raises(websearch.SearchError, match="not a public address"):
        websearch.fetch_text("https://example.com/", opener=redirect, resolve=lambda h, p: PUBLIC(h, p)
                             if h == "example.com" else [(2, 1, 6, "", ("127.0.0.1", p))])


# ---------------------------------------------------------------------------------------------------- chat_turn
class Scripted:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def chat(self, messages, *, tools=None, max_tokens=420, temperature=0.4):
        self.calls.append({"n": len(messages), "tools": tools})
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def call(i, name, **args):
    return {"id": f"c{i}", "name": name, "arguments": args}


def reply(text="", *calls):
    return {"content": text, "tool_calls": list(calls), "finish_reason": "stop"}


TOOLS = [{"type": "function", "function": {"name": "read_file"}}]


def runner(out):
    return lambda name, args: out[name]


def test_a_read_tool_then_an_answer():
    client = Scripted(reply("", call(1, "read_file", path="a.py")), reply("It  prints\nhello."))
    run = runner({"read_file": {"kind": "read", "result": "print('hello')", "label": "Read a.py"}})
    out = chat_turn(client, [{"role": "user", "content": "what does a.py do"}], TOOLS, run)
    assert out["text"] == "It  prints\nhello." and out["pending"] is None and out["tools_unsupported"] is False
    assert out["events"] == [{"name": "read_file", "arguments": {"path": "a.py"}, "label": "Read a.py", "ok": True}]
    asst, tool, final = out["messages"][1:]
    assert asst["tool_calls"][0]["function"] == {"name": "read_file", "arguments": '{"path": "a.py"}'}
    assert tool == {"role": "tool", "tool_call_id": "c1", "content": "print('hello')"} and final["role"] == "assistant"


def test_an_error_result_is_reported_not_ok():
    client = Scripted(reply("", call(1, "read_file")), reply("sorry"))
    out = chat_turn(client, [], TOOLS, runner({"read_file": {"kind": "error", "result": "no", "label": "Could not"}}))
    assert out["events"][0]["ok"] is False and any(m.get("content") == "no" for m in out["messages"])


def test_an_approve_tool_stops_with_pending_and_no_tool_message():
    prev = {"title": "Write x.md"}
    client = Scripted(reply("I propose this.", call(1, "write_file", path="x.md", content="a")))
    out = chat_turn(client, [{"role": "user", "content": "save it"}], TOOLS,
                    runner({"write_file": {"kind": "approve", "preview": prev}}))
    assert out["pending"] == {"call_id": "c1", "name": "write_file", "arguments": {"path": "x.md", "content": "a"},
                              "preview": prev}
    assert out["text"] == "I propose this." and out["messages"][-1]["role"] == "assistant"
    assert not any(m["role"] == "tool" for m in out["messages"]) and len(client.calls) == 1


def test_a_second_approve_call_in_one_reply_is_refused():
    client = Scripted(reply("", call(1, "write_file", path="a.md", content="1"), call(2, "write_file", path="b.md",
                                                                                      content="2")))
    out = chat_turn(client, [], TOOLS, runner({"write_file": {"kind": "approve", "preview": {}}}))
    assert out["pending"]["call_id"] == "c1"
    tools = [m for m in out["messages"] if m["role"] == "tool"]
    assert tools == [{"role": "tool", "tool_call_id": "c2",
                      "content": "Not run: only one action can be proposed at a time."}]


def test_the_round_cap_forces_a_final_answer_without_tools():
    read = {"kind": "read", "result": "r", "label": "l"}
    client = Scripted(*[reply("", call(i, "read_file")) for i in range(3)], reply("Here is my answer."))
    out = chat_turn(client, [], TOOLS, runner({"read_file": read}), max_rounds=3)
    assert out["text"] == "Here is my answer." and len(out["events"]) == 3
    assert [c["tools"] is not None for c in client.calls] == [True, True, True, False]


def test_tools_unsupported_falls_back_once():
    client = Scripted(ToolsUnsupported("400"), reply("plain answer"))
    out = chat_turn(client, [{"role": "user", "content": "hi"}], TOOLS, runner({}))
    assert out["text"] == "plain answer" and out["tools_unsupported"] is True
    assert client.calls[0]["tools"] and client.calls[1]["tools"] is None


def test_base_messages_add_the_tools_note_only_when_on():
    assert base_messages("ctx", None)[0]["content"] != base_messages("ctx", None, True)[0]["content"]
    assert base_messages("ctx", None, True)[0]["content"].endswith(TOOLS_NOTE) and len(base_messages("c", None)) == 3


# ---------------------------------------------------------------------------------------------------- the client
class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_client_chat_parses_tool_calls(monkeypatch):
    sent = []
    body = {"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
        {"id": "abc", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}},
        {"function": {"name": "git_log", "arguments": "{oops"}}]}}]}
    monkeypatch.setattr(client_mod.urllib.request, "urlopen",
                        lambda req, timeout: sent.append(json.loads(req.data)) or FakeResp(json.dumps(body).encode()))
    c = OpenAIChatClient(LLMConfig(base_url="http://x/v1", model="m", reasoning_effort="none"))
    out = c.chat([{"role": "user", "content": "hi"}], tools=TOOLS)
    assert out == {"content": "", "finish_reason": "tool_calls", "tool_calls": [
        {"id": "abc", "name": "read_file", "arguments": {"path": "a.py"}},
        {"id": "call_1", "name": "git_log", "arguments": {}}]}
    assert sent[0]["tools"] == TOOLS and sent[0]["tool_choice"] == "auto" and sent[0]["reasoning_effort"] == "none"
    c.chat([{"role": "user", "content": "hi"}])
    assert "tools" not in sent[1] and "tool_choice" not in sent[1]


def test_client_chat_400_with_tools_means_unsupported(monkeypatch):
    def boom(req, timeout):
        raise urllib.error.HTTPError("u", 400, "Bad", {}, io.BytesIO(b"tools not supported"))
    monkeypatch.setattr(client_mod.urllib.request, "urlopen", boom)
    c = OpenAIChatClient(LLMConfig(base_url="http://x/v1", model="m"))
    with pytest.raises(ToolsUnsupported):
        c.chat([], tools=TOOLS)
    with pytest.raises(LLMError) as e:
        c.chat([])
    assert not isinstance(e.value, ToolsUnsupported)


# ---------------------------------------------------------------------------------------------------- settings
def test_public_settings_hide_the_search_key():
    s = Settings(llm=LLMConfig(api_key="sk-main", search_api_key="tvly-secret", web_search=True))
    pub = config_file.public_settings(s)
    assert "search_api_key" not in pub["llm"] and pub["llm"]["search_api_key_set"] is True
    assert pub["llm"]["api_key_set"] is True and "api_key" not in pub["llm"]
    assert "tvly-secret" not in json.dumps(pub) and "sk-main" not in json.dumps(pub)
    assert config_file.public_settings(Settings())["llm"]["search_api_key_set"] is False


def test_chat_replies_keep_their_line_breaks_so_code_and_lists_survive():
    from llm.text import tidy
    raw = "Here is a script:  \n\n\n\n```python\nimport time\n\n\nprint(1)  \n```\n\n- one\n- two\n"
    assert tidy(raw) == "Here is a script:\n\n```python\nimport time\n\nprint(1)\n```\n\n- one\n- two"
