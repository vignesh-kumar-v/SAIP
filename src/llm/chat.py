"""Talking it through: a conversation that continues after "Help me".

The model is given what SAIP knows (the activity summary, the text near the cursor if reading it is on) once, as
background, and then the conversation itself. Like the one-shot help it only writes words: nothing is typed into your
documents or run on your machine.
"""
from __future__ import annotations

from .client import ChatClient
from .text import complete_text

SYSTEM = """You are SAIP's assistant, talking with someone who may be stuck on their work.
You are given background first: a summary of their recent activity and, when available, the text near their cursor.
Then you talk with them. Be concrete, calm and brief (under 120 words unless they ask for more), plain sentences,
light formatting only. Use the background, but do not recite it. If you need to see something, ask one question.

About the text near the cursor:
- It is an excerpt. "[…]" marks where SAIP cut it, which is not where the person stopped, so never call the text
  incomplete because it starts or ends abruptly.
- "▌" marks the cursor. Only a sentence the cursor is inside or right after can be called unfinished.
- Text they recently typed ends where they stopped typing and may begin partway through a word.
You cannot see their screen now, only the background below, which may be out of date."""

MAX_TURNS = 16          # the newest messages sent to the model; older ones stay in the transcript only


def background(context: str, screen=None) -> str:
    parts = ["Background (what SAIP last saw):", context or "(no activity summary yet)"]
    if screen is not None:
        parts.append(f"Text near the cursor in {screen.get('app')}:\n\"\"\"\n{screen.get('text')}\n\"\"\"")
    return "\n\n".join(parts)


def chat_messages(context: str, screen, history: list[dict]) -> list[dict]:
    """`history` is [{"role": "user" | "assistant", "text": ...}], oldest first, ending with the user's message."""
    turns = [{"role": m["role"], "content": m["text"]} for m in history[-MAX_TURNS:]]
    return ([{"role": "system", "content": SYSTEM}, {"role": "user", "content": background(context, screen)},
             {"role": "assistant", "content": "Understood. What would you like to work on?"}] + turns)


def chat_reply(client: ChatClient, context: str, screen, history: list[dict], max_tokens: int = 420) -> str:
    return complete_text(client, chat_messages(context, screen, history), max_tokens=max_tokens, temperature=0.4)


__all__ = ["chat_reply", "chat_messages", "background"]
