"""Background noise for the synthetic day.

Calibrated against a real aw-watcher-window / aw-watcher-input trace:
  * the window watcher polls ~1 s, so most short events last ~0.96-1.05 s
  * most consecutive window events are the *same app* with a new title
    (animated terminal titles, unsaved-file markers, tab changes)
  * mouse deltas and scroll counts are heavy-tailed
  * idle bins contain tiny mouse nudges

The noise is applied *after* the clean state emitters, so ground-truth labels
are unchanged and `--noise 0` reproduces the clean day for ablations.
"""
from __future__ import annotations

import math
import random
from bisect import bisect_right

import numpy as np

from . import content as C
from . import world as W
from .categories import category

BIN = 5.0
# Flicks come from the user pressing cmd-tab, so they need presence (not while away) but their
# rate must NOT depend on flow/stuck/neutral: noise that carries the label is not noise.
STATE_MULT = {"flow": 1.0, "stuck": 1.0, "neutral": 1.0, "distracted": 0.0}
# Per-bin chance of a stray nudge (desk bump, unmute wiggle). Same for away and meetings on purpose:
# input alone cannot tell a Zoom call from an empty chair; the app in front has to.
STRAY_NUDGE_P = 0.003
BROWSERS = {"Google Chrome", "Safari"}
RAPID_FLICK_MULT = 2.5      # rapid coding overshoots cmd-tab far more often than steady work


def _flick_dur(rng: random.Random) -> float:
    r = rng.random()
    if r < 0.1:
        return rng.uniform(0.5, 0.9)                        # focus change caught between polls
    if r < 0.75:
        return 0.955 + rng.random() * 0.015                 # one watcher poll
    return min(0.96 + rng.lognormvariate(math.log(1.5), 0.8), 9.0)


def _seg_at(starts, segs, t):
    return segs[max(bisect_right(starts, t) - 1, 0)]


def _flick_target(rng, old, k, win):
    recents = [w for _, w in old[max(0, k - 6): k + 7]
               if w["app"] != win["app"] and w["app"] != C.LOCK_WIN["app"]]
    if recents and rng.random() < 0.6:
        return dict(rng.choice(recents))
    for _ in range(5):
        w = dict(rng.choice(C.NOISE_WINDOWS))
        if w["app"] != win["app"]:
            return w
    return dict(C.NOISE_WINDOWS[0])


def _overlays(rng, nprng, st, level, old, k, a, b, win, mult, rec, flick_starts, libs):
    """Candidate (start, end, window) replacements inside one focus interval."""
    ov: list[tuple[float, float, dict]] = []
    span = b - a
    if win["app"] == C.LOCK_WIN["app"] or span < 5:
        return ov

    # --- cmd-tab style focus flicks, sometimes a multi-app cycle
    if span >= 5 and mult > 0:
        n = int(nprng.poisson(st.flick_per_min * level * mult / 60.0 * span))
        for _ in range(n):
            s = rng.uniform(a + 1.0, max(a + 1.1, b - 1.5))
            hops = rng.randint(2, 4) if rng.random() < st.cluster_p else 1
            t, seen = s, {win["app"]}
            for _h in range(hops):
                w = _flick_target(rng, old, k, win)
                if w["app"] in seen:
                    continue
                seen.add(w["app"])
                d = _flick_dur(rng)
                ov.append((t, t + d, w))
                t += d
            flick_starts.append(s)

    # --- browser tab peeks and notification-badge titles
    if win["app"] in BROWSERS and span >= 10 and mult > 0:
        n = int(nprng.poisson(st.tab_peek_per_min * level * mult / 60.0 * span))
        for _ in range(n):
            s = rng.uniform(a + 2, max(a + 2.1, b - 14))
            pg = W.random_page(rng, libs)
            ov.append((s, s + rng.uniform(2, 12), C.browser_win(win["app"], pg["url"], pg["title"])))
        if rng.random() < 0.1 * level:
            s = rng.uniform(a + 1, max(a + 1.1, b - 8))
            e = min(b - 0.6, s + rng.uniform(5, 25))
            w = dict(win)
            w["title"] = f"({rng.randint(1, 5)}) {win['title']}"
            ov.append((s, e, w))

    # --- editor unsaved marker: title gets a dot between typing and the next save
    if win["app"] == "Code":
        for f in rec.fs:
            if f["data"]["kind"] == "save" and a + 5 < f["t"] < b:
                s = max(a, f["t"] - rng.uniform(8, 45))
                if f["t"] - s >= 4:
                    w = dict(win)
                    w["title"] = "● " + win["title"]
                    ov.append((s, f["t"], w))

    # --- animated terminal title (spinner) while a process runs
    if (win["app"] == "Terminal" and category(win) == "terminal"
            and rng.random() < min(1.0, st.title_churn_p * level)):
        t = a + rng.uniform(0.5, 3.0)
        stop = min(b - 0.6, t + rng.uniform(10, 45))
        i = rng.randrange(len(C.SPINNER))
        while t < stop:
            d = 0.955 + rng.random() * 0.02
            w = dict(win)
            w["title"] = f"{C.SPINNER[i % len(C.SPINNER)]} {win['title']}"
            ov.append((t, min(t + d, stop), w))
            t += d
            i += 1
    return ov


