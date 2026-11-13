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


class ToolsUnsupported(LLMError):
    """The endpoint refused a request that carried tools (HTTP 400): it cannot call functions."""


@dataclass
class Choice:
    text: str
    first_token_logprobs: dict | None = None     # {token: logprob} for the top candidates of the first token
    finish_reason: str | None = None             # "stop", or "length" when the reply was cut at max_tokens


class ChatClient(Protocol):
    def complete(self, messages: list, *, max_tokens: int = 1, temperature: float = 0.0, logprobs: bool = False,
                 top_logprobs: int = 10, n: int = 1) -> list[Choice]: ...

    def chat(self, messages: list, *, tools: list | None = None, max_tokens: int = 420,
             temperature: float = 0.4) -> dict: ...


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
        if self.cfg.reasoning_effort:
            body["reasoning_effort"] = self.cfg.reasoning_effort
            if self.cfg.reasoning_effort == "none":
                body["chat_template_kwargs"] = {"enable_thinking": False}
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

    def chat(self, messages, *, tools=None, max_tokens=420, temperature=0.4):
        """One chat turn that may ask for tools: {"content", "tool_calls": [{"id", "name", "arguments"}], "finish_reason"}."""
        body = {"model": self.cfg.model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if tools:
            body.update(tools=tools, tool_choice="auto")
        if self.cfg.reasoning_effort:
            body["reasoning_effort"] = self.cfg.reasoning_effort
            if self.cfg.reasoning_effort == "none":
                body["chat_template_kwargs"] = {"enable_thinking": False}
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = "Bearer " + self.cfg.api_key
        req = urllib.request.Request(self.url, json.dumps(body).encode(), headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout_s) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = f"endpoint returned HTTP {e.code}: {e.read()[:200]!r}"
            raise (ToolsUnsupported if tools and e.code == 400 else LLMError)(detail) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LLMError(f"cannot reach {self.url}: {e}") from e
        try:
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            raise LLMError(f"unexpected reply shape: {str(data)[:200]}") from e
        calls = []
        for i, c in enumerate(message.get("tool_calls") or []):
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (TypeError, ValueError):
                args = {}
            calls.append({"id": c.get("id") or f"call_{i}", "name": fn.get("name") or "",
                          "arguments": args if isinstance(args, dict) else {}})
        return {"content": message.get("content") or "", "tool_calls": calls,
                "finish_reason": choice.get("finish_reason")}


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
        out.append(Choice(text=text, first_token_logprobs=top, finish_reason=c.get("finish_reason")))
    return out


def list_models(base_url: str, api_key: str = "", timeout_s: float = 10.0) -> list[str]:
    """Query GET {base_url}/models on an OpenAI-compatible server and return available model IDs."""
    url = base_url.rstrip("/") + "/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise LLMError(f"endpoint returned HTTP {e.code}: {e.read()[:200]!r}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError(f"cannot reach {url}: {e}") from e
    except ValueError as e:
        raise LLMError(f"invalid JSON from {url}: {e}") from e
    raw = data.get("data") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise LLMError(f"unexpected /models reply shape: {str(data)[:200]}")
    seen: list[str] = []
    for item in raw:
        mid = item.get("id") if isinstance(item, dict) else None
        if isinstance(mid, str) and mid.strip() and mid not in seen:
            seen.append(mid)
    return seen
