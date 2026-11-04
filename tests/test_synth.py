import collections
import hashlib
import json
from datetime import date, datetime

import pytest

from synth.categories import category
from synth.generator import AFK_TIMEOUT, BIN, STATES, generate_day
from synth.summary import summarize
from synth.writer import write_day

SEEDS = [1, 2, 3, 4, 5, 6]
DAY = date(2026, 11, 4)
INPUT_KEYS = {"presses", "clicks", "deltaX", "deltaY", "scrollX", "scrollY"}


@pytest.fixture(scope="module")
def days():
    return {s: generate_day(s, DAY) for s in SEEDS}


def digest(result):
    return hashlib.sha256(json.dumps(result["events"], sort_keys=True).encode()).hexdigest()


def t(ts):
    return datetime.fromisoformat(ts).timestamp()


def test_deterministic_per_seed(days):
    assert digest(generate_day(1, DAY)) == digest(days[1])


def test_seeds_differ(days):
    assert len({digest(d) for d in days.values()}) == len(SEEDS)


def test_labels_cover_day_contiguously(days):
    for d in days.values():
        labels = d["labels"]
        assert t(labels[0]["start"]) == t(d["start_utc"])
        for a, b in zip(labels, labels[1:]):
            assert a["end"] == b["start"]
        assert t(labels[-1]["end"]) - t(d["start_utc"]) == pytest.approx(d["length_s"])
        assert {l["state"] for l in labels} <= set(STATES)


def test_every_state_appears_across_seeds(days):
    seen = {l["state"] for d in days.values() for l in d["labels"]}
    assert seen == set(STATES)


@pytest.mark.parametrize("bucket", ["window", "afk", "input"])
def test_streams_tile_the_day_without_overlap(days, bucket):
    for d in days.values():
        ev = d["events"][bucket]
        end = 0.0
        for e in ev:
            start = t(e["timestamp"]) - t(d["start_utc"])
            assert start == pytest.approx(end, abs=0.01)
            assert e["duration"] > 0
            end = start + e["duration"]
        assert end == pytest.approx(d["length_s"], abs=0.01)


def test_input_is_counts_only(days):
    for d in days.values():
        for e in d["events"]["input"]:
            assert set(e["data"]) == INPUT_KEYS
            assert all(v >= 0 for k, v in e["data"].items() if k in ("presses", "clicks", "deltaX", "deltaY"))
            nonzero = any(e["data"].values())
            if nonzero:
                assert e["duration"] == pytest.approx(BIN)


def test_terminal_and_fs_hold_no_free_text(days):
    for d in days.values():
        for e in d["events"]["terminal"]:
            assert set(e["data"]) == {"exit_code", "cmd_kind", "error_sig", "error_type"}
            sig, etype = e["data"]["error_sig"], e["data"]["error_type"]
            # a hash implies a failure; benign non-zero exits (grep, Ctrl-C) carry no hash
            assert sig is None or e["data"]["exit_code"] != 0
            assert (sig is None) == (etype is None)
            # error_type is just an exception class / error code, never message text
            assert etype is None or (len(etype) <= 24 and " " not in etype)
            assert sig is None or (len(sig) == 8 and all(c in "0123456789abcdef" for c in sig))
        for e in d["events"]["fs"]:
            assert set(e["data"]) == {"kind", "file_id"}


def test_afk_never_triggers_inside_short_gaps(days):
    # every afk interval must be at least the timeout long (derived from input idleness)
    for d in days.values():
        for e in d["events"]["afk"]:
            if e["data"]["status"] == "afk":
                assert e["duration"] >= AFK_TIMEOUT - 1e-6


def test_no_input_during_afk(days):
    for d in days.values():
        afk = [(t(e["timestamp"]), t(e["timestamp"]) + e["duration"])
               for e in d["events"]["afk"] if e["data"]["status"] == "afk"]
        for e in d["events"]["input"]:
            if any(e["data"].values()):
                mid = t(e["timestamp"]) + e["duration"] / 2
                assert not any(a < mid < b for a, b in afk)


