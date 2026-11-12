"""The live agent as an object that can be started, stopped and reloaded while the dashboard stays up.

This is the loop `saip run` has always run (read ActivityWatcher, score, ask the model when the score is high, offer
help), moved out of cli.py so the setup wizard can start it when setup finishes and the settings page can reload
it after a change, without restarting the process.

Besides the terminal lines it always printed, it publishes to the Bus:

    tick      score, trigger, pause, the app in front (blocked apps as "Private app") and each feature's push
    decision  the gate's numbers and the app; also appended to decisions.jsonl
    episode   a stuck stretch: opened when the score crosses the trigger, closed when it drops back or pauses
    offer / outcome / help / status   as before

and keeps the last 50 model checks (the exact context sent, the answers, the offer) in memory for the inspector.
"""
from __future__ import annotations

import itertools
import sys
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass

from llm.assess import assess
from llm.calibration import load_temperatures
from llm.client import LLMError, OpenAIChatClient
from llm.config import load_settings, user_config_dir
from llm.context import build_context
from llm.helper import give_help
from llm.privacy import shown
from llm.proposal import FALLBACK, propose
from llm.replay import MockClient

from . import offers as offers_mod
from . import prompts as repo_prompts
from . import repos as repos_mod
from . import setup as setup_mod
from .chats import Chats
from .agent import NotReady, Orchestrator, describe, fetch_trace, score_now
from .aw import AWError
from .hooks import githook
from .screen import copy_to_clipboard, read_screen, typed_for_model
from .watchers import GitWatcher

INSPECTOR_KEPT = 50


@dataclass
class RunOptions:
    config: str | None = None
    mock_llm: bool = False
    trigger: float | None = None          # testing: wake the LLM above this score instead
    no_dialog: bool = False
    notify: bool = False
    read_screen: bool = False             # --read-screen (or read_screen in private.toml)


def _stamp() -> str:
    return time.strftime("%H:%M:%S")


class Episodes:
    """Turns ticks into stuck stretches, each named after the app that was in front for most of it."""

    def __init__(self, publish):
        self.publish, self.open, self._ids, self._last_t = publish, None, itertools.count(1), None

    def feed(self, t: float, score: float, trigger: float, paused: bool, app: str | None) -> None:
        stuck = not paused and score >= trigger
        dt = 0.0 if self._last_t is None else min(max(t - self._last_t, 0.0), 15.0)
        self._last_t = t
        if stuck:
            if self.open is None:
                self.open = {"id": next(self._ids), "start": t, "end": t, "peak": score, "apps": Counter()}
                self.publish("episode", state="open", **self._view(self.open))
            ep = self.open
            ep["end"], ep["peak"] = t, max(ep["peak"], score)
            if app:
                ep["apps"][app] += dt or 1e-6
        elif self.open is not None:
            ep, self.open = self.open, None
            self.publish("episode", state="closed", **self._view(ep))

    @staticmethod
    def _view(ep: dict) -> dict:
        apps = ep["apps"]
        main = apps.most_common(1)[0][0] if apps else None
        return {"episode": ep["id"], "start": ep["start"], "end": ep["end"], "peak": round(ep["peak"], 4),
                "app": main, "apps": {k: round(v, 1) for k, v in apps.most_common(5)}}

    def close(self) -> None:
        if self.open is not None:
            ep, self.open = self.open, None
            self.publish("episode", state="closed", **self._view(ep))


