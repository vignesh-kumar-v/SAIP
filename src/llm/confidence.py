"""From a yes/no answer to a probability.

First pass: read the log-probabilities of "yes" and "no" on the first token and normalise over just
those two, so mass on other tokens can't leak in:

    p = exp(l_yes) / (exp(l_yes) + exp(l_no))

Borderline pass: sample the answer k times and use the share of "yes". With k = 5 the standard error
near p = 0.5 is about 0.22, wider than the 0.1 margin, so k defaults to 10 (about 0.16).
"""
from __future__ import annotations

import math

from .client import ChatClient, LLMError


def yes_probability(first_token_logprobs: dict) -> float:
    yes = no = 0.0
    for token, lp in first_token_logprobs.items():
        word = token.strip().lower().lstrip("▁Ġ")
        if word == "yes":
            yes += math.exp(lp)
        elif word == "no":
            no += math.exp(lp)
    if yes + no == 0.0:
        raise LLMError("neither 'yes' nor 'no' appears among the top log-probabilities of the first token")
    return yes / (yes + no)


def probe(client: ChatClient, messages: list, top_logprobs: int = 10) -> float:
    """One call, one token out: the model's probability of answering yes."""
    choice = client.complete(messages, max_tokens=1, temperature=0.0, logprobs=True, top_logprobs=top_logprobs)[0]
    return yes_probability(choice.first_token_logprobs)


def resampled_share(client: ChatClient, messages: list, k: int, temperature: float) -> float:
    """Ask k times and return the fraction of answers that start with yes.

    One request with n=k where the server honours it; some servers (certain Ollama versions) ignore `n` and
    return a single choice, so the rest are fetched one at a time rather than silently using fewer samples.
    """
    choices = list(client.complete(messages, max_tokens=3, temperature=temperature, n=k))
    while len(choices) < k:
        more = client.complete(messages, max_tokens=3, temperature=temperature, n=1)
        if not more:
            break
        choices.extend(more[:k - len(choices)])
    return sum(c.text.strip().lower().startswith("yes") for c in choices) / len(choices)


def clip(p: float, eps: float = 1e-3) -> float:
    return min(1.0 - eps, max(eps, p))
