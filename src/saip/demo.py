"""Made-up activity and a pretend setup, for working on the dashboard without ActivityWatch or a model.

`python -m saip.server --demo` (add `--fresh` to start at the setup wizard). Every route answers from memory:
nothing on this machine is read, installed or written.
"""
from __future__ import annotations

import math
import random
import time
from collections import deque

from .server import Api, ApiError

DEMO_APPS = ["Code", "Google Chrome", "Terminal", "Slack", "Notion", "Preview", "Microsoft Word", "1Password 7",
             "Messages", "Mail", "Safari", "Figma", "Zoom", "Spotify", "Signal", "Firefox", "Xcode", "Finder"]
DEMO_TOOLS = ["read_file", "git_log", "git_diff", "read_screen", "activity_summary", "web_search", "fetch_page",
              "write_file", "copy_to_clipboard"]
DEMO_PATH = "~/Developer/demo/notes.md"
DEMO_NOTES = "# Notes\n\n- Write down what you expected to happen.\n- Write down what happened instead.\n- Try the smallest change.\n"
FEATURES = {"circling": 8.0, "revisits": 1.5, "toggling": 0.5, "stalling": 0.4, "reaching": -0.5, "typing": 0.0,
            "ai_share": -1.0}


class _DemoRuntime:
    def __init__(self):
        from .chats import Chats
        self.chats = Chats()
        self.client = object()
        self.last_context = "Activity over the last 5 minutes: 14 app switches between Code and Google Chrome."
        self.look = lambda: None
        self.snapshot_context = lambda: self.last_context
        self.inspector: deque = deque(maxlen=50)
        self.opts = None
        self.started_at = time.time()
        self.running = True

    def status(self) -> dict:
        return {"running": self.running, "mode": "demo", "model": "demo-model", "error": None,
                "started_at": self.started_at, "screen": True, "titles": True,
                "monitor": {"enabled": True, "running": True, "presses": 0, "error": None}}

    def reload(self) -> None:
        pass