class Runtime:
    def __init__(self, bus, plat, aw, opts: RunOptions | None = None, ui=None):
        self.bus, self.plat, self.aw, self.opts = bus, plat, aw, opts or RunOptions()
        self.ui = ui or plat                  # dialogs: the dashboard when one is open, else the system's own
        self.inspector: deque = deque(maxlen=INSPECTOR_KEPT)
        self.chats = Chats()
        self.client = None                    # the model in use while the agent runs (the chat page talks to it)
        self.last_context = ""                # the newest activity summary, as the model saw it
        self.last_screen = None               # and the newest screen text
        self.look = lambda: None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.monitor = None
        self.state = {"running": False, "mode": "stopped", "model": None, "error": None, "started_at": None,
                      "monitor": {"enabled": False, "running": False, "error": None}, "screen": False, "titles": False}

    # ---- lifecycle ----
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="saip-agent")
            self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        with self._lock:
            thread, self._thread = self._thread, None
            self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def reload(self) -> None:
        """Pick up new settings: stop and start again (the dashboard and the tray stay connected)."""
        was = self.running or self.state["running"]
        self.stop()
        if was:
            self.start()

    def status(self) -> dict:
        s = dict(self.state)
        m = self.monitor
        s["monitor"] = dict(s["monitor"], running=bool(m and m.running), presses=getattr(m, "presses", 0))
        s["running"] = self.running
        return s

    def _changed(self) -> None:
        self.bus.publish("app", runtime=self.status())

    # ---- the loop ----
    def _run(self) -> None:
        try:
            self._loop()
        except Exception as e:                                   # never die silently: tell the dashboard
            self.state.update(error=f"{type(e).__name__}: {e}")
            print(f"{_stamp()}  agent stopped: {e}", file=sys.stderr)
            self.bus.publish("status", level="error", text=f"The agent stopped: {e}")
        finally:
            self.state.update(running=False, mode="stopped")
            self._changed()

    def _loop(self) -> None:
        opts, plat, aw, bus, ui = self.opts, self.plat, self.aw, self.bus, self.ui
        settings = load_settings(opts.config)
        cfg, llm = settings.agent, settings.llm
        client, mode, label = None, "score-only", "score only (no LLM endpoint configured: scores are printed, nothing is decided)"
        if opts.mock_llm:
            client, mode, label = MockClient(), "mock", "MOCK LLM (pipeline check only)"
        elif llm.base_url and llm.model:
            client, mode, label = OpenAIChatClient(llm), "llm", f"LLM {llm.model} at {llm.base_url}"
        temps = load_temperatures(user_config_dir() / "calibration.json")
        git = GitWatcher(aw, [])
        orch = Orchestrator(cfg)
        blocked = tuple(llm.private_apps)
        want_screen = opts.read_screen or llm.read_screen
        self.state.update(running=True, mode=mode, model=llm.model or None, error=None, started_at=time.time(),
                          screen=want_screen, titles=llm.include_titles,
                          monitor={"enabled": cfg.monitor_typed_content, "running": False, "error": None})

        def accepted_now(state, hooked):
            if not hooked:
                git.repos = list(dict.fromkeys(git.repos + [state.path]))
            setup_mod.update_manifest(hooked_repos=[str(state.path)] if hooked else [])
        prompter = repo_prompts.RepoPrompter(ui, sys.executable, cfg.new_repos, accepted_now)
        prompter.start()

        monitor = None
        if cfg.monitor_typed_content:
            from . import typing_monitor as tm
            monitor = tm.TypingMonitor(clip_chars=cfg.typed_text_max_chars, blocked=blocked)
            try:
                monitor.start()
                print("Typed-text monitor on (memory only; F1 turns it off).", flush=True)
            except RuntimeError as e:
                self.state["monitor"]["error"] = str(e)
                print(f"Typed-text monitor not started: {e}", flush=True)
                monitor = None
        self.monitor = monitor
        self.client = client
        self._changed()

        seen = {"screen_warned": False}

        def look():
            if not want_screen:
                return None
            screen = read_screen(plat, blocked, llm.screen_chars)
            if screen is None and not seen["screen_warned"]:
                seen["screen_warned"] = True
                why = plat.screen_error or "nothing readable in the front app (or it is blocked)"
                print(f"{_stamp()}  screen text not used: {why}")
            return screen

        self.look = look

        def help_out(offer):
            screen = look() or seen.get("screen")
            try:
                text = give_help(client, offer.proposal.task, seen.get("context", ""), screen)
            except LLMError as e:
                print(f"{_stamp()}  could not write the help ({e})")
                return
            print(f"          help: {text}")
            shot = {"app": shown(screen.app, blocked), "text": screen.text} if screen is not None else None
            session = self.chats.create(offer.proposal.task, seen.get("context", ""), shot, first=text)
            bus.publish("help", text=text, session=session, task=offer.proposal.task)
            if cfg.notify_offers:
                threading.Thread(target=plat.notify, args=("SAIP: help is ready", text[:200]), daemon=True).start()
            if presenter.dialog and bus.pages == 0 and ui.ask("SAIP", text, ("Close", "Copy"), timeout=180,
                                                              default=False) == "Copy":
                copy_to_clipboard(text)

        def answered(offer, outcome):
            orch.record_outcome(outcome)
            if outcome != offers_mod.UNANSWERED:
                extra = f" (quiet period x{orch.cooldown_mult:g})" if outcome != offers_mod.ACCEPTED else ""
                print(f"{_stamp()}  offer {outcome}{extra}")
            bus.publish("outcome", outcome=outcome, cooldown_mult=orch.cooldown_mult)
            if outcome == offers_mod.ACCEPTED and client is not None:
                help_out(offer)
        presenter = offers_mod.OfferPresenter(ui, cfg.offer_timeout_s, use_dialog=not opts.no_dialog, on_outcome=answered)
        episodes = Episodes(bus.publish)
        print(f"Watching on {plat.name}. Mode: {label}", flush=True)
        last_status = last_scan = 0.0
        try:
            while not self._stop.is_set():
                now = time.time()
                if now - last_scan >= cfg.rescan_s:
                    accepted, declined = setup_mod.decisions()
                    plan = repos_mod.plan_sync(cfg, sys.executable, accepted, declined)
                    for st in plan.refresh:
                        githook.install(st.path, sys.executable)
                    setup_mod.update_manifest(hooked_repos=[str(p) for p in plan.hooked + [s.path for s in plan.refresh]])
                    git.repos = plan.polled
                    for st in plan.new:
                        prompter.submit(st)
                    last_scan = now
                git.poll(now)
                try:
                    trace = fetch_trace(aw, now, cfg.history_min * 60)
                    typed_text = monitor.get_current_text() if monitor is not None and monitor.running else None
                    tick = score_now(trace, now, typed_text=typed_text)
                    if opts.trigger is not None:
                        tick.theta_trig = opts.trigger
                except NotReady as e:
                    if now - last_status > 60:
                        print(f"{_stamp()}  waiting: {e}")
                        bus.publish("status", level="waiting", text=str(e))
                        last_status = now
                    self._stop.wait(cfg.tick_s)
                    continue
                except AWError as e:
                    print(f"{_stamp()}  {e}")
                    bus.publish("status", level="error", text=str(e))
                    self._stop.wait(15)
                    continue
                app = shown(tick.front[0] if tick.front else None, blocked)
                bus.publish("tick", time=tick.time, score=round(tick.stuck_score, 4), paused=tick.paused,
                            reason=tick.reason, trigger=tick.theta_trig, app=app, features=tick.features)
                episodes.feed(tick.time, tick.stuck_score, tick.theta_trig, tick.paused, app)
                if client is not None and now >= bus.quiet_until:
                    probe = {}

                    def run_assess():
                        typed = typed_for_model(typed_text)
                        seen["context"] = build_context(trace, tick.time, llm.lookback_s, llm.include_titles,
                                                        blocked, typed_text=typed)
                        self.last_context = seen["context"]
                        probe.update(context=seen["context"], typed=bool(typed))
                        return assess(client, settings, temps, seen["context"], tick.stuck_score)
                    try:
                        ev = orch.step(now, tick, run_assess)
                    except LLMError as e:
                        print(f"{_stamp()}  LLM error: {e}")
                        bus.publish("status", level="error", text=f"LLM error: {e}")
                        ev = None
                        orch.last_assess = now
                    if ev:
                        print(describe(now, tick, ev))
                        d = ev.assessment.decision
                        row = dict(time=round(now, 1), decision=ev.kind, score=round(tick.stuck_score, 4),
                                   p_need=round(d.p_need, 4), p_accept=round(d.p_accept, 4), bar=round(d.tau, 4),
                                   resampled=ev.assessment.resampled, app=app)
                        bus.publish("decision", **{k: v for k, v in row.items() if k != "time"})
                        offers_mod.append_decision(row)
                        entry = dict(row, context=probe.get("context") or seen.get("context", ""),
                                     typed=probe.get("typed", False), offer=None, screen=None)
                        self.inspector.append(entry)
                        if ev.kind == "offer":
                            proposal = FALLBACK
                            seen["screen"] = look()
                            if seen["screen"]:
                                print(f"          (read {len(seen['screen'].text)} characters from {seen['screen'].app})")
                                entry["screen"] = {"app": shown(seen["screen"].app, blocked), "text": seen["screen"].text}
                            try:
                                proposal = propose(client, seen["context"], orch.take_outcome(), screen=seen["screen"])
                            except LLMError as e:
                                print(f"{_stamp()}  could not write the offer ({e}); using the plain one")
                            print(f"          >>> {proposal.message}")
                            entry["offer"] = {"message": proposal.message, "task": proposal.task,
                                              "generated": proposal.generated}
                            bus.publish("offer", message=proposal.message, task=proposal.task,
                                        generated=proposal.generated, app=app)
                            if cfg.notify_offers or (not presenter.dialog and opts.notify):
                                threading.Thread(target=plat.notify, args=("SAIP", proposal.message), daemon=True).start()
                            presenter.submit(offers_mod.make_offer(now, d, ev.assessment.resampled, proposal))
                        bus.publish("inspector", count=len(self.inspector))
                if now - last_status >= 60:
                    state = f"paused ({tick.reason})" if tick.paused else "scoring"
                    print(f"{_stamp()}  status: {state}, stuck score {tick.stuck_score:.2f} "
                          f"(LLM wakes above {tick.theta_trig:.2f})")
                    last_status = now
                self._stop.wait(cfg.tick_s)
        finally:
            episodes.close()
            prompter.stop()
            presenter.stop()
            if monitor is not None:
                monitor.stop()
            self.monitor = None
            self.client = None
