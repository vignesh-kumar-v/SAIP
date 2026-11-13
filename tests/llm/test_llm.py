import math

import numpy as np
import pytest

from classifier.loader import Trace
from llm import prompts
from llm.assess import assess
from llm.calibration import auroc, brier, calibrate, ece, fit_temperature, load_temperatures, logit, save_temperatures
from llm.client import Choice, LLMError, parse_choices
from llm.config import GateParams, LLMConfig, Settings, load_settings
from llm.confidence import clip, probe, resampled_share, yes_probability
from llm.context import build_context
from llm.gate import QUIET, SPEAK, WAIT, costs, decide, log_odds_bar, should_probe, threshold

G = GateParams()


class FakeClient:
    """Answers every question the same way: first-token log-probs plus a fixed share of sampled yeses."""

    def __init__(self, p_yes=0.9, sample_yes=1.0):
        self.p_yes, self.sample_yes, self.calls = p_yes, sample_yes, []

    def complete(self, messages, *, max_tokens=1, temperature=0.0, logprobs=False, top_logprobs=10, n=1):
        self.calls.append(dict(n=n, logprobs=logprobs, temperature=temperature))
        if n > 1:
            yes = round(self.sample_yes * n)
            return [Choice("Yes" if i < yes else "No") for i in range(n)]
        lp = {"Yes": math.log(self.p_yes), "No": math.log(1 - self.p_yes)}
        return [Choice("Yes" if self.p_yes >= 0.5 else "No", lp if logprobs else None)]


# ---- confidence ----------------------------------------------------------------------------------

def test_yes_probability_normalises_over_yes_and_no_only():
    lp = {"Yes": math.log(0.6), "No": math.log(0.2), "The": math.log(0.15)}
    assert yes_probability(lp) == pytest.approx(0.75)


def test_yes_probability_merges_case_and_space_variants():
    lp = {"Yes": math.log(0.3), " yes": math.log(0.3), "YES": math.log(0.1), "No": math.log(0.2), " no": math.log(0.1)}
    assert yes_probability(lp) == pytest.approx(0.7 / 1.0)


def test_yes_probability_needs_an_answer_token():
    with pytest.raises(LLMError):
        yes_probability({"Maybe": -0.1, "Hmm": -3.0})


def test_missing_logprobs_is_an_error_not_a_silent_fallback():
    reply = {"choices": [{"message": {"content": "Yes"}}]}
    with pytest.raises(LLMError, match="log-probabilities"):
        parse_choices(reply, want_logprobs=True)
    assert parse_choices(reply, want_logprobs=False)[0].text == "Yes"


def test_parse_choices_reads_openai_shape():
    reply = {"choices": [{"message": {"content": "Yes"},
                          "logprobs": {"content": [{"token": "Yes", "logprob": -0.1,
                                                    "top_logprobs": [{"token": "Yes", "logprob": -0.1},
                                                                     {"token": "No", "logprob": -2.4}]}]}}]}
    c = parse_choices(reply, want_logprobs=True)[0]
    assert c.first_token_logprobs == {"Yes": -0.1, "No": -2.4}


def test_probe_and_resampled_share_use_the_client():
    client = FakeClient(p_yes=0.8, sample_yes=0.3)
    assert probe(client, prompts.need_messages("ctx")) == pytest.approx(0.8)
    assert resampled_share(client, prompts.accept_messages("ctx"), 10, 0.7) == pytest.approx(0.3)
    assert client.calls[1]["n"] == 10


class IgnoresN:
    """A server that ignores `n` and always returns a single choice (as some Ollama versions do)."""

    def __init__(self):
        self.calls, self.answers = 0, iter(["Yes", "No", "Yes", "Yes", "No", "Yes"])

    def complete(self, messages, *, max_tokens=1, temperature=0.0, logprobs=False, top_logprobs=10, n=1):
        self.calls += 1
        return [Choice(next(self.answers))]


def test_resampling_still_gets_k_samples_when_the_server_ignores_n():
    client = IgnoresN()
    assert resampled_share(client, prompts.accept_messages("c"), 6, 0.7) == pytest.approx(4 / 6)
    assert client.calls == 6


def test_clip_keeps_probabilities_finite():
    assert clip(0.0) == 1e-3 and clip(1.0) == 1 - 1e-3 and clip(0.4) == 0.4


# ---- calibration ---------------------------------------------------------------------------------

