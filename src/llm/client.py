"""A minimal OpenAI-compatible chat client (vLLM, llama.cpp server, Ollama /v1, hosted APIs).

Only the standard library is used. The confidence step needs token log-probabilities, so a reply
without them is an error rather than something to paper over with a self-reported number.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from .config import LLMConfig, require_endpoint


class LLMError(RuntimeError):
    pass


@dataclass
class Choice:
    text: str
    first_token_logprobs: dict | None = None     # {token: logprob} for the top candidates of the first token


class ChatClient(Protocol):
    def complete(self, messages: list, *, max_tokens: int = 1, temperature: float = 0.0, logprobs: bool = False,
                 top_logprobs: int = 10, n: int = 1) -> list[Choice]: ...


class OpenAIChatClient:
    def __init__(self, cfg: LLMConfig):
        require_endpoint(cfg)
        self.cfg = cfg
        self.url = cfg.base_url.rstrip("/") + "/chat/completions"

    def complete(self, messages, *, max_tokens=1, temperature=0.0, logprobs=False, top_logprobs=10, n=1):
        body = {"model": self.cfg.model, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature, "n": n}
        if logprobs:
            body.update(logprobs=True, top_logprobs=top_logprobs)
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = "Bearer " + self.cfg.api_key
        req = urllib.request.Request(self.url, json.dumps(body).encode(), headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout_s) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise LLMError(f"endpoint returned HTTP {e.code}: {e.read()[:200]!r}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LLMError(f"cannot reach {self.url}: {e}") from e
        return parse_choices(data, want_logprobs=logprobs)


def parse_choices(data: dict, want_logprobs: bool) -> list[Choice]:
    try:
        raw = data["choices"]
    except (KeyError, TypeError) as e:
        raise LLMError(f"unexpected reply shape: {str(data)[:200]}") from e
    out = []
    for c in raw:
        text = (c.get("message") or {}).get("content") or c.get("text") or ""
        top = None
        content = (c.get("logprobs") or {}).get("content")
        if content:
            top = {t["token"]: t["logprob"] for t in content[0].get("top_logprobs", [])}
            if not top and "token" in content[0]:
                top = {content[0]["token"]: content[0]["logprob"]}
        if want_logprobs and not top:
            raise LLMError("the endpoint returned no log-probabilities; the confidence step needs a server "
                           "that supports logprobs (vLLM, llama.cpp server, Ollama /v1)")
        out.append(Choice(text=text, first_token_logprobs=top))
    return out