def test_help_requests_match_windows(days):
    from synth.categories import WORK_CATEGORIES
    chat_apps = {"Claude", "ChatGPT"}
    for d in days.values():
        starts = {e["timestamp"] for e in d["events"]["window"]}
        win = [(t(e["timestamp"]), t(e["timestamp"]) + e["duration"], e["data"]) for e in d["events"]["window"]]
        for h in d["help_requests"]:
            assert h["kind"] in {"stuck_help", "incidental", "stuck_help_inline", "incidental_inline"}
            if h["kind"].endswith("_inline"):
                # help asked inside the work app: no chat window exists, the work window just stays in front
                cover = [w for a, b, w in win if a <= t(h["timestamp"]) < b]
                assert cover and category(cover[0]) in WORK_CATEGORIES
            else:
                assert h["timestamp"] in starts
                assert h["app"] in chat_apps | {"Google Chrome"}


def test_stuck_repeats_errors_more_than_flow(days):
    rep_stuck, rep_flow = [], []
    for d in days.values():
        s = summarize(d)
        if s["stuck"]["repeat_error_pct"] is not None:
            rep_stuck.append(s["stuck"]["repeat_error_pct"])
        if s["flow"]["repeat_error_pct"] is not None:
            rep_flow.append(s["flow"]["repeat_error_pct"])
    assert sum(rep_stuck) / len(rep_stuck) > 40
    assert sum(rep_flow) / len(rep_flow) < sum(rep_stuck) / len(rep_stuck)       # more, not only, in stuck


def test_stuck_switches_more_than_flow_on_average(days):
    # raw window events are swamped by flicks and title churn; dwell switches are the robust signal
    stuck = [summarize(d)["stuck"]["dwell_switches_per_min"] for d in days.values()]
    flow = [summarize(d)["flow"]["dwell_switches_per_min"] for d in days.values()]
    assert sum(stuck) / len(stuck) > 1.3 * sum(flow) / len(flow)


def test_return_to_error_reuses_the_same_problem(days):
    found = 0
    for d in days.values():
        labels = d["labels"]
        term = d["events"]["terminal"]
        for i, l in enumerate(labels):
            if l["subtype"] != "return_to_error":
                continue
            found += 1
            prior = next(x for x in reversed(labels[:i]) if x["state"] == "stuck")
            assert l["meta"]["keywords"] == prior["meta"]["keywords"]       # same story
            sigs_allowed = set(l["meta"].get("error_sigs", []))
            if sigs_allowed and not l["meta"].get("remote"):          # remote sessions are invisible to the hook
                a, b = t(l["start"]), t(l["end"])
                seen = {e["data"]["error_sig"] for e in term
                        if e["data"]["error_sig"] and a <= t(e["timestamp"]) < b}
                assert seen and seen <= sigs_allowed
    assert found > 0, "no return_to_error segment in test seeds"


def test_sensitive_segments_flagged_and_use_sensitive_apps(days):
    from synth.content import SENSITIVE_APPS
    for d in days.values():
        for l in d["labels"]:
            if not l["sensitive"]:
                continue
            a, b = t(l["start"]), t(l["end"])
            apps = {e["data"]["app"] for e in d["events"]["window"] if a <= t(e["timestamp"]) < b}
            assert apps & SENSITIVE_APPS


def short_share(d):
    w = d["events"]["window"]
    return sum(e["duration"] < 2 for e in w) / len(w)


def test_noise_adds_short_window_events(days):
    clean = generate_day(1, DAY, noise=0)
    assert short_share(clean) < 0.2             # only brief command-title and agent-spinner changes remain
    assert all(0.25 < short_share(d) < 0.9 for d in days.values())
    cleans = {s: generate_day(s, DAY, noise=0) for s in days}
    # agent-spinner days are already full of 1 s title frames, so compare volume per seed, share on average
    assert all(len(d["events"]["window"]) > len(cleans[s]["events"]["window"]) for s, d in days.items())
    assert sum(short_share(d) for d in days.values()) > sum(short_share(c) for c in cleans.values())


