"""Getting a whole reply: retry once with more room when the model was cut off, then trim to the last full sentence."""
from __future__ import annotations

import re

from .client import ChatClient

_END = re.compile(r"[.!?][\"')\]]*(?=\s|$)")


def whole_sentences(text: str) -> str:
    """Cut text back to its last complete sentence. Text that already ends a sentence, or has none, is unchanged."""
    text = text.strip()
    if not text or _END.search(text[-3:]):
        return text
    ends = list(_END.finditer(text))
    return text[: ends[-1].end()].strip() if ends else text


def tidy(text: str) -> str:
    """A reply with its lines kept (code blocks and lists need them): trailing spaces removed, at most one blank
    line in a row, no blank lines at the ends."""
    lines = [ln.rstrip() for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def complete_text(client: ChatClient, messages: list, *, max_tokens: int, temperature: float,
                  keep_lines: bool = False) -> str:
    """One reply as plain text. If the endpoint says it ran out of tokens, ask again with twice the room; if that is
    also cut, keep only the whole sentences."""
    cut = False
    for tokens in (max_tokens, max_tokens * 2):
        choices = client.complete(messages, max_tokens=tokens, temperature=temperature)
        first = choices[0] if choices else None
        text = tidy(first.text if first else "") if keep_lines else " ".join((first.text if first else "").split())
        cut = bool(first and first.finish_reason == "length")
        if not cut:
            return text
    return whole_sentences(text)