class DemoApi(Api):
    def __init__(self, bus, fresh: bool = False):
        super().__init__(bus, runtime=_DemoRuntime())
        self.ready = not fresh
        self.runtime.running = not fresh
        self.history: list[dict] = []
        self.decision_rows: list[dict] = []
        self.blocked = ["1Password 7", "Messages", "Signal", "Mail"]
        self.cfg = {"llm": {"base_url": "" if fresh else "http://demo:8000/v1", "model": "" if fresh else "demo-model",
                            "api_key_set": False, "timeout_s": 30.0, "samples": 10, "sample_temperature": 0.7,
                            "lookback_s": 300.0, "include_titles": True, "private_apps": self.blocked,
                            "read_screen": True, "screen_chars": 1200, "reasoning_effort": "", "chat_tools": True,
                            "web_search": True, "search_provider": "", "search_api_key_set": False},
                    "gate": {"c0": 1.0, "m0": 2.0, "alpha": 1.0986, "beta": 0.6931, "delta": 0.1, "theta_flow": 0.5,
                             "eps": 0.001},
                    "agent": {"tick_s": 5.0, "history_min": 90.0, "assess_interval_s": 30.0, "cooldown_s": 300.0,
                              "offer_timeout_s": 45.0, "pending_expiry_s": 600.0, "break_idle_s": 5.0, "repos": [],
                              "watch_roots": [] if fresh else ["~/Developer"], "exclude": [], "rescan_s": 300.0,
                              "new_repos": "ask", "monitor_typed_content": True, "typed_text_max_chars": 200, "notify_offers": True, "keep_chats": False},
                    "path": "(demo: nothing is written)"}
        self.hook = False
        self.roots = list(self.cfg["agent"]["watch_roots"])
        self.undecided = ["~/Developer/saip", "~/Developer/notes", "~/Developer/site"] if self.roots else []
        self.auto = False
        self.offers = self._offers
        self.health = lambda: {"checks": [{"name": "demo", "status": "ok",
                                           "detail": "made-up activity, nothing on this machine is read"}]}

    def _offers(self) -> dict:
        from . import offers
        return {"summary": offers.summarise(self.history), "offers": self.history[-200:]}

    @property
    def plat(self):
        class P:
            name = "macOS"
        return P()

    # ---- reading ----
    def app(self) -> dict:
        return {"app": "saip", "version": "demo", "platform": "macOS", "demo": True, "port": self.port, "pages": self.bus.pages,
                "ready": self.ready, "setup_done": self.ready, "model": self.cfg["llm"]["model"] or None,
                "config": self.cfg["path"], "runtime": self.runtime.status(), "autostart": self.auto,
                "monitor_typed_content": self.cfg["agent"]["monitor_typed_content"], "quiet_until": self.bus.quiet_until}

    def decisions(self) -> dict:
        return {"rows": self.decision_rows[-5000:], "total": len(self.decision_rows)}

    def export(self, what, fmt):
        from .server import Download, _rows_to_csv
        import json
        rows = self.history if what == "offers" else self.decision_rows
        if fmt == "csv":
            return Download(_rows_to_csv(rows), "text/csv; charset=utf-8", f"saip-demo-{what}.csv")
        return Download("".join(json.dumps(r) + "\n" for r in rows).encode(), "application/x-ndjson",
                        f"saip-demo-{what}.jsonl")

    def settings(self) -> dict:
        return self.cfg

    def apps(self) -> dict:
        from llm.privacy import GROUPS, SUGGESTED_GROUPS, group_of, is_blocked
        rows = []
        for a in sorted(DEMO_APPS, key=str.lower):
            g = group_of(a)
            rows.append({"label": a, "value": a, "installed": True, "seen": a in DEMO_APPS[:8], "group": g,
                         "blocked": is_blocked(a, self.blocked), "suggested": g in SUGGESTED_GROUPS})
        return {"apps": rows, "blocked": self.blocked,
                "groups": [{"id": g, "label": label} for g, label, _ in GROUPS] + [{"id": "other", "label": "Other apps"}]}

    def setup_status(self) -> dict:
        return {"ready": self.ready, "done": self.ready, "config": self.cfg["path"],
                "model_set": bool(self.cfg["llm"]["model"]), "api_key_set": self.cfg["llm"]["api_key_set"],
                "llm": {"base_url": self.cfg["llm"]["base_url"], "model": self.cfg["llm"]["model"]},
                "features": {"typed": True, "screen": True, "titles": True}, "platform": "macOS"}

    def aw_status(self) -> dict:
        checks = [{"name": "window watcher", "status": "ok", "detail": "events with titles arriving"},
                  {"name": "AFK watcher", "status": "ok", "detail": ""},
                  {"name": "input watcher", "status": "ok", "detail": "counts arriving"}]
        return {"running": True, "installed": True, "url": "https://activitywatch.net/downloads/", "checks": checks,
                "permission_hint": "", "ok": True}

    def permissions(self, typed=True, screen=True) -> dict:
        rows = []
        if typed:
            rows.append({"id": "Input Monitoring", "label": "Input Monitoring", "needed_for": "typed-text monitor",
                         "granted": True, "how": "Demo: pretend it is granted.", "can_request": True})
        if screen:
            rows.append({"id": "Accessibility", "label": "Accessibility", "needed_for": "screen text",
                         "granted": None, "how": "Demo: turn on your terminal in Privacy & Security > Accessibility.",
                         "can_request": True})
        return {"rows": rows, "host_app": "Terminal"}

    def hook_status(self) -> dict:
        return {"shell": "zsh", "profile": "~/.zshrc", "supported": True, "installed": self.hook}

    def repos(self) -> dict:
        return {"roots": self.roots, "exclude": [], "undecided": [{"path": p, "state": "ready"} for p in self.undecided],
                "suggestions": ["~/Developer", "~/projects"],
                "repos": [{"path": f"{r}/saip", "state": "installed", "detail": ""} for r in self.roots],
                "summary": f"{len(self.roots)} repositories: {len(self.roots)} hooked" if self.roots else "no repositories found"}

    def autostart(self) -> dict:
        return {"enabled": self.auto, "where": "~/Library/LaunchAgents/dev.saip.agent.plist (demo)"}

    def _tool_names(self) -> list[str]:
        return list(DEMO_TOOLS)

    def _undo_write(self, undo):
        return True, "Demo: nothing to undo on disk."

    def _scripted(self, text: str) -> tuple[list, dict | None, str]:
        """(tool events, pending action, words) for what the person wrote."""
        low = text.lower()
        if "file" in low or "write" in low:
            args = {"path": DEMO_PATH, "content": DEMO_NOTES}
            preview = {"title": f"Write {DEMO_PATH}", "path": DEMO_PATH, "exists": False, "content": DEMO_NOTES, "diff": None}
            return ([{"name": "read_file", "arguments": {"path": "notes/todo.md"}, "label": "Read notes/todo.md", "ok": True}],
                    {"call_id": "demo-write", "name": "write_file", "arguments": args, "preview": preview},
                    "I've drafted the file. Approve it and I'll save it.")
        if "search" in low:
            label = f"Searched the web: {text[:80]}"
            return ([{"name": "web_search", "arguments": {"query": text}, "label": label, "ok": True}], None,
                    "In the demo the search is made up, but this is where the sources would be summed up in a sentence or two.")
        if "copy" in low:
            args = {"text": "The gap between what you expected and what happened is the next step."}
            preview = {"title": "Copy to clipboard", "path": None, "exists": False, "content": args["text"], "diff": None}
            return [], {"call_id": "demo-copy", "name": "copy_to_clipboard", "arguments": args, "preview": preview}, \
                "Here is a line you could keep. Approve it and I'll copy it."
        return [], None, ("In the demo I can't read your work, but here's how I'd start: say in one sentence what you "
                          "expected to happen, then what happened instead. The gap between the two is usually the next step.")

    def chat_send(self, cid, message):
        from .chats import Chats
        chats = self.runtime.chats
        c = chats.get(cid)
        if c is None:
            raise ApiError(404, "no such conversation")
        text = str(message).strip()
        if not text:
            raise ApiError(400, "write a message first")
        if Chats.pending(c):
            raise ApiError(409, "approve or decline the proposed action first")
        time.sleep(0.6)
        events, pending, words = self._scripted(text)
        wire = list(c["wire"])
        start = len(wire)
        wire.append({"role": "user", "content": text})
        if not pending:
            wire.append({"role": "assistant", "content": words})
        self._record(cid, wire, start, [{"kind": "user", "text": text}],
                     {"events": events, "pending": pending, "text": words})
        return self.chat_get(cid)

    def chat_action(self, cid, item_id, approve):
        chats = self.runtime.chats
        c = chats.get(cid)
        item = None if c is None else next((i for i in c["items"] if i["id"] == item_id and i["kind"] == "action"), None)
        if item is None:
            raise ApiError(404, "no such action")
        if not chats.claim_action(cid, item_id):
            raise ApiError(409, "that action was already decided")
        time.sleep(0.6)
        if approve:
            undo = {"path": item["action"]["path"], "existed": False, "demo": True} if item["action"]["name"] == "write_file" else None
            chats.set_action(cid, item_id, "approved", "Demo: nothing was written.", undo)
            result, words = "Done: Demo: nothing was written.", "Done. In the demo nothing was written; with a model connected it would be saved."
        else:
            chats.set_action(cid, item_id, "declined")
            result, words = "The person declined this action.", "Okay, I won't do that."
        wire = c["wire"] + [{"role": "tool", "tool_call_id": item["_call"], "content": result},
                            {"role": "assistant", "content": words}]
        self._record(cid, wire, None, [], {"events": [], "pending": None, "text": words})
        return self.chat_get(cid)

    # ---- changing (in memory only) ----
    def save_settings(self, body: dict) -> dict:
        for section, table in (body or {}).items():
            if section not in ("llm", "agent", "gate") or not isinstance(table, dict):
                raise ApiError(400, f"unknown section [{section}]")
            for k, v in table.items():
                if k in ("api_key", "search_api_key"):
                    if v:
                        self.cfg["llm"][f"{k}_set"] = True
                    continue
                if k not in self.cfg[section]:
                    raise ApiError(400, f"unknown setting '{k}' in [{section}]")
                self.cfg[section][k] = v
        self.blocked[:] = self.cfg["llm"]["private_apps"]
        self.changed()
        return {"ok": True, "changed": [f"{s}.{k}" for s, t in body.items() for k in t], "settings": self.cfg}

    def aw_action(self, action):
        time.sleep(0.8)
        return {"ok": True, "detail": "running"}

    def test_endpoint(self, base_url, model="", api_key=None):
        time.sleep(0.9)
        base = str(base_url).strip()
        model_name = str(model or "").strip()
        if not base:
            return {"ok": False, "detail": "Enter the endpoint URL.", "hint": ""}
        if "fail" in base:
            return {"ok": False, "detail": "cannot reach http://fail: Connection refused",
                    "hint": "Could not reach that URL.", "seconds": 0.9}
        if not model_name:
            model_name = "Qwen/Qwen2.5-7B-Instruct"
        return {"ok": True, "detail": f"{base} answered with log-probabilities (p_yes = 0.12)", "seconds": 0.42, "model": model_name}

    def request_permission(self, perm_id):
        return {"ok": True}

    def key_test(self, seconds):
        time.sleep(min(float(seconds), 3))
        return {"available": True, "presses": 7, "detail": ""}

    def hook_action(self, action):
        self.hook = action == "install"
        return {"ok": True, "detail": "added in ~/.zshrc (demo)" if self.hook else "removed (demo)"}

    def repo_preview(self, folder):
        return {"folder": folder, "count": 3, "too_many": False, "summary": "3 repositories: 3 can be hooked",
                "repos": [{"path": f"{folder}/{n}", "state": "ready", "detail": ""} for n in ("saip", "notes", "site")]}

    def repo_action(self, action, folder):
        if action in ("add", "decline"):
            self.undecided = []
        if action == "add" and folder not in self.roots:
            self.roots.append(folder)
        if action == "remove":
            self.roots = [r for r in self.roots if r != folder]
        return {"ok": True, "detail": f"{action} {folder} (demo)"}

    def finish(self):
        if not self.cfg["llm"]["model"]:
            return {"ok": False, "detail": "a model endpoint is required: test and save one first"}
        self.ready = True
        self.runtime.running = True
        self.changed()
        return {"ok": True}

    def set_autostart(self, enabled):
        self.auto = bool(enabled)
        self.changed()
        return self.autostart()