def test_calibrate_identity_and_direction():
    assert calibrate(0.8, 1.0) == pytest.approx(0.8)
    assert 0.5 < calibrate(0.9, 2.0) < 0.9          # T > 1 pulls toward 0.5
    assert calibrate(0.9, 0.5) > 0.9                # T < 1 sharpens
    assert calibrate(0.5, 3.0) == pytest.approx(0.5)


def test_fit_temperature_recovers_an_injected_distortion():
    rng = np.random.default_rng(0)
    q = rng.uniform(0.05, 0.95, 20000)              # true probabilities
    y = (rng.random(20000) < q).astype(float)
    overconfident = 1 / (1 + np.exp(-2.5 * np.log(q / (1 - q))))
    assert fit_temperature(overconfident, y) == pytest.approx(2.5, abs=0.25)


def test_fit_temperature_needs_both_classes():
    with pytest.raises(ValueError):
        fit_temperature([0.2, 0.4], [0, 0])


def test_calibration_lowers_ece():
    rng = np.random.default_rng(1)
    q = rng.uniform(0.05, 0.95, 20000)
    y = (rng.random(20000) < q).astype(float)
    p = 1 / (1 + np.exp(-2.5 * np.log(q / (1 - q))))
    T = fit_temperature(p, y)
    fixed = np.array([calibrate(x, T) for x in p])
    assert ece(fixed, y) < ece(p, y) / 2


def test_metrics_known_values():
    assert brier([1.0, 0.0], [1, 0]) == 0.0
    assert brier([0.5, 0.5], [1, 0]) == 0.25
    assert auroc([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0]) == 1.0
    assert auroc([0.5, 0.5], [1, 0]) == 0.5
    assert ece([0.7] * 10, [1] * 7 + [0] * 3) == pytest.approx(0.0)


def test_temperature_file_roundtrip(tmp_path):
    path = tmp_path / "t" / "cal.json"
    assert load_temperatures(path) == {"need": 1.0, "accept": 1.0}
    save_temperatures(path, {"need": 0.5, "accept": 0.7})
    assert load_temperatures(path) == {"need": 0.5, "accept": 0.7}


# ---- gate ----------------------------------------------------------------------------------------

def test_threshold_matches_the_documented_worked_example():
    # c0 : m0 = 1 : 2, alpha = ln 3, beta = ln 2, p_need = 0.6
    assert threshold(0.0, 0.6, G) == pytest.approx(0.714, abs=0.002)    # perfect flow
    assert threshold(1.0, 0.6, G) == pytest.approx(0.294, abs=0.002)    # fully stuck
    assert threshold(0.45, 0.6, G) == pytest.approx(0.528, abs=0.003)


def test_threshold_moves_the_intended_way_everywhere():
    grid = np.linspace(0.01, 0.99, 50)
    for p_need in (0.1, 0.5, 0.9):
        taus = [threshold(s, p_need, G) for s in grid]
        assert all(a > b for a, b in zip(taus, taus[1:]))               # falls as stuck rises
    for s in (0.0, 0.5, 1.0):
        taus = [threshold(s, p, G) for p in grid]
        assert all(a > b for a, b in zip(taus, taus[1:]))               # falls as need rises


def test_log_odds_form_equals_the_ratio_form():
    for s in (0.0, 0.2, 0.7, 1.0):
        for p in (0.05, 0.4, 0.95):
            assert 1 / (1 + math.exp(-log_odds_bar(s, p, G))) == pytest.approx(threshold(s, p, G))


def test_zero_alpha_beta_reduces_to_fixed_cost_prism():
    fixed = GateParams(alpha=0.0, beta=0.0)
    for s in (0.0, 0.5, 1.0):
        assert threshold(s, 0.6, fixed) == pytest.approx(1.0 / (1.0 + 0.6 * 2.0))


def test_costs_grow_with_the_right_state():
    assert costs(0.0, G)[0] > costs(1.0, G)[0]          # false alarm costs most in flow
    assert costs(1.0, G)[1] > costs(0.0, G)[1]          # a miss costs most when stuck


def test_decide_speaks_above_the_bar():
    d = decide(0.9, 0.8, 0.9, G)
    assert d.action == SPEAK and d.margin > 0


def test_decide_waits_for_a_near_miss_in_flow_but_not_when_stuck():
    s_flow = 0.2                                        # flow = 0.8 > theta_flow
    tau = threshold(s_flow, 0.6, G)
    near = decide(s_flow, 0.6, tau - 0.05, G)
    assert near.action == WAIT and near.borderline
    far = decide(s_flow, 0.6, tau - 0.5, G)
    assert far.action == QUIET and not far.borderline
    s_stuck = 0.8                                       # flow = 0.2: the near miss is dropped, not deferred
    tau2 = threshold(s_stuck, 0.6, G)
    assert decide(s_stuck, 0.6, tau2 - 0.05, G).action == QUIET


