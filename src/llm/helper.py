"""What happens after "Help me": the model reads the same text and writes a concrete suggestion.

This is our version of thunlp/ProactiveAgent's execute step, without the tools. Nothing is typed into the user's
documents or run on their machine: the reply is words, shown in a dialog and printed in the terminal.
"""
from __future__ import annotations

from .client import ChatClient
from .text import complete_text

SYSTEM = """You are helping someone who just accepted your offer of help.
You are given what you offered, a summary of their recent activity and, when available, the text they were working on.
Give the help you offered, concretely and briefly (under 90 words, plain sentences, no markdown, no preamble).
Base it only on the text you are given. If no text is given, say what you would need to see and ask one question.

About the text near the cursor:
- It is an excerpt. "[…]" marks where SAIP cut it, which is not where the person stopped, so never call the text
  incomplete because it starts or ends abruptly.
- "▌" marks the cursor. Only a sentence the cursor is inside or right after can be called unfinished, and then it is
  the person's work in progress: help them continue it, do not ask them to finish it and report back.
- Text they recently typed ends where they stopped typing and may begin partway through a word."""

FALLBACK = "I could not write the help this time. Try asking again in a moment."


def help_messages(task: str, context: str, screen=None) -> list[dict]:
    parts = [f"You offered: {task}", context]
    if screen is not None:
        parts.append(f"Text near the cursor in {screen.app}:\n\"\"\"\n{screen.text}\n\"\"\"")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n\n".join(parts)}]


def give_help(client: ChatClient, task: str, context: str, screen=None, max_tokens: int = 220) -> str:
    text = complete_text(client, help_messages(task, context, screen), max_tokens=max_tokens, temperature=0.3)
    return text or FALLBACK


__all__ = ["give_help", "help_messages", "FALLBACK"]
