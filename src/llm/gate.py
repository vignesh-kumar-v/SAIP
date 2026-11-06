"""The decision gate: PRISM's Bayes-risk threshold with costs that move with the user's state.

    flow   = 1 - s                          s is the classifier's stuck score, 0 (flow) to 1 (stuck)
    C_FA   = c0 * exp(alpha * flow)         a false alarm costs more the deeper the focus
    C_FN   = m0 * exp(beta  * s)            a miss costs more the more stuck the user is
    tau    = C_FA / (C_FA + p_need * C_FN)  the bar p_accept has to clear

Speaking is worth it when (1 - p_accept) * C_FA <= p_accept * p_need * C_FN, which rearranges to
p_accept >= tau. In log-odds the bar is a sum, so each input pushes independently:

    L = ln(c0 / m0) + alpha * (1 - s) - beta * s - ln(p_need),    tau = sigmoid(L)

Actions: speak above the bar; wait just below it while the user is in flow (re-check at a natural
break); otherwise stay quiet. With alpha = beta = 0 this is exactly PRISM's fixed-cost gate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import GateParams

SPEAK, WAIT, QUIET = "speak", "wait", "quiet"


@dataclass(frozen=True)
class Decision:
    action: str
    tau: float
    margin: float            # p_accept - tau: positive means above the bar
    c_fa: float
    c_fn: float
    p_need: float
    p_accept: float
    stuck_score: float
    borderline: bool         # within delta of the bar, either side


def clamp(p: float, eps: float) -> float:
    return min(1.0 - eps, max(eps, p))


def costs(s: float, g: GateParams) -> tuple[float, float]:
    return g.c0 * math.exp(g.alpha * (1.0 - s)), g.m0 * math.exp(g.beta * s)


def threshold(s: float, p_need: float, g: GateParams) -> float:
    c_fa, c_fn = costs(s, g)
    return c_fa / (c_fa + clamp(p_need, g.eps) * c_fn)


def log_odds_bar(s: float, p_need: float, g: GateParams) -> float:
    return math.log(g.c0 / g.m0) + g.alpha * (1.0 - s) - g.beta * s - math.log(clamp(p_need, g.eps))


def decide(s: float, p_need: float, p_accept: float, g: GateParams) -> Decision:
    s = min(1.0, max(0.0, s))
    p_need, p_accept = clamp(p_need, g.eps), clamp(p_accept, g.eps)
    c_fa, c_fn = costs(s, g)
    tau = c_fa / (c_fa + p_need * c_fn)
    flow = 1.0 - s
    if p_accept >= tau:
        action = SPEAK
    elif tau - g.delta <= p_accept and flow > g.theta_flow:
        action = WAIT
    else:
        action = QUIET
    return Decision(action, tau, p_accept - tau, c_fa, c_fn, p_need, p_accept, s, abs(p_accept - tau) <= g.delta)


def should_probe(stuck_score: float, paused: bool, theta_trig: float) -> bool:
    """The LLM runs only when the cheap score is high enough and the person is actually at work."""
    return (not paused) and stuck_score >= theta_trig