def test_decide_clamps_degenerate_probabilities():
    d = decide(0.5, 0.0, 1.0, G)
    assert math.isfinite(d.tau) and 0 < d.tau < 1


def test_should_probe_respects_pause_and_trigger():
    assert should_probe(0.3, False, 0.1)
    assert not should_probe(0.05, False, 0.1)
    assert not should_probe(0.9, True, 0.1)


# ---- assess --------------------------------------------------------------------------------------

def test_assess_confident_yes_speaks_with_two_calls():
    client = FakeClient(p_yes=0.97)
    a = assess(client, Settings(), {"need": 1.0, "accept": 1.0}, "ctx", stuck_score=0.8)
    assert a.decision.action == SPEAK and a.calls == 2 and not a.resampled
    assert all(c["logprobs"] for c in client.calls)


def test_assess_confident_no_stays_quiet():
    a = assess(FakeClient(p_yes=0.03), Settings(), {}, "ctx", stuck_score=0.8)
    assert a.decision.action == QUIET


def test_assess_resamples_when_close_to_the_bar():
    s = 0.5
    tau = threshold(s, 0.5, G)                           # both probes return 0.5, so p_need = 0.5
    client = FakeClient(p_yes=0.5, sample_yes=1.0)
    assert abs(0.5 - tau) <= G.delta                     # the setup really is borderline
    a = assess(client, Settings(), {}, "ctx", stuck_score=s)
    assert a.resampled and a.calls == 3
    assert a.decision.p_accept == pytest.approx(1 - 1e-3)   # the sampled share replaced the logprob read
    assert client.calls[-1]["n"] == Settings().llm.samples


def test_assess_applies_temperatures():
    hot = assess(FakeClient(p_yes=0.9), Settings(), {"need": 1.0, "accept": 1.0}, "c", 0.9)
    cool = assess(FakeClient(p_yes=0.9), Settings(), {"need": 5.0, "accept": 5.0}, "c", 0.9)
    assert cool.decision.p_accept < hot.decision.p_accept


# ---- context -------------------------------------------------------------------------------------

def make_trace(hooks=False):
    win = [dict(t=0.0, duration=120.0, data={"app": "Code", "title": "secret_plan.py — proj"}),
           dict(t=120.0, duration=60.0, data={"app": "Messages", "title": "Mum"}),
           dict(t=180.0, duration=120.0, data={"app": "Code", "title": "secret_plan.py — proj"})]
    inp = [dict(t=float(t), duration=5.0, data=dict(presses=4 if t < 100 else 0, clicks=0, scrollX=0, scrollY=0))
           for t in range(0, 300, 5)]
    term = [dict(t=200.0, duration=5.0, data=dict(exit_code=1, error_sig="abc")),
            dict(t=240.0, duration=5.0, data=dict(exit_code=1, error_sig="abc")),
            dict(t=260.0, duration=5.0, data=dict(exit_code=0, error_sig=None))]
    fs = [dict(t=100.0, duration=0.0, data={"kind": "commit"})]
    return Trace({"window": win, "afk": [], "input": inp, "terminal": term, "fs": fs}, hooks=hooks)


def test_context_without_hooks_says_terminal_is_not_monitored():
    text = build_context(make_trace(hooks=False), 300.0, 300.0, private_apps=("Messages",))
    assert "not monitored" in text and "failed" not in text


def test_context_with_hooks_reports_repeated_failures_and_commits():
    text = build_context(make_trace(hooks=True), 300.0, 300.0)
    assert "3 commands, 2 failed" in text and "same failure occurred 2 times" in text and "Commits: 1" in text


def test_context_keeps_titles_and_private_apps_out_by_default():
    text = build_context(make_trace(), 300.0, 300.0, private_apps=("Messages",))
    assert "secret_plan" not in text and "Mum" not in text
    assert "[private app]" in text and "Messages" not in text


def test_context_includes_titles_only_when_asked():
    assert "secret_plan.py" in build_context(make_trace(), 300.0, 300.0, include_titles=True)


