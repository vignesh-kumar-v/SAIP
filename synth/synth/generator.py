"""Synthetic ActivityWatch day generator.

A semi-Markov chain walks through four user states (flow, stuck, distracted,
neutral). Each state emits low-level events in the same shapes the real
watchers produce:

  window    {app, title, url?}                        (aw-watcher-window)
  afk       {status: afk | not-afk}                   (aw-watcher-afk, 180 s timeout)
  input     {presses, clicks, deltaX, deltaY,
             scrollX, scrollY} per 5 s bin            (aw-watcher-input; counts only)
  terminal  {exit_code, cmd_kind, error_sig}          (custom zsh hook; hashes only)
  fs        {kind: save | commit, file_id}            (custom file/git hook)

Ground truth (state per segment, plus when a chatbot was opened) is written
separately. The AFK stream is *derived* from the input stream the way the
real watcher behaves, so short pauses never show up as AFK.

This data exists to build and test the pipeline. It must not be used to
report classifier accuracy: the generator encodes our own idea of "stuck".
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import numpy as np

from . import content as C
from . import world as W
from .noise import apply_noise, splice_all
from .styles import UserStyle, sample_style

BIN = 5.0             # seconds, matches aw-watcher-input
AFK_TIMEOUT = 180.0   # seconds, aw-watcher-afk default
STATES = ("flow", "stuck", "distracted", "neutral")
GENERATOR_VERSION = "0.5"
MAX_PRESSES = 26      # real aw-watcher-input max per bin
HOST = "synthetic-macbook.local"

# Same id pattern, type and client as the real watchers, so one loader reads real and synthetic exports.
# Loaders should match buckets by *type* (and merge hosts): a hostname change on the real machine
# creates a second set of buckets.
BUCKETS = {
    "window": (f"aw-watcher-window_{HOST}", "currentwindow", "aw-watcher-window"),
    "afk": (f"aw-watcher-afk_{HOST}", "afkstatus", "aw-watcher-afk"),
    "input": (f"aw-watcher-input_{HOST}", "os.hid.input", "aw-watcher-input"),
    "web": (f"aw-watcher-web-chrome_{HOST}", "web.tab.current", "aw-client-web"),
    "terminal": (f"aw-watcher-terminal_{HOST}", "app.terminal.command", "aw-watcher-terminal"),   # custom hook
    "fs": (f"aw-watcher-fs_{HOST}", "app.fs.activity", "aw-watcher-fs"),                          # custom hook
}


# ------------------------------------------------------------------ data holders
@dataclass
class Segment:
    idx: int
    state: str
    subtype: str
    start: float
    end: float
    outcome: str | None = None
    sensitive: bool = False
    topic: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class Ctx:
    project: "W.Project"
    file: "W.File"
    projects: list = field(default_factory=list)
    last_sig: str | None = None
    stuck: object = None                      # W.Problem or W.NCProblem
    stuck_proj: "W.Project | None" = None
    stuck_ws: "W.Workspace | None" = None
    stuck_mode: str = "coding"
    mode: str = "coding"                      # coding | writing | analysis
    vim: bool = False                         # coding in vim/tmux inside a terminal
    remote: bool = False                      # ...over ssh: the local hooks cannot see it
    ws: "W.Workspace | None" = None
    unresolved: bool = False


class Recorder:
    def __init__(self, n_bins: int):
        self.n = n_bins
        self.presses = np.zeros(n_bins, dtype=np.int64)
        self.clicks = np.zeros(n_bins, dtype=np.int64)
        self.dx = np.zeros(n_bins)
        self.dy = np.zeros(n_bins)
        self.sx = np.zeros(n_bins, dtype=np.int64)
        self.sy = np.zeros(n_bins, dtype=np.int64)
        self.focus: list[tuple[float, dict]] = []
        self.term: list[dict] = []
        self.fs: list[dict] = []
        self.help: list[dict] = []
        self.pauses: list[tuple[float, float]] = []
        self.cmd_titles: list[tuple[float, float, dict]] = []
        self.hook_off = False       # remote session: no zsh/fs hook events
        self.no_titles = False      # terminal-only editing: window title never shows the command

    def focus_to(self, t: float, win: dict) -> None:
        if self.focus and self.focus[-1][1] == win:
            return
        self.focus.append((t, dict(win)))


# ------------------------------------------------------------------ input profiles
def profile(s: UserStyle, mode: str, kind: str) -> dict:
    r = s.typing_rate

    def P(rate, duty, burst, click, mouse, scroll):
        return dict(rate=rate, duty=duty, burst=burst, click=click,
                    mouse_p=min(mouse, 0.95), scroll_p=min(scroll, 0.95),
                    mouse_scale=s.mouse_scale, type_mouse_p=s.type_mouse_p, scroll_x_p=s.scroll_x_p)

    stuck = mode == "stuck"
    if kind == "editor":
        if stuck:
            return P(r * s.stuck_type_mult, s.stuck_duty, s.stuck_burst, 0.4, s.mouse_p * 1.4, s.scroll_p * 0.8)
        return P(r, s.flow_duty, s.burst_bins, 0.15, s.mouse_p * 0.6, s.scroll_p * 0.3)
    if kind == "terminal":
        return P(r * (0.35 if stuck else 0.5), 0.15 if stuck else 0.25, 2, 0.05,
                 s.mouse_p * 0.3, s.scroll_p * 0.2)
    if kind == "read":
        return P(0, 0, 1, 0.02, s.mouse_p * 0.25, s.scroll_p * 0.5)
    if kind == "browser":
        if stuck:
            return P(r * 0.6, 0.12, 2, 0.8, s.mouse_p * 1.8, 0.7)
        return P(r * 0.6, 0.10, 2, 0.5, s.mouse_p * 1.5, 0.5)
    if kind == "doc":                                         # writing surface
        if stuck:
            return P(r * s.stuck_type_mult * 0.9, s.stuck_duty * 0.9, s.stuck_burst, 0.3, s.mouse_p * 1.2, s.scroll_p)
        return P(r, s.flow_duty, s.burst_bins * 1.2, 0.12, s.mouse_p * 0.5, s.scroll_p * 0.3)
    if kind == "sheet":                                       # spreadsheet: clicks and mouse heavy
        if stuck:
            return P(r * 0.45 * s.stuck_type_mult, 0.25, 3, 1.0, s.mouse_p * 2.0, 0.5)
        return P(r * 0.55, 0.35, 3, 0.8, s.mouse_p * 1.7, s.scroll_p * 0.3)
    if kind == "notebook":
        if stuck:
            return P(r * 0.7 * s.stuck_type_mult, s.stuck_duty, 3, 0.5, s.mouse_p * 1.3, s.scroll_p * 0.8)
        return P(r * 0.9, s.flow_duty * 0.8, 5, 0.35, s.mouse_p * 0.8, s.scroll_p * 0.5)
    if kind == "notes":
        return P(r * 0.8, 0.3, 4, 0.15, s.mouse_p * 0.5, s.scroll_p * 0.2)
    if kind == "vim":                                         # modal editing in a terminal: no mouse
        if stuck:
            return P(r * s.stuck_type_mult * 0.85, s.stuck_duty, s.stuck_burst, 0.02, s.mouse_p * 0.15, s.scroll_p * 0.05)
        return P(r * 0.85, s.flow_duty, s.burst_bins, 0.02, s.mouse_p * 0.1, s.scroll_p * 0.05)
    if kind == "inline_chat":                                 # prompt typed into an in-editor AI panel
        return P(r * 1.3, 0.55, 6, 0.1, s.mouse_p * 0.4, s.scroll_p * 0.1)
    if kind == "reading":                                     # scroll-heavy, almost no typing
        return P(r * 0.05, 0.02, 1, 0.3, s.mouse_p * 0.9, s.scroll_p * 3.5)
    if kind == "comment":                                     # short typed note while reviewing
        return P(r * 0.9, 0.55, 5, 0.2, s.mouse_p * 0.6, s.scroll_p * 0.1)
    if kind == "watch":                                       # watching agent output scroll by
        return P(0, 0, 1, 0.08, s.mouse_p * 0.4, s.scroll_p * 1.2)
    if kind == "wait":                                        # long build/training run: present but idle
        return P(0, 0, 1, 0.03, s.mouse_p * 0.2, s.scroll_p * 0.3)
    if kind == "chat":
        return P(r * 1.4, 0.6, 8, 0.3, s.mouse_p, s.scroll_p)
    if kind == "browse":
        return P(r * 0.5, 0.06, 2, 0.5, s.mouse_p * 1.6, 0.45)
    if kind == "email":
        return P(r * 0.9, 0.3, 4, 0.25, s.mouse_p, s.scroll_p * 0.5)
    if kind == "pdf":
        return P(0, 0, 1, 0.1, s.mouse_p * 0.8, 0.6)
    if kind == "video":
        return P(0, 0, 1, 0.03, s.mouse_p * 0.3, 0.0)
    if kind == "msg":
        return P(r * 0.9, 0.45, 5, 0.2, s.mouse_p * 0.5, s.scroll_p * 0.3)
    return P(0, 0, 1, 0, 0, 0)  # meeting / away / idle


def fill(rec: Recorder, rng: random.Random, nprng: np.random.Generator,
         a: float, b: float, p: dict) -> None:
    """Write input counts into the 5 s bins whose start lies in [a, b)."""
    i0 = max(0, math.ceil(a / BIN))
    i1 = min(rec.n, math.ceil(b / BIN))
    if i1 <= i0:
        return
    rate, duty = p["rate"], p["duty"]
    typing = rate > 0 and duty > 0
    leave = 1.0 / max(p["burst"], 1.0)
    join = 1.0 if duty >= 0.99 else min(1.0, leave * duty / (1.0 - duty)) if typing else 0.0
    on = typing and rng.random() < duty
    for i in range(i0, i1):
        typed = False
        if on:
            if rng.random() < 0.3:                            # partial bin: burst edge, shortcut, arrow keys
                rec.presses[i] += rng.randint(1, 5)
            else:
                rec.presses[i] += min(26, nprng.poisson(rate * rng.uniform(0.6, 1.4)))  # real p99 ~24
            typed = True
            if rng.random() < leave:
                on = False
        elif typing and rng.random() < join:
            on = True
        elif typing and rng.random() < 0.04:                  # stray shortcut (cmd-s, cmd-c) between bursts
            rec.presses[i] += rng.randint(1, 3)
            typed = True
        if p["click"] > 0:
            rec.clicks[i] += nprng.poisson(p["click"])
        # aw-watcher-input stores magnitudes: deltaX/deltaY/scrollX/scrollY are never negative
        if rng.random() < p["mouse_p"] or (typed and rng.random() < p["type_mouse_p"]):
            rec.dx[i] += float(nprng.lognormal(math.log(p["mouse_scale"]), 0.9))
            rec.dy[i] += float(nprng.lognormal(math.log(p["mouse_scale"] * 0.55), 0.9))
        if rng.random() < p["scroll_p"]:                      # trackpad inertia: long tail
            rec.sy[i] += int(min(3000, nprng.lognormal(math.log(40), 1.3)))
            if rng.random() < p["scroll_x_p"]:
                rec.sx[i] += int(min(1500, nprng.lognormal(math.log(8), 1.6)))


class Player:
    """Plays consecutive focus intervals inside one segment."""

    def __init__(self, rec, rng, nprng, style, mode, start, end):
        self.rec, self.rng, self.nprng, self.style = rec, rng, nprng, style
        self.mode, self.t, self.end = mode, start, end
        self.win: dict | None = None

    @property
    def done(self) -> bool:
        return self.t >= self.end - 1e-9

    def run(self, kind: str, dur: float, win: dict, mode: str | None = None):
        a = self.t
        b = min(a + max(dur, 0.0), self.end)
        if b <= a:
            return None
        self.rec.focus_to(a, win)
        fill(self.rec, self.rng, self.nprng, a, b, profile(self.style, mode or self.mode, kind))
        self.t, self.win = b, win
        return a, b


# ------------------------------------------------------------------ small emitters
def add_command(rec, rng, iv, cmd_kind, exit_code, sig, etype=None, cmd=None, proj=None):
    """Terminal hook event (hash + exception class only) and the running-process title."""
    if rec.hook_off:                                          # remote host: the local hook sees nothing
        return
    a, b = iv
    dur = min(b - a, rng.uniform(1.0, 20.0))
    t = a + rng.uniform(0.0, max(0.0, (b - a) - dur))
    rec.term.append(dict(t=t, dur=dur, data=dict(
        exit_code=exit_code, cmd_kind=cmd_kind, error_sig=sig, error_type=etype)))
    if cmd and proj and dur >= 1.0 and not rec.no_titles:
        rec.cmd_titles.append((t, t + dur, {"app": "Terminal", "title": W.terminal_title(proj, cmd)}))


def add_saves(rec, rng, style, a, b, proj, file, force_end=False):
    if rec.hook_off:
        return
    fid = W.file_id(proj, file.path)
    t = a + rng.expovariate(1.0 / style.save_every_s)
    while t < b:
        rec.fs.append(dict(t=t, dur=0.0, data=dict(kind="save", file_id=fid)))
        t += rng.expovariate(1.0 / style.save_every_s)
    if force_end:
        rec.fs.append(dict(t=max(a, b - rng.uniform(0.5, 3.0)), dur=0.0,
                           data=dict(kind="save", file_id=fid)))


def add_commit(rec, rng, iv, proj):
    if rec.hook_off:
        return
    rec.fs.append(dict(t=iv[1] - rng.uniform(0.0, 1.0), dur=0.0,
                       data=dict(kind="commit", file_id=None)))
    add_command(rec, rng, iv, "git", 0, None, None, "git commit -m wip", proj)


def open_chat(rec, pl, rng, seg, kind, chat_dur):
    cw = rng.choice(C.CHAT_WINDOWS)
    iv = pl.run("chat", chat_dur * 0.6, cw)
    if iv:
        rec.help.append(dict(t=iv[0], kind=kind, segment=seg.idx, win=dict(cw)))
        pl.run("read", chat_dur * 0.4, cw)
    return cw


def help_open(rec, pl, rng, seg, kind, chat_dur, st, work_win):
    """Ask an AI for help: in a separate chat window, or inside the work app (Copilot, Cursor, Gemini)
    where the window never changes and only the typing/reading rhythm hints at it."""
    if rng.random() < st.inline_ai_p:
        iv = pl.run("inline_chat", chat_dur * 0.5, work_win)
        if iv:
            rec.help.append(dict(t=iv[0], kind=kind + "_inline", segment=seg.idx, win=dict(work_win)))
            pl.run("read", chat_dur * 0.5, work_win)
        return work_win
    return open_chat(rec, pl, rng, seg, kind, chat_dur)


def agent_stint(rec, pl, rng, proj, task, dur, kind="watch"):
    """Claude Code working in a terminal tab: the title flips ◐/◑ about once a second, then ✳ when it is done."""
    base = {"app": "Terminal", "title": W.agent_title(proj, W.AGENT_IDLE, task)}
    iv = pl.run(kind, dur, base)
    if iv:
        t, i = iv[0] + rng.uniform(0.3, 2.0), rng.randrange(2)
        stop = iv[1] - rng.uniform(1.0, 4.0)                   # ends idle (✳), waiting for the user
        while t < stop:
            d = 0.96 + (rng.random() * 0.12 if rng.random() < 0.85 else rng.uniform(0.5, 2.5))
            rec.cmd_titles.append((t, min(t + d, stop),
                                   {"app": "Terminal", "title": W.agent_title(proj, W.AGENT_SPIN[i % 2], task)}))
            t += d
            i += 1
    return iv, base


class CodeUI:
    """How coding looks on screen: VS Code plus a terminal window, or vim in a terminal (maybe over ssh)."""

    def __init__(self, proj: str, vim: bool, remote: bool):
        self.proj, self.vim, self.remote = proj, vim, remote
        self.ekind = "vim" if vim else "editor"

    def editor(self, f):
        if not self.vim:
            return C.editor_win(self.proj, f.name)
        where = f"ssh://gpu-box/{self.proj}" if self.remote else self.proj
        return {"app": "Terminal", "title": f"{f.name} ({where}) - VIM"}

    def term(self, f):
        return self.editor(f) if self.vim else term_win(self.proj)


def bwin(app, page):
    return C.browser_win(app, page["url"], page["title"])


def term_win(proj_name):
    return {"app": "Terminal", "title": W.terminal_title(proj_name)}


# ------------------------------------------------------------------ state emitters
BROWSERS = ["Google Chrome", "Safari"]


def nc_kind(ws):
    return "doc" if ws.kind == "writing" else ("notebook" if ws.libs else "sheet")


def nc_run(pl, rec, rng, st, ws, kind, dur):
    iv = pl.run(kind, dur, ws.win)
    if iv and ws.saves:
        add_saves_nc(rec, rng, st, iv[0], iv[1], ws)
    return iv


def add_saves_nc(rec, rng, st, a, b, ws, force_end=False):
    fid = W.file_id(ws.name, ws.fmt)
    t = a + rng.expovariate(1.0 / (st.save_every_s * 1.5))
    while t < b:
        rec.fs.append(dict(t=t, dur=0.0, data=dict(kind="save", file_id=fid)))
        t += rng.expovariate(1.0 / (st.save_every_s * 1.5))
    if force_end:
        rec.fs.append(dict(t=max(a, b - rng.uniform(0.5, 3.0)), dur=0.0, data=dict(kind="save", file_id=fid)))


def emit_flow(seg, rec, rng, nprng, st, ctx):
    if seg.subtype == "review":
        return emit_review(seg, rec, rng, nprng, st, ctx)
    return (emit_flow_coding if ctx.mode == "coding" else emit_flow_nc)(seg, rec, rng, nprng, st, ctx)


def emit_flow_coding(seg, rec, rng, nprng, st, ctx):
    pl = Player(rec, rng, nprng, st, "flow", seg.start, seg.end)
    pj = ctx.project
    proj = pj.name
    ui = CodeUI(proj, ctx.vim, ctx.remote)
    rec.hook_off, rec.no_titles = ui.remote, ui.vim
    seg.meta = dict(mode="coding", project=proj, stack=pj.stack, libs=pj.libs, vim=ui.vim, remote=ui.remote)
    dur = seg.end - seg.start
    chat_at = seg.start + dur * rng.uniform(0.2, 0.8) if rng.random() < st.incidental_chat_p else None
    app = rng.choice(BROWSERS)
    cmd_kinds = list(W.STACKS[pj.stack]["cmds"])
    tdd = None                                               # red-green loop: same failure repeats while working
    task = W.agent_task(rng)
    while not pl.done:
        if chat_at is not None and pl.t >= chat_at:
            help_open(rec, pl, rng, seg, "incidental", rng.uniform(30, 90), st, ui.editor(ctx.file))
            chat_at = None
            continue
        dwell = min(max(rng.expovariate(st.flow_switch_per_min / 60.0), 40.0), 600.0)
        iv = pl.run(ui.ekind, dwell, ui.editor(ctx.file))
        if iv:
            add_saves(rec, rng, st, iv[0], iv[1], proj, ctx.file)
        if pl.done:
            break
        roll = rng.random()
        if roll < 0.62 and not ui.remote and rng.random() < st.agent_flow_p:
            # drive Claude Code: type an instruction, watch it work, approve a step
            pl.run("chat", rng.uniform(8, 30), {"app": "Terminal", "title": W.agent_title(proj, W.AGENT_IDLE, task)})
            agent_stint(rec, pl, rng, proj, task, rng.uniform(20, 120))
            if rng.random() < 0.3:
                task = W.agent_task(rng)
        elif roll < 0.62:
            iv = pl.run("terminal", rng.uniform(8, 40), ui.term(ctx.file))
            if iv:
                mod = ctx.file.module or None
                if rng.random() < 0.12:
                    add_commit(rec, rng, iv, proj)
                elif tdd and tdd["left"] > 0:
                    tdd["left"] -= 1
                    add_command(rec, rng, iv, tdd["kind"], 1, tdd["sig"], tdd["etype"],
                                W.cmd_string(rng, pj, tdd["kind"], mod), proj)
                elif rng.random() < st.err_flow:
                    etype, kind, sig, msg, lib = W.random_error(rng, pj)
                    if sig == ctx.last_sig:
                        add_command(rec, rng, iv, kind, 0, None, None, W.cmd_string(rng, pj, kind, mod), proj)
                    else:
                        add_command(rec, rng, iv, kind, 1, sig, etype, W.cmd_string(rng, pj, kind, mod), proj)
                        ctx.last_sig = sig
                        if rng.random() < st.tdd_p:
                            tdd = dict(etype=etype, kind=kind, sig=sig, left=rng.randint(1, 3))
                        elif rng.random() < 0.35 and not pl.done:
                            # quick search for the error, fixed in a minute: the same pages a stuck user opens
                            pg = rng.choice(W.error_pages(rng, pj, etype, msg, lib, n_so=(1, 2)))
                            pl.run("browser", rng.uniform(10, 50), bwin(app, pg))
                else:
                    kind = rng.choice(cmd_kinds)
                    add_command(rec, rng, iv, kind, 0, None, None, W.cmd_string(rng, pj, kind, mod), proj)
        elif roll < 0.90:
            pl.run("browser", rng.uniform(10, 60), bwin(app, W.lookup_page(rng, pj)))
        if rng.random() < 0.35:
            ctx.file = W.next_file(rng, pj, ctx.file)         # module <-> test, neighbours
    for _ in range(int(nprng.poisson(dur / 60.0 / 15.0))):
        a = seg.start + rng.uniform(0.1, 0.9) * dur
        rec.pauses.append((a, min(seg.end, a + rng.uniform(8, 40))))
    ctx.unresolved = False


def emit_flow_nc(seg, rec, rng, nprng, st, ctx):
    """Writing / analysis flow: a work surface that is not an IDE and has no terminal."""
    pl = Player(rec, rng, nprng, st, "flow", seg.start, seg.end)
    ws = ctx.ws
    kind = nc_kind(ws)
    seg.meta = dict(mode=ws.kind, workspace=ws.name, fmt=ws.fmt, libs=ws.libs)
    dur = seg.end - seg.start
    chat_at = seg.start + dur * rng.uniform(0.2, 0.8) if rng.random() < st.incidental_chat_p else None
    app = rng.choice(BROWSERS)
    while not pl.done:
        if chat_at is not None and pl.t >= chat_at:
            help_open(rec, pl, rng, seg, "incidental", rng.uniform(30, 90), st, ws.win)
            chat_at = None
            continue
        dwell = min(max(rng.expovariate(st.flow_switch_per_min / 60.0), 40.0), 600.0)
        nc_run(pl, rec, rng, st, ws, kind, dwell)
        if pl.done:
            break
        roll = rng.random()
        if roll < 0.5:
            pl.run("browser", rng.uniform(10, 70), bwin(app, W.nc_page(rng, ws)))
        elif roll < 0.65:
            pl.run("pdf", rng.uniform(30, 120), W.pdf_win(rng))
    for _ in range(int(nprng.poisson(dur / 60.0 / 10.0))):    # thinking pauses are common when writing
        a = seg.start + rng.uniform(0.1, 0.9) * dur
        rec.pauses.append((a, min(seg.end, a + rng.uniform(8, 45))))
    ctx.unresolved = False


def emit_stuck(seg, rec, rng, nprng, st, ctx):
    mode = ctx.stuck_mode if (seg.subtype == "return_to_error" and ctx.stuck) else ctx.mode
    return (emit_stuck_coding if mode == "coding" else emit_stuck_nc)(seg, rec, rng, nprng, st, ctx)


def emit_stuck_coding(seg, rec, rng, nprng, st, ctx):
    if seg.subtype == "return_to_error" and ctx.stuck:
        pr, pj = ctx.stuck, ctx.stuck_proj
    else:
        pj = ctx.stuck_proj = ctx.project
        pr = ctx.stuck = W.make_problem(rng, pj, silent=rng.random() < st.silent_stuck_p)
        pr.vim, pr.remote = ctx.vim, ctx.remote
    ctx.stuck_mode = "coding"
    proj = pj.name
    ui = CodeUI(proj, pr.vim, pr.remote)
    rec.hook_off, rec.no_titles = ui.remote, ui.vim
    kw = [pr.lib, pr.symptom[:24]] if pr.silent else [pr.etype, pr.msg[:24], pr.lib]
    seg.meta = dict(mode="coding", project=proj, stack=pj.stack, libs=pj.libs, vim=ui.vim, remote=ui.remote,
                    variant="logic_bug" if pr.silent else "error", error_type=pr.etype,
                    error_msg=pr.msg, error_sigs=[] if pr.silent else [pr.sig] + ([pr.sig2] if pr.sig2 else []),
                    lib=pr.lib, cmd_kind=pr.cmd_kind, suspects=[f.name for f in pr.suspects], keywords=kw)
    pl = Player(rec, rng, nprng, st, "stuck", seg.start, seg.end)
    cur = pr.suspects[0]
    pace, outcome = st.stuck_pace, seg.outcome
    edit_x = max(0.5, st.stuck_type_mult) ** 1.2       # thrashers edit and rerun a lot, stallers stare and search
    chat_dur = rng.uniform(45, 110)
    reserve = {"help": chat_dur + 55, "resolved": 50, "abandoned": 0}[outcome]

    def cap(d):
        return max(0.0, min(d, seg.end - reserve - pl.t))

    if seg.subtype == "return_to_error":
        pl.run(ui.ekind, cap(rng.uniform(4, 12)), ui.editor(cur))   # glance at the file, then rerun
    first = True
    while first or seg.end - reserve - pl.t > 25:
        first = False
        iv = pl.run("terminal", cap(rng.uniform(6, 25) * pace), ui.term(cur))
        if iv:
            if pr.silent:                                    # runs fine, output is just wrong
                add_command(rec, rng, iv, pr.cmd_kind, 1 if rng.random() < 0.25 else 0, None, None,
                            pr.cmd_str, proj)
            else:
                sig = pr.sig2 if pr.sig2 and rng.random() < 0.1 else pr.sig
                add_command(rec, rng, iv, pr.cmd_kind, 1, sig, pr.etype, pr.cmd_str, proj)
        pl.run("read", cap(rng.uniform(5, 35) * pace / edit_x), ui.term(cur))     # idle gap after a run
        if rng.random() < 0.55 / edit_x ** 0.5:
            pl.run("browser", cap(rng.uniform(20, 110) * pace), bwin(pr.browser, rng.choice(pr.pages)))
        if len(pr.suspects) > 1 and rng.random() < 0.6:
            cur = rng.choice(pr.suspects)                            # hop between suspect files
        iv = pl.run(ui.ekind, cap(rng.uniform(20, 90) * pace * edit_x), ui.editor(cur))
        if iv:
            add_saves(rec, rng, st, iv[0], iv[1], proj, cur, force_end=True)
        if cap(1.0) <= 0:
            break

    def success():
        iv = pl.run(ui.ekind, rng.uniform(10, 30), ui.editor(cur))
        if iv:
            add_saves(rec, rng, st, iv[0], iv[1], proj, cur, force_end=True)
        iv = pl.run("terminal", rng.uniform(8, 25), ui.term(cur))
        if iv:
            add_command(rec, rng, iv, pr.cmd_kind, 0, None, None, pr.cmd_str, proj)
            if rng.random() < 0.4:
                add_commit(rec, rng, (iv[1], iv[1] + 2), proj)

    if outcome == "help" and not pl.done:
        help_open(rec, pl, rng, seg, "stuck_help", chat_dur, st, ui.editor(cur))
        if not pl.done:
            success()
    elif outcome == "resolved" and not pl.done:
        success()
    if not pl.done:                                  # stare at whatever is left
        pl.run("read", seg.end - pl.t, pl.win or ui.term(cur))
    ctx.unresolved = outcome == "abandoned"


def emit_stuck_nc(seg, rec, rng, nprng, st, ctx):
    """Stuck while writing or analysing: no terminal, no error text. Revisits and idle gaps only."""
    if seg.subtype == "return_to_error" and ctx.stuck and ctx.stuck_mode != "coding":
        pr, ws = ctx.stuck, ctx.stuck_ws
    else:
        ws = ctx.stuck_ws = ctx.ws
        pr = ctx.stuck = W.make_nc_problem(rng, ws)
    ctx.stuck_mode = ws.kind
    kind = nc_kind(ws)
    seg.meta = dict(mode=ws.kind, workspace=ws.name, fmt=ws.fmt, libs=ws.libs, topic=pr.topic,
                    keywords=[pr.topic[:24]])
    pl = Player(rec, rng, nprng, st, "stuck", seg.start, seg.end)
    pace, outcome = st.stuck_pace, seg.outcome
    edit_x = max(0.5, st.stuck_type_mult) ** 1.2
    chat_dur = rng.uniform(45, 110)
    reserve = {"help": chat_dur + 55, "resolved": 50, "abandoned": 0}[outcome]

    def cap(d):
        return max(0.0, min(d, seg.end - reserve - pl.t))

    if seg.subtype == "return_to_error":
        nc_run(pl, rec, rng, st, ws, kind, cap(rng.uniform(4, 12)))
    first = True
    while first or seg.end - reserve - pl.t > 25:
        first = False
        nc_run(pl, rec, rng, st, ws, kind, cap(rng.uniform(20, 90) * pace * edit_x))   # try a few lines
        pl.run("read", cap(rng.uniform(5, 35) * pace / edit_x), ws.win)                # stare at the draft
        for _ in range(rng.randint(1, 3)):                                             # search, revisit sources
            if rng.random() < 0.75 / edit_x ** 0.5:
                pl.run("browser", cap(rng.uniform(15, 100) * pace), bwin(pr.browser, rng.choice(pr.pages)))
        if rng.random() < 0.2:
            pl.run("pdf", cap(rng.uniform(20, 80)), W.pdf_win(rng))
        if cap(1.0) <= 0:
            break
    if outcome == "help" and not pl.done:
        help_open(rec, pl, rng, seg, "stuck_help", chat_dur, st, ws.win)
        if not pl.done:
            nc_run(pl, rec, rng, st, ws, kind, rng.uniform(20, 50))
    elif outcome == "resolved" and not pl.done:
        nc_run(pl, rec, rng, st, ws, kind, rng.uniform(20, 50))
    if not pl.done:
        pl.run("read", seg.end - pl.t, pl.win or ws.win)
    ctx.unresolved = outcome == "abandoned"


def emit_review(seg, rec, rng, nprng, st, ctx):
    """Flow without typing: code review, reading a paper, studying. Scroll-heavy with long dwells."""
    pl = Player(rec, rng, nprng, st, "flow", seg.start, seg.end)
    app = rng.choice(BROWSERS)
    dur = seg.end - seg.start
    chat_at = seg.start + dur * rng.uniform(0.2, 0.8) if rng.random() < st.incidental_chat_p * 0.5 else None

    def chat_check():
        nonlocal chat_at
        if chat_at is not None and pl.t >= chat_at:
            open_chat(rec, pl, rng, seg, "incidental", rng.uniform(30, 90))
            chat_at = None

    if ctx.mode == "coding":
        pj = ctx.project
        ui = CodeUI(pj.name, ctx.vim, ctx.remote)
        rec.hook_off, rec.no_titles = ui.remote, ui.vim
        seg.meta = dict(mode="coding", project=pj.name, stack=pj.stack, libs=pj.libs, variant="code_review",
                        vim=ui.vim, remote=ui.remote)
        f = ctx.file
        prs = [W.pr_page(rng, pj) for _ in range(rng.randint(1, 3))]        # the few PRs under review
        while not pl.done:
            chat_check()
            pg = rng.choice(prs)
            pl.run("reading", rng.uniform(60, 240), bwin(app, pg))             # read the diff on GitHub
            if rng.random() < 0.5:
                f = W.next_file(rng, pj, f)
                pl.run("reading", rng.uniform(30, 150), ui.editor(f))           # read the code locally
            if rng.random() < 0.35:
                pl.run("comment", rng.uniform(10, 40), bwin(app, pg))           # leave a review comment
    else:
        ws = ctx.ws
        seg.meta = dict(mode=ws.kind, workspace=ws.name, fmt=ws.fmt, libs=ws.libs, variant="reading")
        while not pl.done:
            chat_check()
            pl.run("reading", rng.uniform(90, 300), W.pdf_win(rng))
            if rng.random() < 0.4:
                pl.run("notes", rng.uniform(20, 80), W.notes_win(rng))
            if rng.random() < 0.3:
                pl.run("reading", rng.uniform(30, 120), bwin(app, W.browse_page(rng, "research")))
    ctx.unresolved = False


def emit_waiting(seg, rec, rng, nprng, st, ctx):
    """Present but idle: a long build/training run, or supervising an AI coding agent."""
    pl = Player(rec, rng, nprng, st, "neutral", seg.start, seg.end)
    pj = ctx.project
    proj = pj.name
    ui = CodeUI(proj, ctx.vim, ctx.remote)
    rec.hook_off, rec.no_titles = ui.remote, ui.vim
    variant = "agent" if rng.random() < st.agent_p else "build"
    seg.meta = dict(mode="coding", project=proj, stack=pj.stack, libs=pj.libs, variant="wait_" + variant,
                    vim=ui.vim, remote=ui.remote)
    app = rng.choice(BROWSERS)
    lead = rng.uniform(1, 6)
    t0 = seg.start + lead
    run = (seg.end - t0) * rng.uniform(0.8, 1.0)
    if variant == "build":
        cmd = W.long_cmd(rng, pj)
        data = dict(exit_code=0, cmd_kind="build", error_sig=None, error_type=None)
        if rng.random() > 0.85:                                           # it fails at the end
            etype, _k, sig, _m, _l = W.random_error(rng, pj)
            data.update(exit_code=1, error_sig=sig, error_type=etype)
        if not rec.hook_off:
            rec.term.append(dict(t=t0, dur=run, data=data))
        base = ui.term(ctx.file) if ui.vim else {"app": "Terminal", "title": W.terminal_title(proj, cmd)}
        pl.run("wait", lead, ui.term(ctx.file))                          # launching it
        while not pl.done:
            pl.run("wait", rng.uniform(40, 200), base)
            r = rng.random()
            if r < 0.35:
                pl.run("browse", rng.uniform(20, 80), bwin(app, W.lookup_page(rng, pj)))
            elif r < 0.55:
                pl.run("email", rng.uniform(15, 60), W.slack_win(rng))
    else:
        task = W.agent_task(rng)
        base = {"app": "Terminal", "title": W.agent_title(proj, W.AGENT_IDLE, task)}
        if not rec.hook_off:
            rec.term.append(dict(t=t0, dur=run, data=dict(exit_code=0, cmd_kind="agent", error_sig=None,
                                                          error_type=None)))
        pl.run("watch", lead, ui.term(ctx.file))
        while not pl.done:
            agent_stint(rec, pl, rng, proj, task, rng.uniform(25, 100))  # agent works, output scrolls
            r = rng.random()
            if r < 0.4:                                                   # approve a tool call
                iv = pl.run("watch", rng.uniform(3, 10), base)
                if iv:
                    rec.presses[min(rec.n - 1, int(iv[0] // BIN))] += rng.randint(1, 3)
            elif r < 0.6:
                pl.run("chat", rng.uniform(10, 40), base)                 # type the next instruction
            elif r < 0.8:
                pl.run("reading", rng.uniform(20, 90), ui.editor(ctx.file))   # review the diff
            else:
                pl.run("browse", rng.uniform(15, 60), bwin(app, W.lookup_page(rng, pj)))
    ctx.unresolved = False


def emit_distracted(seg, rec, rng, nprng, st, ctx):
    dur = seg.end - seg.start
    if seg.subtype == "lunch" or (dur > 480 and rng.random() < 0.6):
        rec.focus_to(seg.start, C.LOCK_WIN)          # input stays zero; nothing else to do


def emit_neutral(seg, rec, rng, nprng, st, ctx):
    if seg.subtype == "waiting":
        return emit_waiting(seg, rec, rng, nprng, st, ctx)
    pl = Player(rec, rng, nprng, st, "neutral", seg.start, seg.end)
    sub = seg.subtype
    dur = seg.end - seg.start
    chat_at = (seg.start + dur * rng.uniform(0.2, 0.8)
               if sub in ("browsing", "email", "study") and rng.random() < st.incidental_chat_p * 0.7 else None)

    def chat_check():
        nonlocal chat_at
        if chat_at is not None and pl.t >= chat_at:
            open_chat(rec, pl, rng, seg, "incidental", rng.uniform(30, 90))
            chat_at = None

    if sub == "meeting":
        pl.run("meeting", dur, W.zoom_win(rng))
    elif sub == "pdf":
        while not pl.done:
            pl.run("pdf", rng.uniform(60, 240), W.pdf_win(rng))
    elif sub == "study":                                       # notes + reading: typing without being "work"
        while not pl.done:
            chat_check()
            pl.run("notes", rng.uniform(40, 150), W.notes_win(rng))
            pl.run("pdf", rng.uniform(30, 120), W.pdf_win(rng))
            if rng.random() < 0.4:
                pl.run("browse", rng.uniform(20, 80), bwin(rng.choice(BROWSERS), W.browse_page(rng, "research")))
    elif sub == "msg":
        while not pl.done:
            pl.run("msg", rng.uniform(40, 150), rng.choice(C.SENSITIVE_WINDOWS))
            if rng.random() < 0.3:
                pl.run("browse", rng.uniform(10, 40), bwin("Google Chrome", W.browse_page(
                    rng, W._wchoice(rng, W.TOPICS))))
    elif sub == "email":
        mail = W.mail_win(rng)                      # counts only change between sessions
        while not pl.done:
            chat_check()
            pl.run("email", rng.uniform(60, 200), mail)
            pl.run("email", rng.uniform(15, 60), W.slack_win(rng))
    else:
        app = rng.choice(BROWSERS)
        seg.meta = dict(topic=seg.topic)
        while not pl.done:
            chat_check()
            topic = seg.topic if rng.random() < 0.75 else W._wchoice(rng, W.TOPICS)
            if topic == "video":
                pl.run("video", rng.uniform(120, 420), bwin(app, W.browse_page(rng, topic)))
            else:
                pl.run("browse", rng.uniform(30, 150), bwin(app, W.browse_page(rng, topic)))
    ctx.unresolved = False


EMITTERS = {"flow": emit_flow, "stuck": emit_stuck,
            "distracted": emit_distracted, "neutral": emit_neutral}


# ------------------------------------------------------------------ timeline
def lognorm_min(rng, mean_min, lo, hi, sigma=0.5):
    mu = math.log(mean_min) - sigma ** 2 / 2
    return min(max(rng.lognormvariate(mu, sigma), lo), hi) * 60.0


def wchoice(rng, pairs):
    r, acc = rng.random() * sum(w for _, w in pairs), 0.0
    for v, w in pairs:
        acc += w
        if r <= acc:
            return v
    return pairs[-1][0]


MODE_W = [("coding", .5), ("writing", .3), ("analysis", .2)]


def next_segment(rng, st, prev, ctx, t, lunched, length):
    """Pick (state, subtype, duration_s, outcome) for the next segment."""
    if prev is None:
        return "neutral", "email", rng.uniform(4, 10) * 60.0, None
    if not lunched and 3 * 3600 <= t <= 4.5 * 3600 and rng.random() < 0.85:
        return "distracted", "lunch", rng.uniform(30, 55) * 60.0, None
    fatigue = 1.0 + 0.6 * t / length                      # afternoons get harder and more distracted
    if prev.state == "flow":
        state = wchoice(rng, [("stuck", st.stuck_rate * fatigue), ("neutral", .28),
                              ("distracted", .22 * fatigue), ("flow", .2)])
    elif prev.state == "stuck":
        state = (wchoice(rng, [("distracted", .45), ("neutral", .3), ("flow", .25)])
                 if prev.outcome == "abandoned"
                 else wchoice(rng, [("flow", .65), ("neutral", .2), ("distracted", .1), ("stuck", .05)]))
    elif prev.state == "neutral":
        state = wchoice(rng, [("flow", .5), ("stuck", .1 * fatigue), ("distracted", .2 * fatigue), ("neutral", .2)])
    else:
        if ctx.unresolved and ctx.stuck and rng.random() < st.return_p:
            state = "stuck"
        else:
            state = wchoice(rng, [("flow", .5), ("neutral", .3), ("stuck", .1), ("distracted", .05)])
    if state == "flow":
        d = lognorm_min(rng, st.flow_mean_min, 4, 60)
        if rng.random() < st.deep_block_p:                # a long deep-work stretch
            d = min(d * rng.uniform(2.0, 3.5), 90 * 60.0)
        return state, ("review" if rng.random() < st.review_p else "work"), d, None
    if state == "stuck":
        r = rng.random()
        outcome = ("help" if r < st.help_p
                   else "resolved" if r < st.help_p + st.resolved_p else "abandoned")
        ret = prev.state == "distracted" and ctx.unresolved and ctx.stuck is not None
        return (state, "return_to_error" if ret else "debug_loop",
                lognorm_min(rng, st.stuck_mean_min, 4, 25), outcome)
    if state == "distracted":
        sub = wchoice(rng, [("coffee_break", .55), ("phone", .45)])
        lo, hi = (1, 8) if sub == "coffee_break" else (1.5, 10)
        return state, sub, lognorm_min(rng, st.away_mean_min, lo, hi), None
    wait_w = (0.5 if prev.state in ("flow", "stuck") else 0.06) if ctx.mode == "coding" else 0.0
    sub = wchoice(rng, [("browsing", .3), ("email", .15), ("meeting", .12), ("pdf", .1), ("msg", .13),
                        ("study", .2), ("waiting", wait_w)])
    if sub == "meeting":
        return state, sub, rng.uniform(10, 40) * 60.0, None
    if sub == "waiting":
        return state, sub, lognorm_min(rng, 8, 3, 30), None
    return state, sub, lognorm_min(rng, st.neutral_mean_min, 2, 20), None


def switch_work(rng, ctx, projects, workspaces, st):
    """Start a new piece of work: maybe a different project, or a different kind of work."""
    ctx.mode = wchoice(rng, MODE_W)
    if ctx.mode == "coding":
        ctx.project = rng.choice(projects)
        ctx.file = rng.choice([f for f in ctx.project.files if f.kind == "impl"])
        ctx.vim = rng.random() < st.vim_p
        ctx.remote = ctx.vim and rng.random() < 0.3
    else:
        ctx.ws = rng.choice(workspaces[ctx.mode])


# ------------------------------------------------------------------ assembly
def iso(t0: datetime, t: float) -> str:
    """Millisecond precision, like the real watchers."""
    return (t0 + timedelta(milliseconds=round(t * 1000))).isoformat()


def _boundaries(rng, n, length, level):
    """Input bin edges. The real watcher's bins drift a few ms and sometimes poll early (3.6-5 s bins)."""
    b = [0.0]
    for k in range(1, n):
        j = rng.uniform(0.0, 0.012) if level > 0 else 0.0
        if level > 0 and rng.random() < 0.004 * level:
            j = -rng.uniform(0.3, 1.4)
        b.append(k * BIN + j)
    b.append(length)
    return b


