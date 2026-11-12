"""Settings for the LLM layer, read from private.toml (gitignored) with SAIP_* environment overrides.

Lookup order for the file: an explicit path, $SAIP_CONFIG, ./private.toml, then the per-user config
folder. A missing file is fine for offline work (mock mode, tests); `require_endpoint` complains only
when a real endpoint is actually needed.
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:          # Python 3.10
    import tomli as tomllib          # type: ignore[no-redef]

DEFAULT_PRIVATE_APPS = ("Messages", "WhatsApp", "Signal", "Telegram", "Mail", "1Password", "Keychain Access",
                        "KeePassXC", "Bitwarden")


def user_config_dir() -> Path:
    """The per-user settings folder for this OS (no third-party dependency)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "saip"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "saip"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "saip"


@dataclass(frozen=True)
class LLMConfig:
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout_s: float = 30.0
    samples: int = 10                    # k for the borderline re-check
    sample_temperature: float = 0.7
    lookback_s: float = 300.0            # how much recent activity the model is shown
    include_titles: bool = False         # window titles stay on this machine unless you opt in
    private_apps: tuple = DEFAULT_PRIVATE_APPS
    read_screen: bool = False            # read the focused text when an offer is written
    screen_chars: int = 1200             # how much of it the model is shown
    reasoning_effort: str = ""           # sent as-is when set: "none" stops a thinking model from reasoning first,
                                         # so the first token is the yes/no answer the confidence step reads
    chat_tools: bool = True              # the chat model may use tools (reading is automatic, writing needs approval)
    web_search: bool = False             # the chat model may search the web and open pages (off until you allow it)
    search_provider: str = ""            # "" or "duckduckgo" (no key), "brave" or "tavily" (need search_api_key)
    search_api_key: str = ""             # key for the chosen search provider


@dataclass(frozen=True)
class GateParams:
    c0: float = 1.0                      # base false-alarm cost
    m0: float = 2.0                      # base miss cost (c0 : m0 = 1 : 2, PRISM's middle setting)
    alpha: float = math.log(3.0)         # interrupting full flow is 3x worse than usual
    beta: float = math.log(2.0)          # missing a stuck user is 2x worse than usual
    delta: float = 0.1                   # near-miss margin
    theta_flow: float = 0.5              # flow above this defers a near miss
    eps: float = 1e-3                    # keeps probabilities away from exactly 0 and 1


@dataclass(frozen=True)
class AgentConfig:
    tick_s: float = 5.0                  # how often the live loop re-reads ActivityWatcher
    history_min: float = 90.0            # how much history each tick re-scores (the EMA settles in about a minute)
    assess_interval_s: float = 30.0      # at most one LLM assessment per this long
    cooldown_s: float = 300.0            # quiet period after an offer (stretched after ignored or dismissed ones)
    offer_timeout_s: float = 45.0        # an offer dialog left unanswered this long counts as ignored
    pending_expiry_s: float = 600.0      # a deferred offer is dropped after this long
    break_idle_s: float = 5.0            # no input for this long counts as a natural break
    repos: tuple = ()                    # individual git repositories to hook
    watch_roots: tuple = ()              # folders searched for git repositories to hook
    exclude: tuple = ()                  # folders (or repos) never to hook
    rescan_s: float = 300.0              # how often `saip run` looks for new repositories under the roots
    new_repos: str = "ask"               # what to do with a repository that appears later: ask, add, or ignore
    monitor_typed_content: bool = False  # opt-in typed-text monitor (Linux + macOS); needs the `typing` extra
    typed_text_max_chars: int = 200      # how much of the newest typed text the monitor hands out
    keep_chats: bool = False             # save chat conversations to chats.jsonl so they survive a restart
    notify_offers: bool = True           # a system notification with each offer, so it is seen with the dashboard hidden


@dataclass(frozen=True)
class Settings:
    llm: LLMConfig = field(default_factory=LLMConfig)
    gate: GateParams = field(default_factory=GateParams)
    agent: AgentConfig = field(default_factory=AgentConfig)
    path: Path | None = None


def find_config(explicit: str | Path | None = None) -> Path | None:
    candidates = [explicit, os.environ.get("SAIP_CONFIG"), Path("private.toml"), user_config_dir() / "private.toml"]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return None


def _section(cls, table: dict):
    known = {f.name: f for f in fields(cls)}
    kwargs = {}
    for key, value in table.items():
        if key not in known:
            raise ValueError(f"unknown setting '{key}' for [{cls.__name__}]")
        kwargs[key] = tuple(value) if isinstance(value, list) else value
    return cls(**kwargs)


def load_settings(path: str | Path | None = None) -> Settings:
    found = find_config(path)
    data = tomllib.loads(found.read_text(encoding="utf-8")) if found else {}
    llm = _section(LLMConfig, data.get("llm", {}))
    gate = _section(GateParams, data.get("gate", {}))
    agent = _section(AgentConfig, data.get("agent", {}))
    env = {"SAIP_LLM_BASE_URL": "base_url", "SAIP_LLM_MODEL": "model", "SAIP_LLM_API_KEY": "api_key"}
    over = {attr: os.environ[name] for name, attr in env.items() if os.environ.get(name)}
    if over:
        llm = LLMConfig(**{**{f.name: getattr(llm, f.name) for f in fields(LLMConfig)}, **over})
    return Settings(llm, gate, agent, found)


def require_endpoint(cfg: LLMConfig) -> None:
    if not cfg.base_url or not cfg.model:
        raise ValueError("no LLM endpoint configured: set [llm] base_url and model in private.toml "
                         "(see private.example.toml) or SAIP_LLM_BASE_URL / SAIP_LLM_MODEL")