def test_context_includes_typed_text_only_when_given():
    assert "Recently typed" not in build_context(make_trace(), 300.0, 300.0)
    assert "Recently typed" not in build_context(make_trace(), 300.0, 300.0, typed_text="   ")
    text = build_context(make_trace(), 300.0, 300.0, typed_text="the delay is\nmostly caused by the, and")
    assert 'Recently typed (oldest first, newest last): "the delay is mostly caused by the, and"' in text


def test_the_system_prompts_no_longer_claim_the_model_never_sees_content():
    from llm import prompts, proposal
    assert "never content" not in prompts.SYSTEM
    assert "typed" in prompts.SYSTEM and "typed" in proposal.SYSTEM


def test_context_never_shows_the_stuck_score():
    assert "score" not in build_context(make_trace(hooks=True), 300.0, 300.0).lower()


def test_context_counts_returns_and_switches():
    text = build_context(make_trace(), 300.0, 300.0)
    assert "3 visits to 2 distinct windows" in text and "1 returns" in text and "2 app switches" in text


def test_context_on_an_empty_trace_does_not_crash():
    text = build_context(Trace({"window": [], "afk": [], "input": [], "terminal": [], "fs": []}), 100.0, 60.0)
    assert "no window activity" in text and "Input: not recorded" in text


# ---- config --------------------------------------------------------------------------------------

