"""One full assessment: two probes, calibration, an optional re-check near the bar, then the gate."""
from __future__ import annotations

from dataclasses import dataclass

from . import prompts
from .calibration import calibrate
from .client import ChatClient
from .config import Settings
from .confidence import clip, probe, resampled_share
from .gate import Decision, decide, threshold


@dataclass(frozen=True)
class Assessment:
    decision: Decision
    p_need_raw: float
    p_accept_raw: float
    resampled: bool
    calls: int


def assess(client: ChatClient, settings: Settings, temps: dict, context: str, stuck_score: float) -> Assessment:
    g, cfg = settings.gate, settings.llm
    p_need_raw = probe(client, prompts.need_messages(context))
    p_accept_raw = probe(client, prompts.accept_messages(context))
    p_need = calibrate(p_need_raw, temps.get("need", 1.0), g.eps)
    p_accept = calibrate(p_accept_raw, temps.get("accept", 1.0), g.eps)
    calls, resampled = 2, False
    tau = threshold(stuck_score, p_need, g)
    if abs(p_accept - tau) <= g.delta:
        # close to the bar: one logprob read is too noisy, so use the share of yes over k samples
        p_accept = clip(resampled_share(client, prompts.accept_messages(context), cfg.samples,
                                        cfg.sample_temperature), g.eps)
        calls, resampled = calls + 1, True
    return Assessment(decide(stuck_score, p_need, p_accept, g), p_need_raw, p_accept_raw, resampled, calls)
