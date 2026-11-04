"""Per-run 'user style' parameters.

Each seed gets its own style so the downstream scorer cannot overfit one
setting. Ranges deliberately overlap between states: a fast typist who is
stuck can out-type a slow typist who is in flow.
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class UserStyle:
    typing_rate: float          # presses per 5 s bin while a typing burst is active
    flow_duty: float            # fraction of editor bins in flow that are inside a burst
    burst_bins: float           # mean burst length, in 5 s bins
    mouse_p: float              # base chance a bin has mouse movement
    mouse_scale: float          # typical px moved in a moving bin
    scroll_p: float             # base chance a bin has scroll
    flow_switch_per_min: float  # app switches per minute while in flow
    stuck_pace: float           # multiplier on stuck-loop dwell times (<1 = more frantic)
    err_flow: float             # chance a command fails during flow
    save_every_s: float         # mean seconds between file saves while editing
    help_p: float               # stuck segment ends in a chatbot request
    resolved_p: float           # stuck segment resolves without help (rest: abandoned)
    return_p: float             # after abandoning, user comes back to the same error
    incidental_chat_p: float    # chatbot opened with no real need (per flow segment)
    flick_per_min: float        # ~1 s focus flicks (cmd-tab, notifications) per minute
    cluster_p: float            # chance a flick is a multi-app cmd-tab cycle
    tab_peek_per_min: float     # browser tab peeks per minute
    jitter_p: float             # chance an idle bin has a tiny mouse nudge
    title_churn_p: float        # chance a terminal stint has an animating title
    stuck_type_mult: float      # typing rate while stuck relative to flow (>1: thrashes, <1: stalls)
    stuck_duty: float           # fraction of stuck editing bins inside a typing burst
    stuck_burst: float          # mean stuck typing burst, in bins
    tdd_p: float                # chance a failing command starts a red-green loop during flow
    silent_stuck_p: float       # chance a coding stuck session is a logic bug (no error text)
    stuck_rate: float           # base chance work turns into a stuck episode
    deep_block_p: float         # chance a flow block is a long deep-work stretch
    inline_ai_p: float          # help-seeking happens inside the work app (Copilot, Cursor, Gemini) not a chat window
    review_p: float             # chance a flow block is reading/reviewing instead of typing
    agent_p: float              # chance a coding wait is supervising an AI agent (vs a build/training run)
    vim_p: float                # chance a coding block is terminal-only (vim/tmux, maybe over ssh)
    trackpad: bool              # trackpad user: hand rests on it, so the pointer moves in most active bins
    type_mouse_p: float         # chance a typing bin also has pointer movement
    scroll_x_p: float           # chance a scroll bin also scrolls horizontally (trackpads do this a lot)
    agent_flow_p: float         # chance a flow terminal stint is driving Claude Code instead of a shell
    flow_mean_min: float
    stuck_mean_min: float
    neutral_mean_min: float
    away_mean_min: float

    def as_dict(self) -> dict:
        return asdict(self)


def sample_style(rng: random.Random) -> UserStyle:
    u = rng.uniform
    lu = lambda lo, hi: math.exp(rng.uniform(math.log(lo), math.log(hi)))
    # Ranges below were re-calibrated against a real aw-watcher-input trace (MacBook, trackpad):
    # pointer moves in ~80% of active bins (70% of typing bins), scroll in ~50%, horizontal scroll in ~40%,
    # keypresses per typing bin median ~4 / p90 ~18, tiny idle nudges in <1% of active bins.
    trackpad = rng.random() < 0.7
    return UserStyle(
        typing_rate=u(6, 20),
        flow_duty=u(0.3, 0.8),
        burst_bins=u(3, 9),
        mouse_p=u(0.45, 0.80) if trackpad else u(0.15, 0.40),
        mouse_scale=u(350, 800),
        scroll_p=u(0.25, 0.50) if trackpad else u(0.08, 0.25),
        flow_switch_per_min=u(0.12, 0.45),
        stuck_pace=u(0.45, 1.5),
        err_flow=u(0.06, 0.22),
        save_every_s=u(35, 110),
        help_p=u(0.10, 0.28),
        resolved_p=u(0.30, 0.50),
        return_p=u(0.4, 0.7),
        incidental_chat_p=u(0.08, 0.22),
        flick_per_min=u(0.3, 1.6),
        cluster_p=u(0.15, 0.40),
        tab_peek_per_min=u(0.2, 0.9),
        jitter_p=u(0.01, 0.04),
        title_churn_p=u(0.0, 0.2),
        stuck_type_mult=lu(0.5, 2.3),
        stuck_duty=u(0.35, 0.9),
        stuck_burst=u(2, 6),
        tdd_p=u(0.10, 0.50),
        silent_stuck_p=u(0.15, 0.40),
        stuck_rate=u(0.12, 0.28),
        deep_block_p=u(0.10, 0.30),
        inline_ai_p=0.9 if rng.random() < 0.35 else u(0.0, 0.25),
        review_p=u(0.08, 0.3),
        agent_p=u(0.0, 0.8),
        vim_p=0.8 if rng.random() < 0.25 else 0.05,
        trackpad=trackpad,
        type_mouse_p=u(0.45, 0.75) if trackpad else u(0.10, 0.30),
        scroll_x_p=u(0.7, 0.95) if trackpad else u(0.0, 0.1),
        agent_flow_p=0.6 if rng.random() < 0.4 else u(0.0, 0.2),
        flow_mean_min=u(14, 26),
        stuck_mean_min=u(5, 10),
        neutral_mean_min=u(4, 9),
        away_mean_min=u(3, 7),
    )