def test_noise_leaves_labels_untouched(days):
    clean = generate_day(1, DAY, noise=0)
    assert clean["labels"] == days[1]["labels"]
    assert digest(clean) != digest(days[1])


def test_noise_level_scales_event_volume():
    heavy = generate_day(1, DAY, noise=2.0)
    normal = generate_day(1, DAY, noise=1.0)
    clean = generate_day(1, DAY, noise=0)
    n = lambda d: len(d["events"]["window"])
    assert n(clean) < n(normal) < n(heavy)


def test_flicks_last_about_one_watcher_poll(days):
    # real watcher polls ~1 s, so the dominant short duration is 0.96-1.06 s
    durs = [e["duration"] for d in days.values() for e in d["events"]["window"] if e["duration"] < 2]
    near_poll = sum(0.95 <= x <= 1.07 for x in durs) / len(durs)
    assert near_poll > 0.7


def test_most_consecutive_window_events_can_share_an_app(days):
    # terminal spinners, unsaved markers and tab peeks change title, not app
    shares = []
    for d in days.values():
        w = d["events"]["window"]
        shares.append(sum(a["data"]["app"] == b["data"]["app"] for a, b in zip(w, w[1:])) / (len(w) - 1))
    assert max(shares) > 0.3


def test_timestamps_have_millisecond_precision(days):
    for d in days.values():
        for bucket in d["events"].values():
            for e in bucket[:200]:
                assert datetime.fromisoformat(e["timestamp"]).microsecond % 1000 == 0


def test_idle_bins_contain_tiny_mouse_nudges(days):
    tiny = 0
    for d in days.values():
        for e in d["events"]["input"]:
            x = e["data"]
            if x["presses"] == 0 and x["clicks"] == 0 and 0 < x["deltaX"] < 61 and x["scrollY"] == 0:
                tiny += 1
    assert tiny > 0


def titles(d, app=None):
    return {e["data"]["title"] for e in d["events"]["window"] if app is None or e["data"]["app"] == app}


def test_content_is_rich(days):
    for d in days.values():
        assert len(titles(d)) > 80                       # many distinct window titles per day
        assert len({e["data"]["app"] for e in d["events"]["window"]}) >= 8
        urls = {e["data"].get("url") for e in d["events"]["window"]} - {None}
        assert len(urls) > 25


def test_content_varies_across_seeds(days):
    sets = [titles(d) for d in days.values()]
    assert all(a != b for i, a in enumerate(sets) for b in sets[i + 1:])
    assert len({e["data"]["error_sig"] for d in days.values() for e in d["events"]["terminal"]
                if e["data"]["error_sig"]}) > 12


def test_stuck_pages_match_the_error():
    # search results opened while stuck should mention the error class or the library
    # (noise off, so tab peeks and chatbot tabs don't count)
    checked = 0
    for d in (generate_day(s, DAY, noise=0) for s in SEEDS):
        for l in d["labels"]:
            if l["state"] != "stuck":
                continue
            a, b = t(l["start"]), t(l["end"])
            pages = [e["data"]["title"] for e in d["events"]["window"]
                     if a <= t(e["timestamp"]) < b and category(e["data"]) == "browser"
                     and e["duration"] >= 2]
            if len(pages) < 2:
                continue
            checked += 1
            kws = [k.lower().replace("-", "") for k in l["meta"]["keywords"] if k]
            hits = sum(any(k in p.lower().replace("-", "") for k in kws) for p in pages)
            assert hits / len(pages) >= 0.4
    assert checked > 3


def test_stuck_revisits_suspect_files(days):
    revisits = 0
    for d in days.values():
        for l in d["labels"]:
            if l["state"] != "stuck":
                continue
            a, b = t(l["start"]), t(l["end"])
            seq = [e["data"]["title"] for e in d["events"]["window"]
                   if a <= t(e["timestamp"]) < b and e["data"]["app"] == "Code" and e["duration"] >= 2]
            seq = [x.removeprefix("\u25cf ") for x in seq]
            revisits += len(seq) - len(set(seq))
    assert revisits > 0


