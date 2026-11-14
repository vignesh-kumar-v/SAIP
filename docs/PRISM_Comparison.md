# Comparing SAIP's benchmarks with PRISM

Recording how SAIP can be compared with PRISM (Fu et al., *PRISM: Festina Lente Proactivity*, ICLR
2026, [arXiv 2602.01532](https://arxiv.org/abs/2602.01532)), what was built to do it, how the comparison was
run, every result so far, and what is still open.

## Summary

** We cannot our numbers next to PRISM's published table: PRISM was
measured on a different benchmark, with different labels and a fine-tuned model. There are three valid routes:

| Route | What it compares | Status |
|---|---|---|
| **A. PRISM's gate on our data** | Our state-aware gate against PRISM's fixed-cost gate, on the same model answers | **Built and run** (this branch) |
| **B. Our pipeline on PRISM's benchmark** (ProactiveBench) | Our model, prompts and gate on real recorded user sessions | **Built and run** (this branch) |
| **C. PRISM's model in our pipeline** | PRISM's fine-tuned Qwen3-8B as our model | Not tried; server script exists |

Route A is the main comparison. Our gate with both state slopes at zero *is* PRISM's gate, so running both on
the same model answers isolates what SAIP adds. Route B is the outside check: data we did not create, from real
people, on the benchmark PRISM itself uses.

**What was done:**

1. Read the PRISM paper and checked what its authors and ProactiveBench's authors have released.
2. Added a comparison tool to the repository: `python -m llm.compare`, plus an `--all-ticks` option for
   `python -m llm replay`.
3. Ran the comparison with a real model, Qwen3.5 9B, on a Colab GPU: 1,000 held-out test ticks, with
   calibration fitted on separate people.
4. Added a ProactiveBench runner (`python -m llm.proactivebench`) and ran SAIP's model, prompts and gate on 353
   moments from real recorded sessions.

**Did we finish the benchmark?** Routes A and B are finished for one configuration: Qwen3.5 9B, default prompts,
no window titles. Route C (PRISM's own model) and any test on real SAIP users are not done.

**Main findings (synthetic data with terminal and commit events, Qwen3.5 9B):**

- At the default costs, SAIP's pipeline beat PRISM's gate on F1, 27.8% against 16.2%, with a 95% interval of
  +3.1 to +21.4 points.
- **At matched recall the advantage disappears.** SAIP spoke almost twice as often; tuned to the same recall,
  PRISM's gate did as well or slightly better.
- **The gain comes from SAIP's classifier trigger, not from the state-aware costs.** PRISM's gate placed behind
  the same trigger scored the best F1 of all (30.9%).
- **SAIP's central claim, fewer interruptions during flow, could not be tested.** The model is so cautious that
  neither gate interrupted more than 0.4% of flow moments.
- **The model is the bottleneck.** Given app names, durations and terminal counts but no titles, it ranks stuck
  moments worse than the classifier does (AUROC 0.615 against 0.778).

**Main findings on real recorded sessions (ProactiveBench, Qwen3.5 9B):**

- **The model's signal holds up on real people.** Against three annotators' "help needed" label its AUROC is
  0.712, in line with the 0.62 to 0.71 it showed on synthetic data. Against ProactiveBench's undocumented
  `task_status` field it is weaker (0.59 to 0.63).
- **The gate is precise but cautious.** When it spoke, 84% of offers came where annotators said help was needed,
  against a 57.5% base rate. But it spoke at only 25 of 120 moments, so on F1 it lost to always speaking (44.7%
  against 73.0%). With the miss cost doubled (1:4) it matched always speaking (73.3%) with better precision.
- **These numbers are not PRISM's.** PRISM scored against LLM-judge votes that are not released. F1 also favours
  speaking on these sets, where always speaking scores 54% and 73%; precision against the base rate and AUROC
  say more here.


### 1. What PRISM reports

PRISM is evaluated on **ProactiveBench**, from [thunlp/ProactiveAgent](https://github.com/thunlp/ProactiveAgent).
It reports Recall, Precision, Accuracy, False-Alarm and F1, plus a cost-sweep summary (AUDBC), the slow-mode
rate, tokens and latency.

Numbers as read from the paper's Table 1 (automatic evaluation, %). **Check them against the PDF before
quoting**: they were extracted by an automated summary.

| Model | Recall | Precision | Accuracy | False-Alarm | F1 |
|---|---|---|---|---|---|
| Qwen3-8B (not fine-tuned) | 73.79 | 73.33 | 67.85 | 26.67 | 73.34 |
| DeepSeek-R1 | 98.12 | 72.35 | 72.96 | 27.64 | 83.28 |
| Qwen3-8B-PRISM | 98.88 | 77.05 | 76.39 | 22.94 | 86.61 |

Other settings from the paper: calibration temperatures `T_need = 0.5` and `T_accept = 0.7`; slow-mode margin
`δ = 0.1`; cost ratios `C_FA : C_FN` swept from 1:4 to 1.2:1.

**Definition to note:** in ProactiveBench's scoring script, "False-Alarm" is `FP / (TP + FP)`, the share of
offers that were unwanted. That is `1 − precision`, not the false-positive rate. SAIP's existing replay used
"flow false alarms" for something else: the share of flow moments interrupted. The new tool prints all three.

## 2. What is released (checked 14 November 2026)

**ProactiveBench** (`thunlp/ProactiveAgent`, Apache-2.0):

- `dataset/test_data/`: 12 sessions (6 coding, 6 writing), grouped as "real_coding" and "real_writing", with
  **233 events** by my count (the eval README says 227). Each event has a time, a sentence describing what the
  user did, candidate tasks, and a boolean `task_status`: 87 true, 146 false. It appears to mark whether help
  was wanted at that event; this needs confirming against the ProactiveAgent paper.
- `eval/calculate_agent_performance.py`: the metric definitions used above. It scores a proposal by a reward
  model's judgement, not by `task_status`.
- The reward model itself: the README says the download is "Coming soon".

**PRISM** (`prism-festinalente/PRISM`, linked from the project page):

- The README says only "Coming Soon...". No code is released.
- It contains the same test files as ProactiveBench. The judge votes PRISM scored against are not included.
- The project page links model weights at `huggingface.co/prism-festinalente/Qwen3-8B-PRISM`. Not downloaded or
  checked.

**Consequence:** PRISM's results cannot be reproduced exactly. Its labels and code are unavailable, and the
paper is inconsistent about the gate's form between Eq. 3.1 and Appendix B (see section 8 of
`State_Aware_Interruption_Formulas.md`). This work uses the Eq. 3.1 form.

## 3. Why the numbers cannot be compared directly

| | PRISM on ProactiveBench | SAIP's replay |
|---|---|---|
| Input | A sentence per event, e.g. "The user clicks and reviews Google search results for ..." | ActivityWatch window, input and AFK events |
| Unit | One event (233 in the test set) | One sampled 5-second tick |
| Positive label | Need AND accept, by majority vote of LLM judges | The generator's `stuck` state; no acceptance label exists |
| Who is asked | The model judges every event | The model is asked only past the classifier's trigger |
| State signal | None | The stuck score, which moves SAIP's costs |
| Model | Fine-tuned Qwen3-8B | Any configured endpoint, not fine-tuned |

So a row of ours beside PRISM's Table 1 would be misleading, and SAIP's contribution cannot run on ProactiveBench
as it is: the stuck score needs window and input telemetry. The clean comparison is to run PRISM's gate on our
data, where both gates see the same probabilities. That is Route A.

## 4. What was built

**`python -m llm replay --all-ticks`** (`src/llm/replay.py`, `src/llm/__main__.py`). The existing replay only
asked the model about ticks past the classifier's trigger, so its "fixed costs" row was PRISM's gate hidden
behind SAIP's classifier, not PRISM as published. The new flag asks about every sampled tick. Each stored row
now also records the trigger threshold for its day, so the comparison can apply the trigger afterwards.

**`python -m llm.compare`** (`src/llm/compare.py`). It reads the stored rows and needs no model, so it runs in
seconds and can be re-run with other settings. It:

- runs PRISM's gate on every tick, and SAIP's trigger plus state-aware gate, on the same model answers;
- adds two ablations: the state-aware gate without the trigger, and PRISM's gate behind the trigger;
- scores everything in PRISM's metrics with ProactiveBench's definitions, plus the false-positive rate and the
  share of flow ticks interrupted;
- gives 95% intervals for SAIP minus PRISM, by bootstrap over whole user-days, since ticks within a day are
  correlated;
- sweeps PRISM's cost ratios (1:4, 1:2, 1:1, 1.2:1);
- compares at matched recall, tuning each gate's miss cost until it reaches a target. Without this, a gate that
  simply speaks less looks better on false alarms.

**Separate calibration for each system** (`--saip-calibration`). SAIP only asks the model past its trigger, so
in real use its temperatures are fitted on those ticks. PRISM's gate sees every tick, so its temperatures are
fitted on every tick. The first version used one calibration for both, which pushed SAIP's probabilities down
and was unfair to it; this was found during the runs and fixed.

**`python -m llm.proactivebench`** (`src/llm/proactivebench.py`), for Route B. It reads ProactiveBench from a local
clone, gives the model the event sentences up to and including each moment, asks SAIP's two questions, and
scores PRISM's fixed-cost gate against two labels (section 7). It fits calibration leave-one-session-out, so no
session is scored with temperatures fitted on itself. It saves each answer as it goes and resumes where it
stopped, so a dropped connection loses nothing; this was needed in practice. `report` re-scores saved answers
without the model.

**Tests** (`tests/llm/test_compare.py`, `tests/llm/test_proactivebench.py`): the metric definitions, which ticks each system may speak at, that zero
slopes make the gates identical, recall matching, day-level resampling, the separate calibrations, loading
rows written before the trigger field existed; and for Route B, the context the model sees, label loading,
resuming, that calibration never sees the session it scores, and the gate.

## 5. How the benchmark was run

**Environment.** SAIP on a Windows 11 laptop. The model was Qwen3.5 9B (`qwen3.5:9b-q8_0`) served by Ollama on a
Colab GPU, using the team's `server.py` (an authenticated Cloudflare tunnel), with thinking turned off. The
laptop alone ran the model on its CPU at 20 to 40 seconds per check, which was too slow; on Colab each model
call took under a second.

**Settings.** Default prompts and gate costs (`c0 : m0 = 1 : 2`, `α = 1.10`, `β = 0.69`), window titles off. The
synthetic days include terminal and commit events, and the replay uses them by default: the classifier ran with
its `with_hooks` profile (trigger 0.10), and the model's summary included lines such as "Terminal: 9 commands, 2
failed". This matches a SAIP user who has installed the hooks. `--no-hooks` gives the stock-ActivityWatch
setting (trigger 0.19), which was not run.

**Data.** The synthetic dataset from `scripts/generate_dataset.py`: 20 people, 10 days each, 1,700 hours;
labelled 62% flow, 15% neutral, 14% distracted and 8% stuck. Calibration is fitted on the dev people and results
are reported on the held-out test people.

**Steps:**

```
python scripts/generate_dataset.py
python -m llm replay --split dev  --fit --calibration dev_cal.json                  # SAIP's calibration (flagged ticks)
python -m llm replay --split test --calibration dev_cal.json                        # existing benchmark, held out
python -m llm replay --split dev  --all-ticks --fit --calibration dev_cal_all.json  # PRISM's calibration (every tick)
python -m llm replay --split test --all-ticks --max-assess 1000 --out rows_test_1000.jsonl --calibration dev_cal_all.json
python -m llm.compare rows_test_1000.jsonl --calibration dev_cal_all.json --saip-calibration dev_cal.json
```

Route B:

```
git clone https://github.com/thunlp/ProactiveAgent                                 # outside the SAIP folder
python -m llm.proactivebench run --pa PATH/TO/ProactiveAgent --out pb_rows.jsonl
python -m llm.proactivebench report pb_rows.jsonl                                  # re-score, no model
```

Each 200-tick replay took about 6 minutes (about 400 model calls); the 1,000-tick run took about 30 minutes
(2,009 calls); the ProactiveBench run about 10 minutes (706 calls). The tunnel dropped once, at moment 319 of
353; rerunning the command finished the remaining 34.

## 6. Results

### 6.1 The existing benchmark: flagged ticks only

This is the replay already on `main`: the model is asked only about ticks past the trigger. 200 ticks each.

| | Dev (calibration fitted here) | Test (held out) |
|---|---|---|
| Share of these ticks labelled stuck | 47.0% | 34.0% |
| AUROC, classifier score | 0.755 | 0.725 |
| AUROC, model `p_need` | 0.703 | 0.708 |
| Calibration error (ECE), raw → calibrated | 0.144 → 0.089 | 0.076 → 0.088 |
| Fitted temperatures | `T_need` = 1.82, `T_accept` = 4.09 | (from dev) |

| Test, held out | Offers | Precision | Recall | Flow ticks interrupted |
|---|---|---|---|---|
| State-aware gate | 29 | 0.72 | 0.31 | 4.6% |
| Fixed costs (PRISM's gate behind the trigger) | 31 | 0.68 | 0.31 | 6.5% |
| Fixed cut, `p_accept` ≥ 0.5 | 3 | 1.00 | 0.04 | 0.0% |

Recall here counts only stuck ticks past the trigger. What this showed:

- **The model carries real signal**: an AUROC of about 0.70 on people it never saw during fitting.
- **Its answers are badly scaled**: raw "needs help" sits near 0.1 although a third or more of these ticks are
  stuck, which is why the live app never made an offer without calibration.
- **Calibration did not transfer**: on test it made the error slightly worse, probably because the stuck share
  differed between dev (47%) and test (34%).
- The two gates were almost identical. This table could not answer the PRISM question anyway, because the
  "fixed costs" row is PRISM's gate behind SAIP's trigger.

### 6.2 First comparison run: 200 ticks of every kind

- Test ticks: 200, of which 21 stuck (10.5%); 14% past the trigger. Calibration on every dev tick:
  `T_need` = 0.71, `T_accept` = 0.81.
- AUROC on every tick: dev classifier 0.851, model 0.587; test classifier 0.778, model 0.706.
- **Inconclusive.** Each gate made 2 offers. Even with SAIP's own calibration, SAIP's 2 offers were both correct
  against 1 of 2 for PRISM's gate, and the F1 interval (0 to +26.1 points) included zero.
- SAIP's rate per flagged tick matched section 6.1 (about one offer per seven flagged ticks). The sample simply
  held too few flagged ticks (28), so the test was rerun with 1,000 ticks.

### 6.3 Main result: 1,000 ticks of every kind

| | |
|---|---|
| Test ticks | 1,000, from 70 user-days |
| Labelled stuck | 88 (8.8%); flow 74.7% |
| Past the classifier's trigger | 10.3% of ticks, and 43.2% of stuck ticks |
| AUROC for stuck, every tick | classifier 0.778, model `p_need` 0.615 |
| Model calls | 2,009 (9 ticks re-checked near the bar) |

**At the default costs (`C_FA : C_FN` = 1 : 2):**

| System | Offers | Recall | Precision | Accuracy | False-Alarm | F1 | Flow ticks interrupted |
|---|---|---|---|---|---|---|---|
| PRISM gate, every tick | 11 | 9.1% | 72.7% | 91.7% | 27.3% | 16.2% | 0.3% |
| **SAIP: trigger + state-aware gate** | 20 | 17.0% | 75.0% | 92.2% | 25.0% | 27.8% | 0.4% |
| State-aware gate, no trigger | 16 | 13.6% | 75.0% | 92.0% | 25.0% | 23.1% | 0.3% |
| PRISM gate behind the trigger | 22 | 19.3% | 77.3% | 92.4% | 22.7% | 30.9% | 0.4% |
| Fixed cut, `p_accept` ≥ 0.5 | 4 | 3.4% | 75.0% | 91.4% | 25.0% | 6.5% | 0.0% |
| Always speak | 1000 | 100% | 8.8% | 8.8% | 91.2% | 16.2% | 100% |

**SAIP minus PRISM's gate, 95% intervals** (2,000 resamples over 70 user-days):

| Metric | Interval | Reading |
|---|---|---|
| F1 | +3.1 to +21.4 points | SAIP better at these costs |
| False-Alarm (share of offers unwanted) | −31.1 to +23.1 points | No difference shown |
| Flow ticks interrupted | −0.3 to +0.6 points | No difference shown |

**Cost-ratio sweep:**

| `C_FA : C_FN` | PRISM's gate, every tick | SAIP |
|---|---|---|
| 1 : 4 | 23 offers, recall 19.3%, F1 30.6% | 28 offers, recall 22.7%, F1 34.5% |
| 1 : 2 | 11 offers, recall 9.1%, F1 16.2% | 20 offers, recall 17.0%, F1 27.8% |
| 1 : 1 | 4 offers, recall 3.4%, F1 6.5% | 12 offers, recall 10.2%, F1 18.0% |
| 1.2 : 1 | 4 offers, recall 3.4%, F1 6.5% | 5 offers, recall 4.5%, F1 8.6% |

SAIP has the higher F1 at every ratio, but it also speaks more at every ratio, so this does not separate "better"
from "more willing to speak".

**At matched recall:**

| System | Offers | Recall | Precision | F1 |
|---|---|---|---|---|
| SAIP at the default costs | 20 | 17.0% | 75.0% | 27.8% |
| PRISM's gate, miss cost raised to 3.4 | 22 | 19.3% | 77.3% | 30.9% |

Recall moves in steps of one stuck tick (1.1%), so this match is within about two ticks. Neither system reached
80% or 90% recall at any cost setting.

### 6.4 What the results show

1. **SAIP's advantage at the default costs is real but explained by caution.** The F1 interval excludes zero,
   but SAIP spoke almost twice as often. Raise PRISM's miss cost until it speaks as often, and it does as well or
   slightly better.
2. **The useful part of SAIP's design here is the trigger.** Asking the model only on classifier-flagged
   moments, with calibration fitted on those moments, lifted PRISM's gate from 16.2% to 30.9% F1. Behind the
   trigger, the state-aware costs did slightly worse than fixed costs (27.8% against 30.9%).
3. **Without a trigger, the state-aware costs helped a little** (23.1% against 16.2% F1), but not at matched
   recall.
4. **SAIP's main hypothesis was not tested.** The claim is that state-aware costs cut interruptions during flow.
   Neither gate interrupted more than 0.4% of flow ticks, so there was nothing to cut. The interval for the
   difference is centred on zero.
5. **The model limits everything.** Its answers rank stuck moments worse than the classifier (0.615 against
   0.778), even with terminal failures in its summary, and sit near 10% almost everywhere, so few answers ever clear the bar.
6. **The trigger caps SAIP's recall.** It passed only 43% of stuck ticks to the model; the rest could never be
   offered help.

### 6.5 Caveats

- **Synthetic data and labels.** The same generator produced the activity and decided which moments count as
  stuck. These results compare decision rules under controlled conditions; they do not show real-world benefit.
- **The gain came from the component most at risk.** The classifier was tuned on this same generator, so its
  trigger may look better here than on real activity. In live testing on Windows it read focused typing across
  a few windows as stuck.
- **One model, one configuration, one run.** 88 stuck ticks. The model's AUROC was 0.706 in the 200-tick run and
  0.615 in the 1,000-tick run, which shows how much small samples move.
- **No interval for the matched-recall comparison yet**, and the match is coarse.
- **No acceptance label.** `T_accept` was fitted on the stuck label as a stand-in.
- **PRISM's slow reasoning pass is not reproduced.** SAIP's re-check near the bar plays a similar role. AUDBC is
  not computed: the paper gives two definitions and the code that would settle it is unreleased.
- **Code version.** `dev_cal.json` was fitted on the code before commit `53be433`. The changes since (a text
  cleaner and a second thinking switch) leave the model's input unchanged with titles off, but a clean rerun
  would remove the doubt.
- **Not comparable with PRISM's published table**: different data, labels and model.

## 7. Route B results: real recorded sessions (ProactiveBench)

### 7.1 What was run

**Label sets.** ProactiveBench's released test data gives two:

- **Recorded sessions** (`dataset/test_data`): 12 sessions recorded from real users, 6 coding and 6 writing, 233
  moments in order. Each is scored against `task_status`, which ProactiveBench does not document. It is true at
  87 moments, nearly always where candidate tasks exist, and reads as "a proposal here was acceptable".
- **Reward-model test set** (`dataset/reward_data/test_data.jsonl`): 120 moments, each with its own event history
  and an explicit `help_needed` label from three annotators. 92 come from the recorded sessions; 28 are generated
  scenes. It was sampled to 30 moments per category, so its 57.5% positive rate is by design, not natural.

**What the model sees.** The event sentences up to and including the moment, oldest first, with relative times
(at most 15), for example "1m 34s ago: The user searches on Google for 'How to capitalize person name in
ruby?'". It is asked SAIP's two unchanged questions.

**Gate.** PRISM's fixed-cost gate. The sentences carry no stuck score, so SAIP's state-aware costs and trigger
cannot apply: Route B tests SAIP's model, prompts and calibration on real data, not its state signal.

**Calibration.** Fitted leave-one-group-out: each recorded session, and each of six folds of the reward set, is
scored with temperatures fitted on the others.

**Metrics.** ProactiveBench's definitions, with the label in place of its reward model, which is unreleased.

### 7.2 Results

| | Recorded sessions (`task_status`) | Reward set (`help_needed`) |
|---|---|---|
| Moments | 233 | 120 |
| Share positive | 37.3% | 57.5% |
| AUROC, `p_need` | 0.592 | 0.712 |
| AUROC, `p_accept` | 0.632 | 0.697 |
| Temperatures (range over folds) | `T_need` 2.02 to 2.91, `T_accept` 1.81 to 2.80 | `T_need` 2.36 to 3.12, `T_accept` 4.68 to 7.41 |
| ECE `p_need`, raw → calibrated | 0.119 → 0.068 | 0.217 → 0.142 |
| ECE `p_accept`, raw → calibrated | 0.138 → 0.048 | 0.294 → 0.121 |

**Decisions at the default costs (1 : 2):**

| System | Sessions: offers | Precision | Recall | F1 | Reward set: offers | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|---|
| Gate, calibrated | 15 | 53.3% | 9.2% | 15.7% | 25 | 84.0% | 30.4% | 44.7% |
| Gate, raw probabilities | 16 | 50.0% | 9.2% | 15.5% | 21 | 90.5% | 27.5% | 42.2% |
| Fixed cut, `p_accept` ≥ 0.5 | 14 | 57.1% | 9.2% | 15.8% | 15 | 93.3% | 20.3% | 33.3% |
| Always speak | 233 | 37.3% | 100% | 54.4% | 120 | 57.5% | 100% | 73.0% |

95% interval for the calibrated gate's F1, resampling whole sessions or folds: 0.0% to 33.3% on the sessions,
39.1% to 51.0% on the reward set.

**Cost-ratio sweep, calibrated gate:**

| `C_FA : C_FN` | Sessions: offers, precision, recall, F1 | Reward set: offers, precision, recall, F1 |
|---|---|---|
| 1 : 4 | 94, 44.7%, 48.3%, 46.4% | 92, 64.1%, 85.5%, 73.3% |
| 1 : 2 | 15, 53.3%, 9.2%, 15.7% | 25, 84.0%, 30.4%, 44.7% |
| 1 : 1 | 1, 0.0%, 0.0%, 0.0% | 2, 100%, 2.9%, 5.6% |
| 1.2 : 1 | 1, 0.0%, 0.0%, 0.0% | 0, –, 0.0%, 0.0% |

**By group, calibrated gate at 1 : 2:**

| Group | Share positive | Offers | Precision | Recall | F1 | Always speak F1 |
|---|---|---|---|---|---|---|
| Coding sessions | 48% | 15 | 53.3% | 14.8% | 23.2% | 65.1% |
| Writing sessions | 27% | 0 | – | 0.0% | 0.0% | 42.9% |
| Reward set, recorded moments | 51% | 16 | 75.0% | 25.5% | 38.1% | 67.6% |
| Reward set, generated moments | 79% | 9 | 100% | 40.9% | 58.1% | 88.0% |

### 7.3 What Route B shows

1. **The model's signal is real on real people, and about as strong as on synthetic data.** Against annotators'
   "help needed" it scores AUROC 0.712; on the recorded moments alone it still makes precise offers (75% against
   a 51% base rate). The synthetic runs gave 0.615 to 0.708.
2. **The gate is precise but speaks too rarely.** At the default costs, most of its offers are right, well above
   the base rate on both sets, but it reaches only 9% to 30% of the moments that needed help. In the writing
   sessions it never spoke.
3. **On F1 it does not beat always speaking.** On these sets help is needed often (37% and 57.5% of moments), so
   always speaking scores a high F1. With the miss cost doubled (1:4) the gate matches always speaking on the
   reward set (73.3% against 73.0%) with better precision (64.1% against 57.5%).
4. **Calibration helps the probabilities but barely changes the decisions.** ECE roughly halved; offers changed by
   at most four.
5. **The `task_status` results are weaker throughout.** That may be the model, or it may be the label, whose
   meaning is undocumented. The annotator label is the more trustworthy of the two.

### 7.4 Caveats for Route B

- **Not PRISM's protocol.** PRISM scored against LLM-judge votes that are not released, and ProactiveBench scores
  against a reward model that is not released either. PRISM's 73.34 F1 for un-fine-tuned Qwen3-8B is a loose
  reference; the reward set's 73.3% at 1:4 equals it by coincidence, not by like-for-like comparison.
- **Small samples.** 12 sessions and 120 moments. The sessions' F1 interval runs from 0 to 33%.
- **SAIP's prompts were written for activity summaries**, not event sentences. They were used unchanged.
- **No state signal**, so SAIP's contribution (the trigger and state-aware costs) is not tested here.
- **The reward set's rates are designed**, not natural, and 28 of its moments are generated, not recorded.

## 8. Status: is the benchmark finished?

| Part | Status |
|---|---|
| Research: how to compare with PRISM | Done (sections 1 to 3) |
| Comparison tool (Route A) | Done, with tests |
| Route A with a real model | Done for Qwen3.5 9B, default prompts, titles off, with terminal and commit events |
| Route A with other configurations (titles on, no hooks, other prompts, other models) | Not done |
| Route B: our pipeline on ProactiveBench's real recorded sessions | Done for Qwen3.5 9B, default prompts |
| Route C: PRISM's own model in our pipeline | Not done |
| Test on real users of SAIP | Not done; needs labelled data |

## 9. Next steps

1. **Window titles on the synthetic data.** A setting change (`include_titles = true`, in a separate config file
   so the live app is unaffected). It may give the model enough to go on that the gates start speaking, which
   is what the flow-interruption claim needs. About 45 minutes of runs.
2. **Route B with other settings.** The ProactiveBench run takes about 10 minutes, so it is cheap to repeat with
   a different model, a prompt written for event sentences, or the 1:4 costs that matched always speaking. It
   is also the quickest way to try PRISM's own model (Route C) once that is served.
3. **Route C: PRISM's model.** The team's `custom_server.py` serves `Qwen3-8B-PRISM` with vLLM. It needs an L4 or
   larger GPU, and the model may not answer SAIP's yes/no questions in the expected format; `saip doctor` will
   show whether it does.
4. **A prompt that describes stuck behaviour.** Likely the largest gain for the model, but it changes the method,
   so it is a team decision.
5. **Real labels.** SAIP already records offer outcomes in `offers.jsonl`. Adding an "I'm stuck now" item to the
   tray menu would record the moments the gate missed. A week of use by a few teammates would give the first
   real labels for the classifier and both gates.
6. **Tool improvements:** an interval for the matched-recall comparison; saving rows as `llm replay` runs, as the
   ProactiveBench runner already does, so a dropped connection does not lose a 30-minute run.

## 10. Pitfalls when writing this up

- Say which "false alarm" is meant: PRISM's `FP / (TP + FP)`, the false-positive rate, or the share of flow
  moments interrupted.
- Do not put our numbers in the same table as PRISM's Table 1 without saying the data, labels and model differ.
- Compare at matched recall, not only at the default costs.
- Tune on dev people and report on test people. `replay --fit` refuses the test split for this reason.
- Fit each system's calibration on the ticks it actually sees.
- On ProactiveBench, report precision against the base rate and AUROC beside F1, and always show always-speak:
  help is needed so often there that speaking every time scores a high F1.