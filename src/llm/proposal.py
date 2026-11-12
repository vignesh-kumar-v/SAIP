"""Writing the offer itself.

The gate decides *when* to speak; this decides *what to say*. It is adapted from thunlp/ProactiveAgent, whose
prompt asks the model for a purpose, a short task phrase and a message in JSON. Two differences from theirs:

* There is no tool to run. The reply is only words shown to the user.
* The model sees the same behaviour-only summary as the yes/no probes, so it is told not to guess what the
  work is about. It may say what pattern it noticed ("you keep switching between your editor and a browser"),
  never what the problem is.

The previous offer's outcome is passed back, as in the original: ignored means the user was busy, dismissed means
try a different angle or stay quiet. If the model's reply cannot be used, the plain fallback line is shown, so a
bad reply can never block an offer the gate already approved.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .client import ChatClient

FALLBACK_TASK = "Offer help"
FALLBACK_MESSAGE = "Looks like you may be stuck. Want a hand?"
MAX_TASK, MAX_MESSAGE = 80, 240

SYSTEM = """You write a short offer of help for someone whose computer activity suggests they may be stuck.
You see a summary of behaviour (windows, switches, input and terminal counts). Sometimes you also see window titles,
text the person recently typed, and the text near the person's cursor.

Reply with one JSON object and nothing else:
{"Purpose": "what the person seems to be doing, from behaviour only",
 "Proactive_Task": "the help you offer, as a short phrase",
 "Response": "one friendly sentence offering it, under 30 words"}

Rules:
- Use only facts in what you are given. When typed text, screen text or a window title is shown, refer to what it
  actually says (the topic, the sentence that seems unfinished, the error); never invent anything beyond it.
- When no text or title is shown, do not guess the subject, language or cause: describe the pattern in plain words.
- Offer something specific to what you can see (finishing a sentence, explaining that error, tightening a
  paragraph), or something general (talking it through, a second look) when you can see nothing.
- Screen text is an excerpt: "[…]" marks where SAIP cut it, not where the person stopped, and "▌" marks the cursor.
  Never call text incomplete because it starts or ends abruptly; a sentence can be unfinished only if the cursor
  is inside or right after it. Typed text ends where they stopped typing and may begin partway through a word.
- Be brief and calm. No exclamation marks, no emoji.
- If the previous offer was dismissed, try a different angle. If it was ignored, be even briefer.

Example, for a summary showing many returns to the same two windows and little typing:
{"Purpose": "moving back and forth between two windows", "Proactive_Task": "Talk through the next step",
 "Response": "You've gone back and forth between the same two windows several times lately. Want to talk through the next step?"}

Example, for a summary plus text near the cursor that ends mid-argument ("...the delay is mostly caused by the, and▌"):
{"Purpose": "writing a results paragraph and unsure how to continue", "Proactive_Task": "Finish the paragraph",
 "Response": "Your last sentence stops mid-thought, after 'caused by the, and'. Want help finishing it?"}"""

_FEEDBACK = {
    "accepted": "The user accepted your previous offer. Do not repeat it or suggest a closely related one.",
    "dismissed": "The user dismissed your previous offer. Try a different angle.",
    "ignored": "The user ignored your previous offer, probably because they were busy. Be even briefer.",
}


@dataclass(frozen=True)
class Proposal:
    purpose: str
    task: str
    message: str
    generated: bool          # False when the reply was unusable and the fallback line is shown


FALLBACK = Proposal("", FALLBACK_TASK, FALLBACK_MESSAGE, False)


def proposal_messages(context: str, previous_outcome: str | None = None, screen=None) -> list[dict]:
    parts = [context]
    if screen is not None:
        parts.append(f"Text near the cursor in {screen.app} (what the person is working on):\n\"\"\"\n{screen.text}\n\"\"\"")
    if previous_outcome in _FEEDBACK:
        parts.append(_FEEDBACK[previous_outcome])
    parts.append("Write the offer now.")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n\n".join(parts)}]


def _clean(value, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def parse_proposal(text: str) -> Proposal | None:
    """The first JSON object in the reply, tolerating code fences and chatter around it. None if unusable."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    task = _clean(data.get("Proactive_Task"), MAX_TASK)
    message = _clean(data.get("Response"), MAX_MESSAGE)
    if not message or task.lower() in ("", "null", "none"):
        return None
    return Proposal(_clean(data.get("Purpose"), MAX_MESSAGE), task, message, True)


def propose(client: ChatClient, context: str, previous_outcome: str | None = None, max_tokens: int = 240,
            temperature: float = 0.3, screen=None) -> Proposal:
    choices = client.complete(proposal_messages(context, previous_outcome, screen), max_tokens=max_tokens,
                              temperature=temperature)
    return (parse_proposal(choices[0].text) if choices else None) or FALLBACK


__all__ = ["Proposal", "FALLBACK", "propose", "parse_proposal", "proposal_messages"]
