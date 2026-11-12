"""Turn the last few minutes of a Trace into a short text summary for the model.

The summary is raw behaviour (what was in front, how often it changed, input and terminal counts). It
deliberately does not include the classifier's stuck score: that score already sets the costs, and
showing it to the model would make the model's answer an echo of it rather than a second opinion.

Privacy: window titles stay out unless `include_titles` is on, and apps on the private list are never
named. Typed text (the opt-in typing monitor, as in thunlp/ProactiveAgent's `user_input`) is included only when the
caller passes it; the caller decides whether that is allowed. Without it, keys are only ever counted.
"""
from __future__ import annotations

from collections import Counter

from classifier.categories import category, window_key
from classifier.loader import Trace

MIN_DWELL_S = 2.0
MAX_TIMELINE = 15
FAIL_FREE_EXIT = (0, 130, None)      # success, Ctrl-C, unknown: not failures
PRIVATE = "[private app]"


def _visits(window: list, t0: float, t1: float, private: tuple, titles: bool) -> list[dict]:
    out: list[dict] = []
    for e in window:
        start, end = max(e["t"], t0), min(e["t"] + e["duration"], t1)
        if end - start <= 0:
            continue
        d = e["data"]
        app = d.get("app") or "unknown"
        hidden = any(p.lower() == app.lower() for p in private)
        key = window_key(d)
        cat = "private" if hidden else category(d)
        label = PRIVATE if hidden else app
        title = "" if hidden or not titles else (d.get("title") or "")
        if out and out[-1]["key"] == key and start - out[-1]["end"] <= MIN_DWELL_S:
            out[-1]["end"] = end
        else:
            out.append(dict(start=start, end=end, key=key, app=label, cat=cat, title=title))
    return [v for v in out if v["end"] - v["start"] >= MIN_DWELL_S]


def build_context(trace: Trace, t_end: float, lookback_s: float = 300.0, include_titles: bool = False,
                  private_apps: tuple = (), typed_text: str | None = None) -> str:
    t0 = t_end - lookback_s
    visits = _visits(trace.streams["window"], t0, t_end, private_apps, include_titles)
    lines = [f"Activity over the last {lookback_s / 60:.0f} minutes (oldest first):"]
    if visits:
        for v in visits[-MAX_TIMELINE:]:
            title = f' "{v["title"]}"' if v["title"] else ""
            lines.append(f'- {t_end - v["start"]:.0f}s ago: {v["app"]} ({v["cat"]}) for '
                         f'{v["end"] - v["start"]:.0f}s{title}')
    else:
        lines.append("- no window activity recorded")
    switches = sum(1 for a, b in zip(visits, visits[1:]) if a["app"] != b["app"])
    distinct = len({v["key"] for v in visits})
    lines.append(f"Windows: {len(visits)} visits to {distinct} distinct windows "
                 f"({len(visits) - distinct} returns to a window seen earlier), {switches} app switches.")

    inp = [e for e in trace.streams.get("input", []) if t0 <= e["t"] <= t_end]
    covered = sum(e["duration"] for e in inp)
    typed = sum(e["duration"] for e in inp if e["data"].get("presses", 0) > 0)
    active = [e for e in inp if any(e["data"].get(k, 0) for k in ("presses", "clicks", "scrollX", "scrollY"))]
    if covered > 0:
        ago = t_end - max(e["t"] + e["duration"] for e in active) if active else None
        lines.append(f"Input: keys pressed during {100 * typed / covered:.0f}% of the time; "
                     + (f"last input {ago:.0f}s ago." if ago is not None else "no input at all."))
    else:
        lines.append("Input: not recorded.")
    typed = " ".join((typed_text or "").split())
    if typed:
        lines.append(f'Recently typed (oldest first, newest last): "{typed}"')

    if trace.hooks:
        cmds = [e for e in trace.streams.get("terminal", []) if t0 <= e["t"] + e["duration"] <= t_end]
        failed = [e for e in cmds if e["data"].get("exit_code") not in FAIL_FREE_EXIT]
        sigs = Counter(e["data"].get("error_sig") for e in failed if e["data"].get("error_sig"))
        repeats = max(sigs.values(), default=0)
        line = f"Terminal: {len(cmds)} commands, {len(failed)} failed"
        line += f"; the same failure occurred {repeats} times." if repeats >= 2 else "."
        lines.append(line)
        commits = sum(1 for e in trace.streams.get("fs", [])
                      if e["data"].get("kind") == "commit" and t0 <= e["t"] <= t_end)
        lines.append(f"Commits: {commits}.")
    else:
        lines.append("Terminal and commits: not monitored.")
    return "\n".join(lines)