def build_events(rec: Recorder, t0: datetime, length: float, rng: random.Random | None = None,
                 level: float = 0.0) -> dict:
    rng = rng or random.Random(0)
    rec.presses = np.minimum(rec.presses, MAX_PRESSES)

    # --- window
    window = []
    for k, (t, data) in enumerate(rec.focus):
        end = rec.focus[k + 1][0] if k + 1 < len(rec.focus) else length
        if end - t > 1e-6:
            if level > 0 and window and rng.random() < 0.004 * level:
                window.append(dict(timestamp=iso(t0, t), duration=0.0, data=data))   # real zero-length heartbeat
            window.append(dict(timestamp=iso(t0, t), duration=round(end - t, 3), data=data))

    # --- input: one cell per 5 s bin; a missed poll folds the next bin in (a ~10 s event)
    edges = _boundaries(rng, rec.n, length, level)

    def row(i):
        return (int(rec.presses[i]), int(rec.clicks[i]), float(rec.dx[i]), float(rec.dy[i]),
                int(rec.sx[i]), int(rec.sy[i]))

    cells, i = [], 0
    while i < rec.n:
        r = row(i)
        if (level > 0 and any(r) and i + 1 < rec.n and any(row(i + 1)) and edges[i] >= i * BIN
                and edges[i + 2] <= (i + 2) * BIN + 0.012 and rng.random() < 0.003 * level):
            r2 = row(i + 1)
            r = (min(MAX_PRESSES, r[0] + r2[0]),) + tuple(a + b for a, b in zip(r[1:], r2[1:]))
            cells.append((i, i + 2, r))
            i += 2
        else:
            cells.append((i, i + 1, r))
            i += 1
    inp, c = [], 0
    while c < len(cells):
        a, b, r = cells[c]
        d = c + 1
        if not any(r):                                        # merge consecutive all-zero bins like the real watcher
            while d < len(cells) and not any(cells[d][2]):
                b = cells[d][1]
                d += 1
        t_a, t_b = edges[a], edges[b]
        inp.append(dict(timestamp=iso(t0, t_a), duration=round(t_b - t_a, 3), data=dict(
            presses=r[0], clicks=r[1], deltaX=round(r[2], 3), deltaY=round(r[3], 3),
            scrollX=r[4], scrollY=r[5])))
        c = d

    # --- afk, derived from input activity (180 s without input), plus short wake-up flaps
    active = np.flatnonzero((rec.presses > 0) | (rec.clicks > 0) | (rec.dx > 0) | (rec.dy > 0)
                            | (rec.sx != 0) | (rec.sy != 0))
    afk_iv, prev_end = [], 0.0
    for k in list(active) + [rec.n]:
        start = edges[k] if k < rec.n else length
        if start - prev_end >= AFK_TIMEOUT:
            afk_iv.append((prev_end, min(start, length)))
        if k < rec.n:
            prev_end = edges[k + 1]
    rows = []                                                 # (start, end, status)
    cur = 0.0
    for a, b in afk_iv:
        if a - cur > 1e-6:
            rows.append((cur, a, "not-afk"))
        if level > 0 and b < length - 1 and b - a > AFK_TIMEOUT + 60 and rng.random() < 0.35 * level:
            # real traces show 0-15 s afk/not-afk flaps when the machine wakes or the screen unlocks
            x = b - rng.uniform(8, 25)
            u = 0.0 if rng.random() < 0.4 else rng.uniform(3, min(15, b - x - 1))
            rows += [(a, x, "afk"), (x, x + u, "not-afk"), (x + u, b, "afk")]
        else:
            rows.append((a, b, "afk"))
        cur = b
    if length - cur > 1e-6:
        rows.append((cur, length, "not-afk"))
    afk = [dict(timestamp=iso(t0, a), duration=round(b - a, 3), data={"status": st}) for a, b, st in rows]

    # --- web: aw-watcher-web (Chrome extension) mirrors the focused Chrome tab, with tab metadata
    web, tabs = [], rng.randint(6, 30)
    for e in window:
        d = e["data"]
        if d.get("app") == "Google Chrome" and d.get("url"):
            if rng.random() < 0.08:
                tabs = int(min(40, max(1, tabs + rng.choice([-2, -1, 1, 1, 2]))))
            web.append(dict(timestamp=e["timestamp"], duration=e["duration"], data=dict(
                url=d["url"], title=d["title"], audible="youtube.com/watch" in d["url"],
                incognito=False, tabCount=tabs)))

    def fmt(rows):
        return [dict(timestamp=iso(t0, r["t"]), duration=round(r["dur"], 3), data=r["data"])
                for r in sorted(rows, key=lambda r: r["t"]) if 0 <= r["t"] < length]

    return dict(window=window, afk=afk, input=inp, web=web, terminal=fmt(rec.term), fs=fmt(rec.fs))


