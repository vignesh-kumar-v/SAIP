"""The two yes/no questions. Both see the same summary and answer with one word."""
from __future__ import annotations

SYSTEM = ("You watch a short summary of someone's computer activity and judge whether an assistant should "
          "offer help. You mostly see behaviour; sometimes you also see window titles or text the person recently "
          "typed. Answer with exactly one word: Yes or No.")

NEED = "Does the user need help right now? Answer yes or no."
ACCEPT = "If an assistant offered help right now, would the user accept it? Answer yes or no."


def messages(context: str, question: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"{context}\n\n{question}"}]


def need_messages(context: str) -> list[dict]:
    return messages(context, NEED)


def accept_messages(context: str) -> list[dict]:
    return messages(context, ACCEPT)