def test_terminal_title_shows_running_command(days):
    found = 0
    for d in days.values():
        for e in d["events"]["window"]:
            if e["data"]["app"] == "Terminal" and any(k in e["data"]["title"] for k in
                                                     ("pytest", "python", "npm", "make", "cargo")):
                found += 1
    assert found > 20


TOPIC_OF = {"canvas.example.edu": "course", "arxiv.org": "research", "wikipedia.org": "research",
            "semanticscholar.org": "research", "ycombinator.com": "news", "youtube.com": "video",
            "myworkdayjobs.com": "jobs", "linkedin.com": "jobs", "amazon.com": "shopping",
            "reddit.com": "social"}


def test_browsing_stays_on_topic():
    hit = tot = 0
    for d in (generate_day(s, DAY, noise=0) for s in range(1, 13)):
        for l in d["labels"]:
            if l["subtype"] != "browsing":
                continue
            a, b = t(l["start"]), t(l["end"])
            for e in d["events"]["window"]:
                if a <= t(e["timestamp"]) < b and e["data"].get("url"):
                    dom = next((v for k, v in TOPIC_OF.items() if k in e["data"]["url"]), None)
                    tot += 1
                    hit += dom == l["topic"]
    assert tot > 30 and hit / tot > 0.6


def test_mail_title_is_stable_within_a_session(days):
    for d in days.values():
        for l in d["labels"]:
            if l["subtype"] != "email":
                continue
            a, b = t(l["start"]), t(l["end"])
            mail = {e["data"]["title"] for e in d["events"]["window"]
                    if a <= t(e["timestamp"]) < b and e["data"]["app"] == "Mail" and "Inbox" in e["data"]["title"]}
            assert len(mail) <= 2                         # the noise layer may add one flick variant


def test_labels_carry_story_metadata(days):
    for d in days.values():
        for l in d["labels"]:
            if l["state"] == "stuck":
                assert {"mode", "keywords"} <= set(l["meta"])
                if l["meta"]["mode"] == "coding":
                    assert {"project", "lib", "suspects", "variant"} <= set(l["meta"])
            if l["subtype"] == "browsing":
                assert l["topic"]


ALL_SEEDS = list(range(1, 31))


@pytest.fixture(scope="module")
def many():
    return {s: generate_day(s, DAY) for s in ALL_SEEDS}


def test_work_is_not_only_coding(many):
    modes = collections.Counter(l["meta"].get("mode") for d in many.values() for l in d["labels"]
                                if l["state"] in ("flow", "stuck"))
    assert set(modes) >= {"coding", "writing", "analysis"}
    assert min(modes.values()) / sum(modes.values()) > 0.1


def test_non_coding_work_has_no_terminal_or_ide():
    for d in (generate_day(s, DAY, noise=0) for s in ALL_SEEDS):      # noise off: no cmd-tab back to an IDE
        for l in d["labels"]:
            if l["state"] in ("flow", "stuck") and l["meta"].get("mode") in ("writing", "analysis"):
                a, b = t(l["start"]), t(l["end"])
                apps = {e["data"]["app"] for e in d["events"]["window"] if a <= t(e["timestamp"]) < b}
                assert "Code" not in apps
                assert not any(a <= t(e["timestamp"]) < b and e["data"]["error_sig"]
                               for e in d["events"]["terminal"])


def test_flow_has_repeated_errors_too(many):
    # red-green loops: the same failure repeats while the user is doing fine
    assert sum(summarize(d)["flow"]["repeat_error_pct"] or 0 > 0 for d in many.values()) >= 10


def test_stuck_without_error_text_exists(many):
    variants = collections.Counter(l["meta"].get("variant") for d in many.values() for l in d["labels"]
                                   if l["state"] == "stuck" and l["meta"].get("mode") == "coding")
    assert variants["logic_bug"] > 10 and variants["error"] > 10