def generate_day(seed: int, day: date, tz_offset_h: float = -7.0,
                 start_hour: float = 9.0, end_hour: float = 17.5, noise: float = 1.0) -> dict:
    """`noise` scales background noise (0 = clean ablation, 1 = default, 2 = heavy)."""
    rng = random.Random(seed)
    nprng = np.random.default_rng(seed)
    st = sample_style(rng)
    length = (end_hour - start_hour) * 3600.0
    t0 = (datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
          + timedelta(hours=start_hour - tz_offset_h))
    rec = Recorder(int(math.ceil(length / BIN)))
    projects = [W.make_project(rng) for _ in range(rng.randint(1, 2))]
    workspaces = {k: [W.make_workspace(rng, k) for _ in range(rng.randint(1, 2))] for k in ("writing", "analysis")}
    ctx = Ctx(project=projects[0], file=None, projects=projects)
    ctx.file = rng.choice([f for f in ctx.project.files if f.kind == "impl"])
    ctx.mode = wchoice(rng, MODE_W)
    ctx.ws = workspaces[ctx.mode][0] if ctx.mode != "coding" else None
    ctx.vim = rng.random() < st.vim_p
    ctx.remote = ctx.vim and rng.random() < 0.3

    segments: list[Segment] = []
    t, prev, lunched = 0.0, None, False
    while t < length - 1e-6:
        state, sub, dur, outcome = next_segment(rng, st, prev, ctx, t, lunched, length)
        if state == "flow" and prev is not None and rng.random() < 0.3:
            switch_work(rng, ctx, projects, workspaces, st)
        lunched = lunched or sub == "lunch"
        end = t + dur
        if end > length - 90.0:
            end = length
        topic = W._wchoice(rng, W.TOPICS) if sub == "browsing" else None
        seg = Segment(len(segments), state, sub, t, end, outcome, sensitive=(sub == "msg"), topic=topic)
        EMITTERS[state](seg, rec, rng, nprng, st, ctx)
        rec.hook_off = rec.no_titles = False
        segments.append(seg)
        prev, t = seg, end

    for a, b in rec.pauses:                           # brief thinking / sip-of-water gaps
        i0, i1 = max(0, math.ceil(a / BIN)), min(rec.n, math.ceil(b / BIN))
        for arr in (rec.presses, rec.clicks, rec.dx, rec.dy, rec.sx, rec.sy):
            arr[i0:i1] = 0

    if rec.cmd_titles:                                # running-process titles (content, not noise)
        rec.focus = splice_all(rec.focus, length, rec.cmd_titles, 0.0)
    apply_noise(rec, rng, nprng, st,
                [(s.start, s.end, s.state, s.subtype, s.meta.get("libs")) for s in segments],
                noise, length)
    events = build_events(rec, t0, length, rng, noise)
    labels = [dict(segment=s.idx, state=s.state, subtype=s.subtype, outcome=s.outcome,
                   sensitive=s.sensitive, topic=s.topic, meta=s.meta,
                   start=iso(t0, s.start), end=iso(t0, s.end),
                   duration_s=round(s.end - s.start, 3)) for s in segments]
    help_req = [dict(timestamp=iso(t0, h["t"]), kind=h["kind"], segment=h["segment"],
                     app=h["win"]["app"], title=h["win"]["title"], url=h["win"].get("url"))
                for h in sorted(rec.help, key=lambda h: h["t"])]
    return dict(
        seed=seed, date=day.isoformat(), tz_offset_h=tz_offset_h, start_utc=t0.isoformat(),
        length_s=length, style=st.as_dict(), noise=noise, events=events, labels=labels,
        help_requests=help_req, generator_version=GENERATOR_VERSION)
