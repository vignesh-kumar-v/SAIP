"""The offer text: parsing the model's JSON, falling back safely, and passing the last outcome back."""
import json

from llm.client import Choice
from llm.proposal import (FALLBACK, FALLBACK_MESSAGE, MAX_MESSAGE, MAX_TASK, SYSTEM, parse_proposal, propose,
                          proposal_messages)

GOOD = {"Purpose": "comparing sources", "Proactive_Task": "Talk through the options",
        "Response": "You keep switching between two windows. Want to talk it through?"}


class Says:
    def __init__(self, *texts):
        self.texts, self.calls = list(texts), []

    def complete(self, messages, *, max_tokens=1, temperature=0.0, logprobs=False, top_logprobs=10, n=1):
        self.calls.append(dict(messages=messages, max_tokens=max_tokens, temperature=temperature, logprobs=logprobs))
        return [Choice(self.texts.pop(0))] if self.texts else []


def test_a_clean_json_reply_is_parsed():
    p = parse_proposal(json.dumps(GOOD))
    assert p.generated and p.task == "Talk through the options" and p.message.startswith("You keep switching")
    assert p.purpose == "comparing sources"


def test_code_fences_and_chatter_around_the_json_are_tolerated():
    reply = "Sure! Here you go:\n```json\n" + json.dumps(GOOD) + "\n```\nHope that helps."
    assert parse_proposal(reply).message == GOOD["Response"]


def test_unusable_replies_give_none():
    for bad in ("", "no braces here", "{not json}", "[1, 2]", json.dumps({**GOOD, "Proactive_Task": None}),
                json.dumps({**GOOD, "Proactive_Task": "null"}), json.dumps({**GOOD, "Response": "  "}),
                json.dumps({"Purpose": "x"})):
        assert parse_proposal(bad) is None, bad


def test_long_text_is_cut_and_whitespace_collapsed():
    long = json.dumps({**GOOD, "Proactive_Task": "t " * 100, "Response": "word\n\n" * 100})
    p = parse_proposal(long)
    assert len(p.task) <= MAX_TASK and len(p.message) <= MAX_MESSAGE and "\n" not in p.message
    assert p.task.endswith("…") and p.message.endswith("…")


def test_propose_uses_the_models_words_and_asks_for_plain_text_not_logprobs():
    client = Says(json.dumps(GOOD))
    p = propose(client, "ctx")
    assert p.generated and p.message == GOOD["Response"]
    assert client.calls[0]["logprobs"] is False and client.calls[0]["max_tokens"] >= 100


def test_a_bad_or_empty_reply_falls_back_to_the_plain_line():
    assert propose(Says("garbage"), "ctx") == FALLBACK
    assert propose(Says(), "ctx") == FALLBACK
    assert FALLBACK.message == FALLBACK_MESSAGE and not FALLBACK.generated


def test_the_last_outcome_reaches_the_prompt_only_when_there_was_one():
    plain = proposal_messages("CTX")[1]["content"]
    assert "previous offer" not in plain and plain.startswith("CTX")
    for outcome, word in (("accepted", "accepted"), ("dismissed", "dismissed"), ("ignored", "ignored")):
        assert word in proposal_messages("CTX", outcome)[1]["content"]
    assert proposal_messages("CTX", "unanswered")[1]["content"] == plain          # no dialog: nothing learned


def test_the_prompt_forbids_guessing_what_the_work_is_about():
    assert "do not guess the subject" in SYSTEM and "never invent anything" in SYSTEM
    assert proposal_messages("CTX")[0]["role"] == "system"
