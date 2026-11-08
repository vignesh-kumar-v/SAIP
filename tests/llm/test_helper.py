from llm.client import Choice
from llm.helper import FALLBACK, give_help, help_messages
from llm.proposal import propose, proposal_messages
from saip.screen import Screen

SCREEN = Screen("Word", "The gate lowers false alarms, but")


class One:
    def __init__(self, text):
        self.text, self.seen = text, []

    def complete(self, messages, **kw):
        self.seen.append(messages)
        return [Choice(self.text)]


def test_screen_text_reaches_the_proposal_prompt():
    user = proposal_messages("CTX", None, SCREEN)[1]["content"]
    assert "Text near the cursor in Word" in user and "The gate lowers" in user
    assert "Text near the cursor" not in proposal_messages("CTX")[1]["content"]


def test_propose_passes_the_screen_on():
    c = One('{"Purpose": "p", "Proactive_Task": "Finish it", "Response": "Want help finishing that sentence?"}')
    p = propose(c, "CTX", screen=SCREEN)
    assert p.generated and "The gate lowers" in c.seen[0][1]["content"]


def test_help_uses_the_offer_and_the_text():
    c = One("  Try: but the effect\n is small.  ")
    text = give_help(c, "Finish the paragraph", "CTX", SCREEN)
    assert text == "Try: but the effect is small."
    user = c.seen[0][1]["content"]
    assert "You offered: Finish the paragraph" in user and "The gate lowers" in user


def test_empty_help_falls_back():
    assert give_help(One("   "), "t", "CTX") == FALLBACK
    assert "Text near" not in help_messages("t", "CTX")[1]["content"]