def _features(rng, score: float) -> dict:
    loop = max(0.0, min(1.0, score * 1.25 + rng.uniform(-0.05, 0.05)))
    vals = {"circling": loop * 0.55, "revisits": min(1.0, loop * 0.9 + rng.uniform(0, 0.1)),
            "toggling": min(1.0, 0.2 + loop * 0.6), "stalling": rng.uniform(0.05, 0.4),
            "reaching": max(0.0, 0.5 - loop * 0.4), "typing": rng.uniform(0.2, 0.8), "ai_share": rng.uniform(0, 0.2)}
    return {"bias": -3.2, "items": {k: {"value": round(v, 4), "weight": FEATURES[k], "push": round(FEATURES[k] * v, 4)}
                                    for k, v in vals.items()}}


def demo_loop(bus, api: DemoApi, speed: float = 1.0) -> None:
    """A calm stretch, a climb while circling between two apps, a deferred offer, then an offer."""
    rng, trigger, score, i, mult = random.Random(7), 0.19, 0.04, 0, 1.0
    ep, app = None, "Code"
    while True:
        if not api.ready:                              # first run: nothing is scored until setup is done
            time.sleep(0.5)
            continue
        i += 1
        phase = (i % 150) / 150
        target = 0.05 if phase < 0.35 else 0.62 if phase < 0.8 else 0.08
        score += 0.1 * (target - score) + rng.uniform(-0.01, 0.01)
        score = min(1.0, max(0.0, score))
        paused = 0.86 < phase < 0.93
        if phase < 0.35:
            app = "Code" if i % 9 else rng.choice(["Google Chrome", "Terminal"])
        elif phase < 0.8:
            app = "Code" if (i // 2) % 2 else "Google Chrome"
        else:
            app = rng.choice(["Code", "Notion", "Slack"])
        now = time.time()
        bus.publish("tick", time=now, score=round(score, 4), paused=paused, reason="afk" if paused else None,
                    trigger=trigger, app=None if paused else app, features=_features(rng, score))
        stuck = not paused and score >= trigger
        if stuck and ep is None:
            ep = {"episode": i, "start": now, "end": now, "peak": score, "apps": {}}
            bus.publish("episode", state="open", **ep, app=app)
        if ep is not None:
            ep["end"], ep["peak"] = now, max(ep["peak"], score)
            ep["apps"][app] = ep["apps"].get(app, 0) + 1.0 / speed
            if not stuck:
                main = max(ep["apps"], key=ep["apps"].get)
                bus.publish("episode", state="closed", **{**ep, "peak": round(ep["peak"], 4)}, app=main)
                ep = None
        if stuck and i % 12 == 0 and now >= bus.quiet_until:
            p_need = min(0.97, 0.35 + score * 0.8 + rng.uniform(-0.05, 0.05))
            p_accept = min(0.97, 0.2 + score * 0.9 + rng.uniform(-0.08, 0.08))
            c_fa, c_fn = math.exp(math.log(3) * (1 - score)), 2 * math.exp(math.log(2) * score)
            bar = c_fa / (c_fa + p_need * c_fn)
            kind = "offer" if p_accept >= bar else "deferred" if p_accept >= bar - 0.1 and score < 0.5 else "quiet"
            row = dict(time=round(now, 1), decision=kind, score=round(score, 4), p_need=round(p_need, 4),
                       p_accept=round(p_accept, 4), bar=round(bar, 4), resampled=abs(p_accept - bar) <= 0.1, app=app)
            api.decision_rows.append(row)
            bus.publish("decision", **{k: v for k, v in row.items() if k != "time"})
            context = ("Activity over the last 5 minutes (oldest first):\n"
                       + "\n".join(f"- {s}s ago: {a} ({'editor' if a == 'Code' else 'browser'}) for 4s"
                                   for s, a in zip(range(120, 0, -8), ["Code", "Google Chrome"] * 8))
                       + "\nWindows: 15 visits to 2 distinct windows (13 returns to a window seen earlier), 14 app switches."
                       + '\nRecently typed (oldest first, newest last): "def parse_header(raw): return raw.split(b\':\')"')
            entry = dict(row, context=context, typed=True, offer=None, screen=None)
            api.runtime.inspector.append(entry)
            if kind == "offer":
                message = f"You've gone back and forth between {app} and the docs several times. Want to talk through the next step?"
                entry["offer"] = {"message": message, "task": "Talk through the next step", "generated": True}
                bus.publish("offer", message=message, task="Talk through the next step", generated=True, app=app)
                bus.publish("inspector", count=len(api.runtime.inspector))
                began = time.time()
                answer = bus.ask("SAIP", message, ("Not now", "Help me"), 45)
                outcome = {"Help me": "accepted", "Not now": "dismissed"}.get(answer, "ignored")
                mult = 1.0 if outcome == "accepted" else min(mult * (2.0 if outcome == "dismissed" else 1.5), 4.0)
                api.history.append({"time": began, "stuck_score": score, "p_need": p_need, "p_accept": p_accept,
                                    "bar": bar, "task": "Talk through the next step", "message": message,
                                    "outcome": outcome, "answered_after_s": time.time() - began if answer else None,
                                    "app": app})
                bus.publish("outcome", outcome=outcome, cooldown_mult=mult)
                if outcome == "accepted":
                    text = ("Name the one thing you expected to see in the second window and did not. If you can say "
                            "it in a sentence, start there; if not, tell me what each window is for.")
                    sid = api.runtime.chats.create("Talk through the next step", api.runtime.last_context, None, first=text)
                    bus.publish("help", text=text, session=sid, task="Talk through the next step")
                score = 0.1
            else:
                bus.publish("inspector", count=len(api.runtime.inspector))
        time.sleep(1.0 / speed)
