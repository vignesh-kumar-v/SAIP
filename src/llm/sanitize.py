"""Hidden characters: removed from text before the model reads it, and folded away before SAIP's own checks match.

Text can carry characters you cannot see: zero-width spaces and joiners, direction overrides, a soft hyphen, and
Unicode "tag" characters (U+E0000 to U+E007F), which spell out whole sentences invisibly. A model reads them; a person
reading the same file or page does not, so they are a way to hide instructions for the model ("ASCII smuggling"), and
they also slip past keyword checks (`r​m -rf` does not match `rm -rf`). Lookalike letters (Cyrillic "а" for
Latin "a", fullwidth "ｒｍ") do the same to the checks.

* `strip_hidden` removes the invisible characters and says what was hidden; it runs on everything a tool returns,
  on screen text, typed text, window titles in the activity summary, and the person's own chat messages.
* `for_matching` also folds lookalikes to plain letters. It is only for SAIP's checks (risky commands, signs of
  injection); the text the model and the person see is not rewritten into other letters.
"""
from __future__ import annotations

import re
import unicodedata

# zero-width and joiners, direction marks and overrides, word joiner and invisible operators, BOM, soft hyphen,
# Mongolian vowel separator, Hangul fillers, and the tag block
_INVISIBLE = re.compile("[­᠎​-‏‪-‮⁠-⁤⁦-⁩﻿"
                        "ᅟᅠㅤﾠ\U000e0000-\U000e007f]")
_TAGS = re.compile("[\U000e0020-\U000e007e]+")

# Cyrillic and Greek letters that look like Latin ones (the ones that matter for commands and English phrases)
_LOOKALIKES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "һ": "h",
    "ԁ": "d", "ԛ": "q", "ԝ": "w", "ɡ": "g", "ı": "i", "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X", "У": "Y", "І": "I", "Ј": "J", "Ѕ": "S",
    "α": "a", "ο": "o", "ρ": "p", "ν": "v", "τ": "t", "υ": "u", "ι": "i", "κ": "k", "Α": "A", "Β": "B", "Ε": "E",
    "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Χ": "X", "Υ": "Y", "Ζ": "Z",
})


def hidden_message(text: str) -> str:
    """What tag characters in `text` spell out (each one mirrors a plain ASCII character), or ""."""
    return " ".join("".join(chr(ord(ch) - 0xE0000) for ch in run) for run in _TAGS.findall(text or ""))


def strip_hidden(text: str) -> tuple[str, dict | None]:
    """(the text without invisible characters, None) or, when some were there, a note:
    {"count": how many, "message": what tag characters spelled out, if any}."""
    if not text:
        return text or "", None
    count = len(_INVISIBLE.findall(text))
    if not count:
        return text, None
    return _INVISIBLE.sub("", text), {"count": count, "message": hidden_message(text)[:300]}


def clean(text: str) -> str:
    return strip_hidden(text)[0]


def for_matching(text: str) -> str:
    """Text as SAIP's checks should see it: hidden characters gone, compatibility forms (fullwidth letters, ligatures)
    unified by NFKC, and lookalike letters folded to Latin."""
    return unicodedata.normalize("NFKC", clean(text or "")).translate(_LOOKALIKES)


__all__ = ["clean", "for_matching", "hidden_message", "strip_hidden"]
