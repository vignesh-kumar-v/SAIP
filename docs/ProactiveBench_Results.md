# SAIP's gate on ProactiveBench

One run of `python -m llm bench` on ProactiveBench's test set (12 sessions, 233 steps, 87 where an offer was wanted),
with a prompted `qwen3.5:9b-q8_0` and no fine-tuning. Run on 2026-11-12. To repeat it, clone
[thunlp/ProactiveAgent](https://github.com/thunlp/ProactiveAgent) into `ProactiveAgent/` and run the command.

## What it shows

- **The two yes/no questions barely separate wanted from unwanted steps** on this data: AUROC 0.60 for need and 0.63
  for accept, where 0.5 is a coin flip. Everything below follows from that.
- **Every gate that uses the accept probability hardly ever speaks.** The fixed bar, PRISM's bar and SAIP's gate speak
  on 5 to 8% of steps and find 8 to 13% of the wanted ones. When they do speak they are right about 6 times in 10
  (false alarms 0.36 to 0.47, against 0.63 for always speaking), but F1 is far below always speaking (0.14 to 0.21
  against 0.54).
- **SAIP's state-aware gate is not better than PRISM's bar or a fixed bar here.** Its false-alarm rate is the lowest
  (0.36) and its recall is the lowest (0.08). All the intervals overlap, so none of these differences is established.
- **On the writing sessions none of the three gates spoke at all.**
- Calibration made the probabilities better matched to the label (lower ECE) but did not help ranking, and it moved
  almost no decisions.

This matches the PRISM paper's own ablation: with an uncalibrated, non-fine-tuned model its dynamic bar did worse than
a fixed one. The limit here is the quality of the probabilities, not the shape of the gate.

## The report

```
model: qwen3.5:9b-q8_0
sessions: 12   steps: 233   positives: 87   skipped: 0

policies (calibrated = temperatures cross-fitted across sessions; F1 and false-alarm with 95% interval over sessions)
  policy            input  recall   prec    acc    FA    F1  speak   F1 interval   FA interval   
  always              raw    1.00   0.37   0.37  0.63  0.54   1.00   [0.40, 0.65]  [0.52, 0.75]  
  always       calibrated    1.00   0.37   0.37  0.63  0.54   1.00   [0.40, 0.65]  [0.52, 0.75]  
  fixed               raw    0.09   0.53   0.63  0.47  0.16   0.06   [0.00, 0.33]  [0.00, 0.56]  
  fixed        calibrated    0.09   0.53   0.63  0.47  0.16   0.06   [0.00, 0.33]  [0.00, 0.56]  
  need                raw    0.28   0.51   0.63  0.49  0.36   0.20   [0.19, 0.49]  [0.31, 0.69]  
  need         calibrated    0.28   0.51   0.63  0.49  0.36   0.20   [0.19, 0.49]  [0.31, 0.69]  
  static-cost         raw    0.37   0.60   0.67  0.40  0.46   0.23   [0.23, 0.61]  [0.28, 0.53]  
  static-cost  calibrated    0.64   0.39   0.49  0.61  0.49   0.61   [0.35, 0.58]  [0.52, 0.72]  
  prism               raw    0.13   0.61   0.64  0.39  0.21   0.08   [0.04, 0.36]  [0.00, 0.56]  
  prism        calibrated    0.13   0.61   0.64  0.39  0.21   0.08   [0.04, 0.36]  [0.00, 0.56]  
  saip                raw    0.09   0.62   0.64  0.38  0.16   0.06   [0.00, 0.34]  [0.00, 0.62]  
  saip         calibrated    0.08   0.64   0.64  0.36  0.14   0.05   [0.00, 0.29]  [0.00, 0.57]  

saip answered WAIT (counted as not speaking) on 5 steps raw, 6 calibrated.

probability quality against the label
  probe           input   AUROC    ECE  Brier
  p_need            raw   0.597  0.129  0.243
  p_need     calibrated   0.567  0.062  0.235
  p_accept          raw   0.635  0.141  0.240
  p_accept   calibrated   0.575  0.101  0.230

per domain (calibrated): F1 / false-alarm
  code      (112 steps)   fixed 0.23 / 0.47   prism 0.31 / 0.39   saip 0.22 / 0.36
  writing   (121 steps)   fixed 0.00 / 0.00   prism 0.00 / 0.00   saip 0.00 / 0.00

Read this before quoting numbers
- `s` here is the model's own yes/no answer about being stuck, not SAIP's classifier on desktop telemetry, which
  this benchmark does not have. So this tests the shape of the gate, not the full system.
- There is no "accepted" label, so p_accept is calibrated against the same label as p_need (task_status).
- There are only 12 sessions, so the intervals are wide; differences smaller than the intervals mean nothing.
- The PRISM paper's own numbers come from a fine-tuned model and an LLM judge. They are not directly comparable
  to a prompted model scored on task_status.

per-step rows written to data/bench/rows.jsonl
```
