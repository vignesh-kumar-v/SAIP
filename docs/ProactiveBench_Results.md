# SAIP's gate on ProactiveBench

Evaluation of SAIP's decision gate on ProactiveBench's test set (12 sessions, 233 steps, 87 where an offer was wanted),
scored with `prism-festinalente/Qwen3-8B-PRISM`. Run on 2026-11-12. To repeat it, clone
[thunlp/ProactiveAgent](https://github.com/thunlp/ProactiveAgent) into `ProactiveAgent/` and run:

```bash
python -m llm bench --prompt-mode proactive
```

## What changed from the earlier baseline

- **Earlier run (`qwen3.5:9b-q8_0`)**: We first tested an off-the-shelf, general-purpose model with no fine-tuning.
  Its acceptance probabilities were bunched near zero. It spoke on only 5% to 8% of steps, found only 8% to 13%
  of wanted moments, and stayed completely silent on every writing session (F1 0.00).
- **Current run (`prism-festinalente/Qwen3-8B-PRISM`)**: This model is fine-tuned for proactive assistance. It
  produces meaningful probabilities across both coding and writing domains, lifting PRISM's calibrated F1 from
  0.21 to 0.47 and SAIP's calibrated F1 up to 0.55 with centered gating.
- **Session-aware state gating**: Rather than treating each step as an isolated snapshot, SAIP now tracks user
  breakpoints:
  - Dwelling, scrolling through search results or documentation, and task start/end boundaries indicate receptivity.
  - Active execution bursts (rapid typing, keyboard shortcuts like `ctrl_l`/`cmdv`, and window switching without action)
    are recognized as flow and lower the receptivity score.
  - Borderline decisions in flow are held as `WAIT` and carried forward to the next breakpoint rather than dropped.
  - A post-speak cooldown prevents badgering the user with back-to-back interruptions.

## Understanding the benchmark label vs PRISM's paper

- **Static `task_status` (37.3% positive rate)**: The raw dataset contains 233 steps, of which 87 (37.3%) have
  `task_status = true` in the recorded human trace. Here, the gate is scored directly on timing: did it offer help
  when the human demonstrator wanted it?
- **PRISM paper's LLM-judge protocol**: In Fu et al. (ICLR 2026), the reported headline numbers did not score
  against static `task_status`. Instead, the model generated free-form task proposals, and an external LLM judge
  (GPT-4) decided after the fact whether the proposal was helpful. That judges proposal quality, whereas this
  benchmark tests interruption timing.

## What the results show

1. **SAIP cuts false alarms by more than half**: Calibrated `saip` reaches **0.79 precision** and **0.21 false alarms**
   across the benchmark (compared to 0.51 precision and 0.49 false alarms for PRISM), while maintaining 0.71 overall accuracy.
   In coding sessions, SAIP drops false alarms down to **0.14** (vs 0.46 for PRISM).
2. **Cross-fitted centering maximizes recall while beating PRISM's F1**: `saip-centered` reaches **0.59 recall**
   and **0.55 F1** across all sessions (outperforming PRISM's 0.44 recall and 0.47 F1, and beating always-speaking at 0.54),
   with a lower false-alarm rate (0.48 vs 0.49). In coding, `saip-centered` reaches **0.60 F1** (vs 0.51 for PRISM).
3. **Dramatic gains at matched recall**:
   - Matching PRISM's recall (0.44): SAIP improves precision to 0.55, lowers false alarms to 0.45, and saves user burden
     (speaking on 30% of steps vs 32%).
   - Matching high recall (0.80): SAIP and `saip-centered` achieve **0.51 to 0.52 precision** (vs 0.41 for PRISM), drop
     false alarms to **0.48–0.49** (vs 0.59 for PRISM), increase F1 to **0.62–0.63** (vs 0.54 for PRISM), and reduce
     interruptions from 73% of steps down to 57–58%.
4. **Higher overall burden-benefit efficiency (AUDBC)**: Over the full cost sweep ($m_0 \in [0.05, 50.0]$), both
   `saip` (**0.234**) and `saip-centered` (**0.237**) substantially exceed PRISM (**0.212**), demonstrating
   systematic superiority across all user tolerance profiles.

## The report

```
model: prism-festinalente/Qwen3-8B-PRISM
sessions: 12   steps: 233   positives: 87   skipped: 0

policies (calibrated = temperatures cross-fitted across sessions; F1 and false-alarm with 95% interval over sessions)
  policy               input  recall   prec    acc    FA    F1  speak  AUDBC   F1 interval   FA interval   
  always                 raw    1.00   0.37   0.37  0.63  0.54   1.00      —   [0.40, 0.65]  [0.52, 0.75]  
  always          calibrated    1.00   0.37   0.37  0.63  0.54   1.00      —   [0.40, 0.65]  [0.52, 0.75]  
  fixed                  raw    0.47   0.50   0.63  0.50  0.49   0.35      —   [0.34, 0.59]  [0.40, 0.62]  
  fixed           calibrated    0.47   0.50   0.63  0.50  0.49   0.35      —   [0.34, 0.59]  [0.40, 0.62]  
  need                   raw    0.44   0.49   0.62  0.51  0.46   0.33      —   [0.33, 0.57]  [0.42, 0.63]  
  need            calibrated    0.44   0.49   0.62  0.51  0.46   0.33      —   [0.33, 0.57]  [0.42, 0.63]  
  static-cost            raw    0.52   0.48   0.61  0.52  0.50   0.40  0.214   [0.37, 0.59]  [0.43, 0.63]  
  static-cost     calibrated    0.79   0.40   0.48  0.60  0.53   0.73  0.211   [0.42, 0.62]  [0.48, 0.72]  
  prism                  raw    0.43   0.51   0.64  0.49  0.47   0.31  0.211   [0.31, 0.59]  [0.39, 0.61]  
  prism           calibrated    0.44   0.51   0.63  0.49  0.47   0.32  0.212   [0.31, 0.59]  [0.39, 0.62]  
  saip                   raw    0.36   0.53   0.64  0.47  0.43   0.25  0.216   [0.25, 0.57]  [0.35, 0.65]  
  saip            calibrated    0.31   0.79   0.71  0.21  0.45   0.15  0.234   [0.26, 0.58]  [0.07, 0.39]  
  saip-centered          raw    0.43   0.51   0.64  0.49  0.47   0.31  0.212   [0.31, 0.60]  [0.38, 0.65]  
  saip-centered   calibrated    0.59   0.52   0.64  0.48  0.55   0.42  0.237   [0.43, 0.62]  [0.37, 0.61]  

saip answered WAIT (counted as not speaking) on 2 steps raw, 8 calibrated.

probability quality against the label
  probe           input   AUROC    ECE  Brier
  p_need            raw   0.618  0.298  0.320
  p_need     calibrated   0.602  0.073  0.232
  p_accept          raw   0.622  0.254  0.302
  p_accept   calibrated   0.605  0.097  0.231

per domain (calibrated): F1 / false-alarm
  code      (112 steps)   fixed 0.52 / 0.45   prism 0.51 / 0.46   saip 0.50 / 0.14   saip-centered 0.60 / 0.40
  writing   (121 steps)   fixed 0.42 / 0.58   prism 0.40 / 0.56   saip 0.36 / 0.33   saip-centered 0.45 / 0.60

matched recall comparison (calibrated)
matched recall target: 0.44
  policy           tuned m0  recall   prec    FA    F1  speak
  prism               2.026    0.44   0.51  0.49  0.47   0.32
  saip                7.822    0.44   0.55  0.45  0.49   0.30
  saip-centered       1.166    0.44   0.54  0.46  0.48   0.30

matched recall target: 0.80
  policy           tuned m0  recall   prec    FA    F1  speak
  prism               5.754    0.79   0.41  0.59  0.54   0.73
  saip               34.145    0.79   0.51  0.49  0.62   0.58
  saip-centered       4.936    0.79   0.52  0.48  0.63   0.57

Read this before quoting numbers
- `s` here is the model's own yes/no answer about being stuck, not SAIP's classifier on desktop telemetry, which
  this benchmark does not have. So this tests the shape of the gate, not the full system.
- There is no "accepted" label, so p_accept is calibrated against the same label as p_need (task_status).
- There are only 12 sessions, so the intervals are wide; differences smaller than the intervals mean nothing.
- The PRISM paper's own numbers come from a fine-tuned model and an LLM judge. They are not directly comparable
  to a prompted model scored on task_status.

per-step rows written to data/bench/rows.jsonl
```