def test_load_settings_reads_toml(tmp_path, monkeypatch):
    for v in ("SAIP_LLM_BASE_URL", "SAIP_LLM_MODEL", "SAIP_LLM_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    path = tmp_path / "private.toml"
    path.write_text('[llm]\nbase_url = "http://h:8000/v1"\nmodel = "m"\nsamples = 12\n[gate]\nc0 = 2.0\n', encoding="utf-8")
    s = load_settings(path)
    assert (s.llm.base_url, s.llm.model, s.llm.samples, s.gate.c0) == ("http://h:8000/v1", "m", 12, 2.0)


def test_load_settings_rejects_unknown_keys(tmp_path):
    path = tmp_path / "private.toml"
    path.write_text("[llm]\nbogus = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bogus"):
        load_settings(path)


def test_environment_overrides_the_file(tmp_path, monkeypatch):
    path = tmp_path / "private.toml"
    path.write_text('[llm]\nmodel = "from-file"\n', encoding="utf-8")
    monkeypatch.setenv("SAIP_LLM_MODEL", "from-env")
    assert load_settings(path).llm.model == "from-env"


def test_missing_config_gives_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SAIP_CONFIG", str(tmp_path / "nope.toml"))
    assert load_settings().llm == LLMConfig()


def test_logit_roundtrip():
    assert logit(0.5) == 0.0 and logit(0.9) == pytest.approx(-logit(0.1))


# ---- replay (end to end on one generated day) ----------------------------------------------------

def test_replay_runs_end_to_end_on_a_generated_day(tmp_path):
    import json
    from datetime import date

    from llm.replay import MockClient, fit_temps, report, run_replay
    from synth.generator import generate_day
    from synth.writer import write_day

    write_day(generate_day(1001, date(2026, 11, 4), user=1), tmp_path / "user01" / "day01")
    (tmp_path / "index.json").write_text(json.dumps([dict(user=1, day=1, path="user01/day01")]), encoding="utf-8")
    rows = run_replay(MockClient(), Settings(), {"need": 1.0, "accept": 1.0}, tmp_path, [1], stride_s=300, max_assess=25)
    assert rows and all(r.action in ("speak", "wait", "quiet") for r in rows)
    assert all(0 <= r.p_need_raw <= 1 and 0 <= r.stuck_score <= 1 for r in rows)
    text = report(rows, Settings(), {"need": 1.0, "accept": 1.0})
    assert "state-aware gate" in text and "AUROC" in text
    if len({r.state == "stuck" for r in rows}) == 2:
        assert set(fit_temps(rows)) == {"need", "accept"}


def test_list_models_parses_openai_models_response(monkeypatch):
    import io
    import json
    import urllib.request
    from llm.client import list_models

    captured = {}

    class FakeResp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=10.0):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        payload = {"data": [{"id": "model-a"}, {"id": "model-b"}, {"id": "model-a"}]}
        return FakeResp(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert list_models("http://host:8000/v1/", api_key="secret") == ["model-a", "model-b"]
    assert captured == {"url": "http://host:8000/v1/models", "auth": "Bearer secret"}


def test_server_test_endpoint_auto_detects_single_and_multiple_models(monkeypatch):
    from saip import setup
    from saip.server import Api

    api = Api(bus=None, plat=type("P", (), {"name": "darwin"})())

    # Single model -> auto-selects and checks endpoint
    monkeypatch.setattr("llm.client.list_models", lambda *a, **kw: ["prism-festinalente/Qwen3-8B-PRISM"])
    monkeypatch.setattr(setup, "check_endpoint", lambda trial: setup.Result("LLM endpoint", "ok", f"ok {trial.llm.model}"))
    res = api.test_endpoint("http://host:8000/v1", "", "k")
    assert res["ok"] is True
    assert res["model"] == "prism-festinalente/Qwen3-8B-PRISM"
    assert res["models"] == ["prism-festinalente/Qwen3-8B-PRISM"]

    # Multiple models -> returns choices without probing
    monkeypatch.setattr("llm.client.list_models", lambda *a, **kw: ["m1", "m2"])
    res_multi = api.test_endpoint("http://host:8000/v1", "", None)
    assert res_multi["ok"] is False
    assert res_multi["models"] == ["m1", "m2"]


def test_server_test_endpoint_retries_with_reasoning_effort_none(monkeypatch):
    from saip import setup
    from saip.server import Api, _classify

    api = Api(bus=None, plat=type("P", (), {"name": "darwin"})())

    def fake_check(trial):
        if trial.llm.reasoning_effort == "none":
            return setup.Result("LLM endpoint", "ok", "ok")
        return setup.Result("LLM endpoint", "failed", "neither 'yes' nor 'no' appears among the top log-probabilities of the first token")

    monkeypatch.setattr(setup, "check_endpoint", fake_check)
    res = api.test_endpoint("http://host:8000/v1", "Qwen/Qwen3-8B-PRISM", None)
    assert res["ok"] is True
    assert res["reasoning_effort"] == "none"
    assert "thinking mode" in _classify("neither 'yes' nor 'no' appears among the top log-probabilities")


def test_server_chat_api_methods():
    from collections import deque
    from types import SimpleNamespace
    from saip.chats import Chats
    from saip.server import Api, ApiError

    rt = SimpleNamespace(
        chats=Chats(),
        client=None,
        last_context="Default activity.",
        look=lambda: None,
        snapshot_context=lambda: "Snapshot activity.",
        inspector=deque(),
        running=True,
    )
    api = Api(bus=None, plat=type("P", (), {"name": "darwin"})(), runtime=rt)

    # 1. chat_new with context and offer
    c1 = api.chat_new(title="Custom Title", context="Activity in VS Code", offer="Would you like help with debugging?")
    assert c1["title"] == "Custom Title"
    assert c1["context"] == "Activity in VS Code"
    assert len(c1["items"]) == 1
    assert c1["items"][0]["kind"] == "assistant"
    assert c1["items"][0]["text"] == "Would you like help with debugging?"

    # chat_new with offer only (derives title)
    c2 = api.chat_new(offer="Need assistance with tests?")
    assert c2["title"] == "Need assistance with tests?"
    assert c2["context"] == "Snapshot activity."
    assert c2["items"][0]["text"] == "Need assistance with tests?"

    # 2. chat_rename
    res_rename = api.chat_rename(c1["id"], "Renamed Conversation")
    assert res_rename["ok"] is True
    assert res_rename["chat"]["title"] == "Renamed Conversation"
    assert any(ch["id"] == c1["id"] and ch["title"] == "Renamed Conversation" for ch in res_rename["chats"])

    # chat_rename validation
    with pytest.raises(ApiError) as exc_empty:
        api.chat_rename(c1["id"], "   ")
    assert exc_empty.value.code == 400

    with pytest.raises(ApiError) as exc_404:
        api.chat_rename(999999, "Valid Title")
    assert exc_404.value.code == 404

    # 3. chat_stop
    assert api.chat_stop(c1["id"]) == {"ok": True}

    # 4. inspector_feedback
    entry1 = {"time": 100.5, "decision": "offer", "context": "ctx1"}
    entry2 = {"time": 200.0, "decision": "quiet", "context": "ctx2"}
    rt.inspector.append(entry1)
    rt.inspector.append(entry2)

    fb_res = api.inspector_feedback(100.5, "up")
    assert fb_res == {"ok": True, "vote": "up"}
    assert entry1.get("feedback") == "up"

    fb_clear = api.inspector_feedback(100.5, None)
    assert fb_clear == {"ok": True, "vote": None}
    assert entry1.get("feedback") is None

    with pytest.raises(ApiError) as exc_invalid_vote:
        api.inspector_feedback(100.5, "invalid")
    assert exc_invalid_vote.value.code == 400
