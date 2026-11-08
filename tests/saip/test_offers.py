"""Offers: the record, the dialog and what the answer changes."""
import threading
import time

import pytest

import saip.platform.linux as linux_mod
import saip.platform.windows as windows_mod
from llm.config import AgentConfig
from llm.gate import decide
from llm.config import GateParams
from llm.proposal import FALLBACK, Proposal
from saip import offers
from saip.agent import MAX_COOLDOWN_MULT, Orchestrator
from saip.offers import ACCEPTED, DISMISSED, IGNORED, NO, UNANSWERED, YES, Offer, OfferPresenter
from saip.platform.base import Platform
from saip.platform.linux import Linux
from saip.platform.macos import MacOS, dialog_script
from saip.platform.windows import Windows

G = GateParams()
PROPOSAL = Proposal("p", "Talk it through", "You keep switching windows. Want to talk it through?", True)


def offer(t=100.0):
    d = decide(0.9, 0.9, 0.99, G)
    return Offer(t, d.stuck_score, d.p_need, d.p_accept, d.tau, False, PROPOSAL)


class DialogPlatform(Platform):
    name = "Dialog"

    def __init__(self, answers, dialog=True):
        self.answers, self.dialog, self.asked = list(answers), dialog, []

    def can_ask(self):
        return self.dialog

    def ask(self, title, message, buttons=(), timeout=120, default=True):
        self.asked.append(dict(title=title, message=message, buttons=buttons, timeout=timeout, default=default))
        return self.answers.pop(0)


# ---- the record ------------------------------------------------------------------------------------

def test_a_record_holds_numbers_and_the_offer_text_but_nothing_about_the_work():
    row = offers.record(offer(), ACCEPTED, 3.14159)
    assert set(row) == {"time", "stuck_score", "p_need", "p_accept", "bar", "resampled", "task", "message",
                        "generated", "outcome", "answered_after_s"}
    assert row["outcome"] == ACCEPTED and row["answered_after_s"] == 3.1 and row["message"] == PROPOSAL.message


def test_the_log_round_trips_and_survives_a_torn_last_line(tmp_path):
    path = tmp_path / "x" / "offers.jsonl"
    assert offers.read_log(path) == []
    offers.append_log(offers.record(offer(1.0), ACCEPTED, 2.0), path)
    offers.append_log(offers.record(offer(2.0), IGNORED, None), path)
    with path.open("a", encoding="utf-8") as f:
        f.write('{"time": 3, "outco')                                      # a crash mid-write
    rows = offers.read_log(path)
    assert [r["outcome"] for r in rows] == [ACCEPTED, IGNORED]


def test_summary_counts_outcomes_and_the_acceptance_rate_among_answered_only():
    rows = [{"outcome": o} for o in (ACCEPTED, ACCEPTED, DISMISSED, IGNORED, UNANSWERED)]
    text = offers.summarise(rows)
    assert text.startswith("5 offers") and "2 accepted" in text and "1 dismissed" in text and "67%" in text
    assert offers.summarise([]) == "no offers recorded yet"
    assert "%" not in offers.summarise([{"outcome": IGNORED}])


# ---- the dialog --------------------------------------------------------------------------------------

@pytest.mark.parametrize("answer, outcome", [(YES, ACCEPTED), (NO, DISMISSED), (None, IGNORED)])
def test_each_button_maps_to_an_outcome_and_timing_is_only_kept_when_answered(answer, outcome):
    plat = DialogPlatform([answer])
    got, took = OfferPresenter(plat, timeout_s=30).ask(offer())
    assert got == outcome and (took is None) == (answer is None)
    asked = plat.asked[0]
    assert asked["message"] == PROPOSAL.message and asked["buttons"] == (NO, YES)
    assert asked["timeout"] == 30 and asked["default"] is False              # Return cannot answer for you


def test_without_a_dialog_the_offer_is_unanswered_and_nothing_is_asked():
    for plat, use in ((DialogPlatform([], dialog=False), True), (DialogPlatform([YES]), False)):
        p = OfferPresenter(plat, use_dialog=use)
        assert not p.dialog and p.ask(offer()) == (UNANSWERED, None) and plat.asked == []


def test_handle_logs_the_offer_and_reports_the_outcome(tmp_path):
    seen = []
    p = OfferPresenter(DialogPlatform([NO]), on_outcome=lambda o, out: seen.append(out), log=tmp_path / "o.jsonl")
    assert p.handle(offer()) == DISMISSED and seen == [DISMISSED]
    assert offers.read_log(tmp_path / "o.jsonl")[0]["outcome"] == DISMISSED


def test_offers_are_shown_on_a_background_thread_one_at_a_time(tmp_path):
    done, order = threading.Event(), []

    def on_outcome(o, out):
        order.append((o.time, out))
        if len(order) == 2:
            done.set()
    p = OfferPresenter(DialogPlatform([YES, None]), on_outcome=on_outcome, log=tmp_path / "o.jsonl")
    p.submit(offer(1.0))
    p.submit(offer(2.0))
    assert done.wait(5) and order == [(1.0, ACCEPTED), (2.0, IGNORED)]
    p.stop()


def test_a_crashing_dialog_does_not_kill_the_worker(tmp_path):
    class Boom(DialogPlatform):
        def ask(self, *a, **k):
            raise RuntimeError("no display")
    done = threading.Event()
    p = OfferPresenter(Boom([]), on_outcome=lambda o, out: done.set(), log=tmp_path / "o.jsonl")
    p.submit(offer())
    time.sleep(0.2)
    assert not done.is_set() and p.thread.is_alive()
    p.stop()


