"""Per-state sanity statistics for a generated day.

Use this to check that states are distinguishable *but overlapping*. If a
single feature separates the states perfectly, the generator is too clean.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime

from .generator import BIN, STATES


def summarize(result: dict) -> dict:
    t0 = datetime.fromisoformat(result["start_utc"])

    def sec(ts: str) -> float:
        return (datetime.fromisoformat(ts) - t0).total_seconds()

    segs = [(sec(l["start"]), sec(l["end"]), l["state"]) for l in result["labels"]]
    starts = [s[0] for s in segs]

    def seg_at(t: float) -> int:
        return max(bisect_right(starts, t) - 1, 0)

    rows = {s: dict(minutes=0.0, presses=0, idle_s=0.0, switches=0, dwell_switches=0, cmds=0,
                    fails=0, errors=0, repeat_errors=0, chats=0, afk_s=0.0) for s in STATES}
    for a, b, s in segs:
        rows[s]["minutes"] += (b - a) / 60.0

    ev = result["events"]
    for e in ev["input"]:
        t, d, data = sec(e["timestamp"]), e["duration"], e["data"]
        if not any(data.values()):
            for k in range(int(round(d / BIN))):
                rows[segs[seg_at(t + k * BIN + BIN / 2)][2]]["idle_s"] += BIN
        else:
            rows[segs[seg_at(t + d / 2)][2]]["presses"] += data["presses"]
    for e in ev["afk"]:
        if e["data"]["status"] == "afk":
            t = sec(e["timestamp"])
            for k in range(int(round(e["duration"] / BIN))):
                rows[segs[seg_at(t + k * BIN + BIN / 2)][2]]["afk_s"] += BIN
    # raw window events include ~1 s flicks and title churn; "dwell" switches only count
    # app changes that stick for >= 2 s, which is what a noise-robust feature should use.
    last_app = None
    for e in ev["window"]:
        r = rows[segs[seg_at(sec(e["timestamp"]) + 0.01)][2]]
        r["switches"] += 1
        if e["duration"] >= 2.0:
            app = e["data"]["app"]
            if last_app is not None and app != last_app:
                r["dwell_switches"] += 1
            last_app = app
    seen: dict[int, set] = {}
    for e in ev["terminal"]:
        i = seg_at(sec(e["timestamp"]))
        r, d = rows[segs[i][2]], e["data"]
        r["cmds"] += 1
        if d["exit_code"] != 0:
            r["fails"] += 1
        if d["error_sig"] is not None:                       # a real error, not grep/Ctrl-C
            r["errors"] += 1
            if d["error_sig"] in seen.setdefault(i, set()):
                r["repeat_errors"] += 1
            seen[i].add(d["error_sig"])
    for h in result["help_requests"]:
        rows[segs[h["segment"]][2]]["chats"] += 1

    out = {}
    for s, r in rows.items():
        m = max(r["minutes"], 1e-9)
        out[s] = dict(
            minutes=round(r["minutes"], 1),
            presses_per_min=round(r["presses"] / m, 1),
            idle_pct=round(100 * r["idle_s"] / (m * 60), 1),
            window_events_per_min=round(r["switches"] / m, 2),
            dwell_switches_per_min=round(r["dwell_switches"] / m, 2),
            fail_pct=round(100 * r["fails"] / r["cmds"], 1) if r["cmds"] else None,
            repeat_error_pct=round(100 * r["repeat_errors"] / r["errors"], 1) if r["errors"] else None,
            afk_pct=round(100 * r["afk_s"] / (m * 60), 1),
            chats=r["chats"],
        )
    return out


def format_table(stats: dict) -> str:
    cols = ["minutes", "presses_per_min", "idle_pct", "window_events_per_min",
            "dwell_switches_per_min", "fail_pct", "repeat_error_pct", "afk_pct", "chats"]
    head = f"{'state':<11}" + "".join(f"{c:>24}" for c in cols)
    lines = [head]
    for s, r in stats.items():
        lines.append(f"{s:<11}" + "".join(f"{str(r[c]):>24}" for c in cols))
    return "\n".join(lines)