def test_some_users_type_more_when_stuck(many):
    gaps = []
    for d in many.values():
        s = summarize(d)
        gaps.append(s["stuck"]["presses_per_min"] - s["flow"]["presses_per_min"])
    assert sum(g > 0 for g in gaps) >= 3                 # thrashers exist
    assert sum(g < 0 for g in gaps) >= 3                 # and stallers


def test_chatbot_is_a_weak_label(many):
    kinds = collections.Counter(h["kind"] for d in many.values() for h in d["help_requests"])
    stuck = [l for d in many.values() for l in d["labels"] if l["state"] == "stuck"]
    help_rate = sum(l["outcome"] == "help" for l in stuck) / len(stuck)
    assert kinds["incidental"] / sum(kinds.values()) > 0.3     # many chatbot opens are not stuck
    assert help_rate < 0.35


def nudge_rate(many, pred):
    n = bins = 0
    for d in many.values():
        spans = [(t(l["start"]), t(l["end"])) for l in d["labels"] if pred(l)]
        for e in d["events"]["input"]:
            x = e["data"]
            if e["duration"] != pytest.approx(5.0) or not any(x.values()):
                continue
            mid = t(e["timestamp"]) + 2.5
            n += any(a <= mid < b for a, b in spans)
        bins += sum((b - a) / 5 for a, b in spans)
    return n / bins


def test_meeting_and_away_look_the_same_to_input(many):
    away = nudge_rate(many, lambda l: l["state"] == "distracted")
    meeting = nudge_rate(many, lambda l: l["subtype"] == "meeting")
    assert 0.5 < meeting / away < 2.0


def test_timeline_has_deep_work_and_modest_stuck_share(many):
    flow = [l["duration_s"] / 60 for d in many.values() for l in d["labels"] if l["state"] == "flow"]
    total = sum(l["duration_s"] for d in many.values() for l in d["labels"])
    stuck = sum(l["duration_s"] for d in many.values() for l in d["labels"] if l["state"] == "stuck")
    assert max(flow) > 45                                # deep-work blocks exist
    assert stuck / total < 0.15


def test_noise_rate_does_not_depend_on_state():
    from synth.noise import STATE_MULT
    assert STATE_MULT["flow"] == STATE_MULT["stuck"] == STATE_MULT["neutral"]


def test_categories_cover_work_surfaces():
    from synth.categories import category
    assert category({"app": "Code", "title": "x"}) == "editor"
    assert category({"app": "Google Chrome", "title": "Draft - Google Docs",
                     "url": "https://docs.google.com/document/d/abc/edit"}) == "web_work"
    assert category({"app": "zoom.us", "title": "Zoom Meeting"}) == "meeting"
    assert category({"app": "Google Chrome", "title": "Claude", "url": "https://claude.ai/new"}) == "chat"
    assert category({"app": "\u200eWhatsApp", "title": "x"}) == "messaging"


@pytest.fixture(scope="module")
def clean():
    return {s: generate_day(s, DAY, noise=0) for s in ALL_SEEDS}


def seg_events(d, l, bucket):
    """Events overlapping the segment (a window that stays in front across a boundary counts)."""
    a, b = t(l["start"]), t(l["end"])
    return [e for e in d["events"][bucket] if t(e["timestamp"]) < b and t(e["timestamp"]) + e["duration"] > a]


def test_terminal_only_coding_has_no_ide(clean):
    n = 0
    for d in clean.values():
        for l in d["labels"]:
            if l["state"] in ("flow", "stuck") and l["meta"].get("vim") and l["subtype"] != "review":
                n += 1
                wins = seg_events(d, l, "window")
                assert not any(e["data"]["app"] == "Code" for e in wins)
                edit = [e["data"]["title"] for e in wins if e["duration"] >= 5 and "VIM" in e["data"]["title"]]
                assert edit and all(category({"app": "Terminal", "title": x}) == "terminal_editor" for x in edit)
    assert n >= 10


def test_remote_sessions_are_invisible_to_local_hooks(clean):
    n = 0
    for d in clean.values():
        for l in d["labels"]:
            if l["meta"].get("remote"):
                n += 1
                assert not seg_events(d, l, "terminal") and not seg_events(d, l, "fs")
                assert any("ssh://" in e["data"]["title"] for e in seg_events(d, l, "window"))
    assert n >= 3