# ---- what the answer changes ---------------------------------------------------------------------------

def test_dismissing_stretches_the_quiet_period_and_accepting_resets_it():
    cfg = AgentConfig()
    o = Orchestrator(cfg)
    o.record_outcome(DISMISSED)
    assert o.cooldown_mult == 2.0
    o.record_outcome(IGNORED)
    assert o.cooldown_mult == 3.0
    for _ in range(5):
        o.record_outcome(DISMISSED)
    assert o.cooldown_mult == MAX_COOLDOWN_MULT
    o.record_outcome(ACCEPTED)
    assert o.cooldown_mult == 1.0


def test_an_unanswered_offer_teaches_nothing():
    o = Orchestrator(AgentConfig())
    o.record_outcome(UNANSWERED)
    assert o.cooldown_mult == 1.0 and o.take_outcome() is None


def test_the_last_outcome_is_handed_to_the_next_proposal_exactly_once():
    o = Orchestrator(AgentConfig())
    o.record_outcome(IGNORED)
    assert o.take_outcome() == IGNORED and o.take_outcome() is None


def test_the_cooldown_after_a_dismissed_offer_is_longer():
    from saip.agent import Tick
    cfg = AgentConfig()
    tick = Tick(0.0, 0.9, False, None, 0.1, 1.0, ("Code", "a"))

    def speak():
        from llm.assess import Assessment
        return Assessment(decide(0.9, 0.9, 0.99, G), 0.5, 0.5, False, 2)
    o = Orchestrator(cfg)
    assert o.step(0.0, tick, speak).kind == "offer"
    o.record_outcome(DISMISSED)
    assert o.step(cfg.cooldown_s + 1, tick, speak) is None                     # would have spoken without the dismissal
    assert o.step(cfg.cooldown_s * 2 + 1, tick, speak).kind == "offer"


# ---- the dialogs on each system ----------------------------------------------------------------------------

def test_mac_script_has_no_default_button_when_asked_not_to():
    with_default = dialog_script("t", 'say "hi"', ("No", "Yes"), 45, True)
    without = dialog_script("t", 'say "hi"', ("No", "Yes"), 45, False)
    assert 'default button "Yes"' in with_default and "default button" not in without
    assert 'buttons {"No", "Yes"}' in without and "giving up after 45" in without and 'say \\"hi\\"' in without


def test_mac_ask_passes_the_timeout_and_reads_the_button(monkeypatch):
    import saip.platform.macos as m
    seen = []
    monkeypatch.setattr(m, "run", lambda cmd, timeout=0: (seen.append((cmd[-1], timeout)), (0, "button returned:Help me, gave up:false"))[1])
    assert MacOS().ask("t", "m", ("Not now", "Help me"), timeout=45, default=False) == "Help me"
    assert seen[0][1] == 65 and "default button" not in seen[0][0]


def test_windows_two_button_dialog_is_yes_no(monkeypatch):
    seen = []
    monkeypatch.setattr(windows_mod, "run", lambda cmd, timeout=0: (seen.append((cmd[-1], timeout)), (0, "Yes"))[1])
    assert Windows().ask("t", "m", ("Not now", "Help me"), timeout=45) == "Help me"
    assert "'YesNo'" in seen[0][0] and "Yes = Help me   No = Not now" in seen[0][0] and seen[0][1] == 65


def test_windows_three_button_dialog_keeps_its_old_mapping(monkeypatch):
    monkeypatch.setattr(windows_mod, "run", lambda cmd, timeout=0: (0, "Cancel"))
    assert Windows().ask("t", "m", ("Never", "Not now", "Add")) == "Never"
    monkeypatch.setattr(windows_mod, "run", lambda cmd, timeout=0: (0, "No"))
    assert Windows().ask("t", "m", ("Never", "Not now", "Add")) == "Not now"


def test_linux_two_button_dialog_has_no_extra_button(monkeypatch):
    seen = []
    monkeypatch.setattr(linux_mod, "run", lambda cmd, timeout=0: (seen.append(cmd), (0, ""))[1])
    assert Linux().ask("t", "m", ("Not now", "Help me"), timeout=45) == "Help me"
    cmd = seen[0]
    assert "--extra-button" not in cmd and cmd[cmd.index("--timeout") + 1] == "45"
    assert cmd[cmd.index("--cancel-label") + 1] == "Not now" and cmd[cmd.index("--ok-label") + 1] == "Help me"
    monkeypatch.setattr(linux_mod, "run", lambda cmd, timeout=0: (1, ""))
    assert Linux().ask("t", "m", ("Not now", "Help me")) == "Not now"
    monkeypatch.setattr(linux_mod, "run", lambda cmd, timeout=0: (5, ""))      # zenity's timeout code
    assert Linux().ask("t", "m", ("Not now", "Help me")) is None


def test_linux_three_button_dialog_still_offers_the_extra_button(monkeypatch):
    seen = []
    monkeypatch.setattr(linux_mod, "run", lambda cmd, timeout=0: (seen.append(cmd), (1, "Never"))[1])
    assert Linux().ask("t", "m", ("Never", "Not now", "Add")) == "Never" and "--extra-button" in seen[0]


def test_the_default_fallback_is_a_real_proposal():
    assert FALLBACK.message and not FALLBACK.generated