def splice_interval(a, b, win, ov, margin=0.4):
    """Replace parts of one focus interval [a, b) with overlay windows.

    Overlays are (start, end, window); those that overlap an earlier overlay, touch the
    interval edges (within `margin`) or are tiny are dropped.
    """
    res, cur = [], a
    for s, e, w in sorted(ov, key=lambda x: x[0]):
        if s < cur - 1e-9 or s < a + margin or e > b - margin or e - s < 0.3:
            continue
        if 1e-9 < s - cur < 0.05 or 1e-9 < b - e < 0.05:    # would leave a sliver of the base window
            continue
        if s > cur + 1e-9:
            res.append((cur, win))
        res.append((s, w))
        cur = e
    if cur < b - 1e-9:
        res.append((cur, win))
    return res


def splice_all(focus, length, overlays, margin=0.0):
    """Apply a global overlay list to a focus timeline; each overlay must sit in one interval."""
    ovs = sorted(overlays, key=lambda x: x[0])
    out, j = [], 0
    for k, (a, win) in enumerate(focus):
        b = focus[k + 1][0] if k + 1 < len(focus) else length
        mine = []
        while j < len(ovs) and ovs[j][0] < b - 1e-9:
            if ovs[j][0] >= a - 1e-9:
                mine.append(ovs[j])
            j += 1
        for t, w in splice_interval(a, b, win, mine, margin):
            if not out or out[-1][1] != w:
                out.append((t, w))
    return out


def noise_focus(rec, rng, nprng, st, segs, level, length):
    starts = [s[0] for s in segs]
    rapid = sorted(rec.rapid_spans)
    rapid_starts = [r[0] for r in rapid]

    def hopping(t):
        i = bisect_right(rapid_starts, t) - 1
        return i >= 0 and t < rapid[i][1]

    old = rec.focus
    new: list[tuple[float, dict]] = []
    flick_starts: list[float] = []
    for k, (a, win) in enumerate(old):
        b = old[k + 1][0] if k + 1 < len(old) else length
        seg = _seg_at(starts, segs, (a + b) / 2)
        mult = STATE_MULT[seg[2]] * (0.1 if seg[3] == "meeting" else 1.0)   # rarely cmd-tab mid-call
        if hopping((a + b) / 2):
            mult *= RAPID_FLICK_MULT
        ov = _overlays(rng, nprng, st, level, old, k, a, b, win, mult, rec, flick_starts, seg[4])
        for t, w in splice_interval(a, b, win, ov, 0.4):
            if not new or new[-1][1] != w:
                new.append((t, w))
    rec.focus = new
    return flick_starts


def _tiny_move(rec, rng, i):
    rec.dx[i] += rng.uniform(1, 60)
    if rng.random() < 0.6:
        rec.dy[i] += rng.uniform(1, 40)


def noise_input(rec, rng, nprng, st, segs, level, flick_starts):
    for t in flick_starts:                                  # the cmd-tab keystrokes themselves
        i = int(t // BIN)
        if 0 <= i < rec.n:
            rec.presses[i] += rng.choice([2, 2, 3])
    for s, e, state, sub, _libs in segs:
        i0, i1 = max(0, math.ceil(s / BIN)), min(rec.n, math.ceil(e / BIN))
        for i in range(i0, i1):
            idle = not (rec.presses[i] or rec.clicks[i] or rec.dx[i] or rec.dy[i]
                        or rec.sx[i] or rec.sy[i])
            if state == "distracted" or sub == "meeting":
                if rng.random() < STRAY_NUDGE_P * level:    # desk bump / unmute wiggle
                    _tiny_move(rec, rng, i)
                    if rng.random() < 0.2:
                        rec.clicks[i] += 1
            elif idle and rng.random() < st.jitter_p * level * (1.3 if state == "neutral" else 1.0):
                _tiny_move(rec, rng, i)
            if rec.presses[i] > 0 and rng.random() < 0.08 * level:   # reach for the mouse mid-typing
                rec.dx[i] += float(nprng.lognormal(math.log(st.mouse_scale * 0.5), 0.8))


def noise_terminal(rec, rng, nprng, level, length):
    """Quick shell commands: ls, cd, git status. A few benign non-zero exits."""
    for k, (a, win) in enumerate(rec.focus):
        if win["app"] != "Terminal" or "ssh://" in win["title"]:     # no zsh hook on remote hosts
            continue
        if category(win) in ("agent", "terminal_editor"):           # vim / Claude Code own the tab: no shell prompt
            continue
        b = rec.focus[k + 1][0] if k + 1 < len(rec.focus) else length
        if b - a < 6:
            continue
        n = int(nprng.poisson(level * (b - a) / 45.0))
        for _ in range(n):
            r = rng.random()
            code = 0 if r < 0.90 else 1 if r < 0.96 else 130  # grep no-match / Ctrl-C
            rec.term.append(dict(t=rng.uniform(a, b - 0.5), dur=rng.uniform(0.1, 3.0), data=dict(
                exit_code=code, cmd_kind=rng.choice(["shell", "shell", "git"]),
                error_sig=None, error_type=None)))


def apply_noise(rec, rng, nprng, st, segs, level, length):
    """segs: list of (start, end, state, subtype, project_libs) in seconds from day start."""
    if level <= 0:
        return
    noise_terminal(rec, rng, nprng, level, length)          # before titles get split into pieces
    flick_starts = noise_focus(rec, rng, nprng, st, segs, level, length)
    noise_input(rec, rng, nprng, st, segs, level, flick_starts)