def test_ai_help_inside_the_work_app_leaves_no_chat_window(clean):
    inline = [(d, h) for d in clean.values() for h in d["help_requests"] if h["kind"].endswith("_inline")]
    windowed = [h for d in clean.values() for h in d["help_requests"] if not h["kind"].endswith("_inline")]
    assert len(inline) >= 8 and len(windowed) >= 8
    assert 0.15 < len(inline) / (len(inline) + len(windowed)) < 0.85
    for d, h in inline:
        lo, hi = t(h["timestamp"]) - 2, t(h["timestamp"]) + 40
        assert not any(category(e["data"]) == "chat" and lo <= t(e["timestamp"]) < hi
                       for e in d["events"]["window"])


def test_reading_flow_is_scroll_heavy_with_little_typing(clean):
    def per_min(kind_pred):
        press = scroll = mins = 0.0
        for d in clean.values():
            for l in d["labels"]:
                if l["state"] == "flow" and kind_pred(l):
                    inp = seg_events(d, l, "input")
                    press += sum(e["data"]["presses"] for e in inp)
                    scroll += sum(abs(e["data"]["scrollY"]) for e in inp)
                    mins += l["duration_s"] / 60
        return press / mins, scroll / mins, mins
    rp, rs, rm = per_min(lambda l: l["subtype"] == "review")
    wp, ws, wm = per_min(lambda l: l["subtype"] == "work")
    assert rm > 120 and wm > 120
    assert rp < 0.35 * wp and rs > 1.5 * ws


def test_waiting_is_labeled_neutral_and_looks_busy_but_idle(clean):
    builds = agents = 0
    for d in clean.values():
        for l in d["labels"]:
            v = l["meta"].get("variant")
            if v not in ("wait_build", "wait_agent"):
                continue
            assert l["state"] == "neutral" and l["subtype"] == "waiting"
            wins = seg_events(d, l, "window")
            inp = seg_events(d, l, "input")
            mins = l["duration_s"] / 60
            typed = sum(e["data"]["presses"] for e in inp) / mins
            assert typed < 45                      # flow is ~75/min; builds allow a Slack reply, agents a typed instruction
            if v == "wait_build":
                builds += 1
                if not l["meta"].get("remote"):
                    long_runs = [e for e in seg_events(d, l, "terminal") if e["duration"] > 100]
                    assert long_runs and long_runs[0]["data"]["cmd_kind"] == "build"
                if not l["meta"].get("vim"):
                    assert any(e["data"]["app"] == "Terminal" and e["duration"] > 30 for e in wins)
            else:
                agents += 1
                frames = [e for e in wins if "Claude Code" in e["data"]["title"]]
                assert len(frames) > 15
                assert all(category(e["data"]) == "agent" for e in frames)
    assert builds >= 5 and agents >= 3


def test_categories_for_terminal_editing_and_agents():
    assert category({"app": "Terminal", "title": "a.py (proj) - VIM"}) == "terminal_editor"
    assert category({"app": "Terminal", "title": "a.py (ssh://gpu-box/proj) - VIM"}) == "terminal_editor"
    assert category({"app": "Terminal", "title": "proj \u2014 \u2733 Claude Code \u2014 128\u00d734"}) == "agent"
    assert category({"app": "Terminal", "title": "proj \u2014 -zsh \u2014 128\u00d734"}) == "terminal"


def test_writer_roundtrip(tmp_path, days):
    out = write_day(days[1], tmp_path / "d")
    for name in ["window", "afk", "input", "terminal", "fs", "labels", "help_requests"]:
        rows = [json.loads(line) for line in (out / f"{name}.jsonl").read_text().splitlines()]
        assert rows
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["seed"] == 1 and "Synthetic" in manifest["note"]
    export = json.loads((out / "aw-export.json").read_text())
    assert set(export["buckets"]) == {"aw-synth-" + k for k in ["window", "afk", "input", "terminal", "fs"]}
