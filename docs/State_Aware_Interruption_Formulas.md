# State-Aware Interruption: Formulas and Derivations

Nov 3, 2026 · @Barath Suresh

Revised Nov 5, 2026 to match the Phase 2 classifier in `src/classifier`: one stuck score $s_t$, flow defined as $1-s_t$.

## Overview

The agent speaks only when the chance its offer is accepted clears a bar, and that bar moves with the user's state: higher in flow, lower when stuck. Four quantities feed one rule. The Phase 2 classifier turns recent activity into one stuck score from 0 (flow) to 1 (stuck), and flow is its complement. Two yes/no questions to the model give the chance help is needed and the chance an offer would be accepted. The gate compares them and picks speak, wait, or quiet.

The gate is PRISM's Bayes-risk threshold (Fu et al., ICLR 2026). The new part is that its two costs are functions of user state instead of constants. Everything works without an LLM: the probabilities can come from any model, including a logistic regression or a constant.

**Pipeline:** the classifier gives the stuck score $s_t$. The two probes give $p_{\text{need}}$ and $p_{\text{accept}}$. The gate turns $s_t$ and $p_{\text{need}}$ into one bar $\tau_t$, compares $p_{\text{accept}}$ with it, and picks speak, wait or quiet.

State only enters through the costs. A wait loops back and the whole gate re-runs with fresh scores at the next natural break.

### Symbols

| Symbol | Meaning | Range | Set by |
| --- | --- | --- | --- |
| t | A decision point (one logged event) | time | Logger |
| $X_t$ | Context at t: recent events in the window | events | Logger |
| $s_t$ | Stuck score from the classifier, so $\mathrm{stuck}_t = s_t$ | 0 to 1 | Classifier |
| $\mathrm{flow}_t$ | How focused the user looks, defined as $1 - s_t$ | 0 to 1 | Classifier |
| $\mathrm{stuck}_t$ | How stuck the user looks, equal to $s_t$ | 0 to 1 | Classifier |
| $p_{\text{need}}$ | Probability help is needed now | 0 to 1 | Probe or model |
| $p_{\text{accept}}$ | Probability an offer made now would be accepted | 0 to 1 | Probe or model |
| $C_{\text{FA}}$ | Cost of a false alarm (speaking when the offer is not wanted) | > 0 | Cost function |
| $C_{\text{FN}}$ | Cost of a miss (silence when help was needed and would be accepted) | > 0 | Cost function |
| $c_0$ | Base false-alarm cost, no state information | > 0 | You (fixed) |
| $m_0$ | Base miss cost, no state information | > 0 | You (fixed) |
| α (alpha) | How strongly flow raises the false-alarm cost | ≥ 0 | You (fixed) |
| β (beta) | How strongly stuck raises the miss cost | ≥ 0 | You (fixed) |
| τ (tau) | The bar: minimum $p_{\text{accept}}$ needed to speak | 0 to 1 | Computed |
| δ (delta) | Near-miss margin for the wait action | about 0.1 | You (fixed) |
| T | Calibration temperature | > 0 | Fitted on held-out labels |
| $w_k$, b | Feature weights and bias inside the classifier | real | weights.json |
| $\theta_{\text{trig}}$ | Stuck score above which the LLM probes run | 0.10 (hooks), 0.19 (no hooks) | weights.json |
| σ(·), logit(·) | Sigmoid and its inverse: σ(z) = 1 / (1 + e^(−z)), logit(p) = ln(p / (1 − p)) |  | Math |

Fixed means chosen once on held-out days and kept constant while the agent runs. Only flow, stuck, $p_{\text{need}}$ and $p_{\text{accept}}$ change at every decision point.

## 1. State score (the Phase 2 classifier)

State comes from the classifier in `src/classifier`, not from a separate flow detector. It produces one number per tick: $s_t \in [0,1]$, where 0 is flow and 1 is stuck. No LLM is involved. The code is the source of truth: if a value here disagrees with `features.py`, `scorer.py` or `weights.json`, the code wins.

**Clock and window.** One tick every $\Delta = 5$ s. Features look back over a time window of $W = 60$ s (not a count of events, because typing is a rate and window switches are minutes apart). Three features use a longer memory, listed in the table.

**Clip function.** $\mathrm{clip}(x)$ keeps a value inside 0 to 1:

$$
\mathrm{clip}(x) = \min(1, \max(0, x))
$$

### Ten features

Every feature is causal: it uses only what was known at that tick. A window visit counts only after 2 s, a command only after it finishes, and AFK only after the watcher's 180 s timeout.

| Feature $f_k$ | Definition (all in 0 to 1) | Scale ("a lot") |
| --- | --- | --- |
| toggling | clip(app switches per min over $W$ / 6) | 6 per min |
| revisits | clip(returns per min to a window seen in the last 300 s / 3) | 3 per min |
| stalling | share of the last $W$ of ticks with no input while a work app is in front | — |
| typing | share of the last $W$ of ticks with key presses | — |
| ai\_share | share of the last $W$ with an AI tool in front | — |
| circling | see below, over 300 s | 10 returns, 10 windows |
| reaching | clip(windows not seen for 30 min, in the last 300 s / 6) | 6 new windows |
| failures | clip(failing commands per min over $\max(W,180\,\text{s})$ / 1) | 1 per min |
| repeats | clip(same error signature again within 600 s, per min / 0.5) | 0.5 per min |
| progress | clip(commits in the last 300 s) | 1 commit |

Circling separates being stuck from rapid coding. Both switch constantly, but rapid coding keeps reaching new files and pages, while being stuck keeps returning to the same few. With $R$ the number of returns (visits beyond the distinct windows) and $D$ the number of distinct windows in the last 300 s:

$$
\mathrm{circling} = \min\!\left(\frac{R}{10},\,1\right)\cdot \max\!\left(1-\frac{D}{10},\,0\right)
$$

The failures, repeats and progress features exist only when terminal and file hooks exist. A stock ActivityWatch install has no such streams.

### Raw score and smoothing

Each profile has a bias $b$ and a weight $w_k$ per feature. The raw score is a logistic, and the reported score is an exponential moving average (EMA) of it:

$$
r_t = \sigma\!\Big(b + \sum_k w_k f_{k,t}\Big), \qquad s_t = \alpha_s\, r_t + (1-\alpha_s)\, s_{t-1}, \qquad s_0 = \sigma(b), \quad \alpha_s = 0.1
$$

($\alpha_s$ is the smoothing constant. It is a different symbol from the cost parameter $\alpha$ in section 4.) At one tick per 5 s, $s_t$ moves halfway to a steady reading in about 33 s. A single pause cannot spike it.

### Weights (hand set, in `weights.json`)

| Feature | with hooks | window, input, AFK only |
| --- | --- | --- |
| bias $b$ | −3.5 | −3.2 |
| circling | 7.0 | 8.0 |
| revisits | 1.0 | 1.5 |
| toggling | 0.3 | 0.5 |
| stalling | 0.4 | 0.4 |
| reaching | −0.5 | −0.5 |
| typing | 0.0 | 0.0 |
| ai\_share | −0.8 | −1.0 |
| failures | 3.0 | n/a |
| repeats | 2.0 | n/a |
| progress | −2.5 | n/a |
| LLM trigger $\theta_{\text{trig}}$ | 0.10 | 0.19 |

Typing has weight 0: steady typing was not a useful signal on the synthetic data, so the earlier "steady typing means flow" idea is not used. Both profiles are chosen by whether a terminal stream exists.

### Where $s_t$ sits (dev people, hooks profile)

| True state | median | 75th pct | 95th pct |
| --- | --- | --- | --- |
| flow | 0.04 | 0.06 | 0.11 |
| stuck | 0.10 | 0.45 | 0.97 |

Flow sits near the prior, $\sigma(-3.5)\approx 0.03$. Stuck is spread out: about a quarter of stuck ticks stay under 0.06. These are results on our own generator, so they test the pipeline and are not evidence about real users.

### Flow is the complement

The classifier scores one axis, so there is no separate flow score:

$$
\mathrm{stuck}_t = s_t, \qquad \mathrm{flow}_t = 1 - s_t
$$

The damper $\mathrm{flow}'=\mathrm{flow}\,(1-\mathrm{stuck})$ from the earlier draft is dropped. With complementary scores it would count stuck twice. The "both high" problem it fixed cannot happen.

### Paused or away

The classifier does not set flow and stuck to 0 when the user is away. It pauses scoring and freezes $s_t$ when any of these holds:

| Reason | Meaning |
| --- | --- |
| no\_input | no input events cover the tick |
| no\_window | no window event (a sleeping laptop) |
| meeting | a meeting app is in front |
| afk | the AFK watcher reports away (known only after 180 s) |
| locked | lock screen |

While paused, the gate makes no decision: no probes, no speaking. After 1800 s of pause, $s_t$ resets to $\sigma(b)$, so a coffee break does not wipe the score but a long absence does. Quiet staring at the screen is still scored, because the stalling feature reads it. If the user returns to the same error, revisits and repeats raise $s_t$ on their own.

### Using the score to save LLM calls

The probes of section 2 run only when the cheap score is high enough:

$$
s_t \ge \theta_{\text{trig}} \quad (0.10 \text{ with hooks}, \; 0.19 \text{ without})
$$

Below $\theta_{\text{trig}}$ the gate stays quiet without calling the model.

### Fitting the weights

The weights are hand set. To fit them, use a logistic regression on the same ten features, still LLM-free, with the same form as $r_t$. Fit on the labels, cross-validated by person (the repo splits people, not days: dev people 1 to 14, test people 15 to 20). Use different labels for fitting and for evaluation.

## 2. Confidence probes and calibration

The gate needs two calibrated probabilities. Use two questions, not one, because PRISM's ablation shows that using acceptance alone raised false alarms to 62.5%, while using both signals kept them at 22.9%.

| Probe | Question asked | Gives |
| --- | --- | --- |
| Need | "Does the user need help right now? Answer yes or no." | $p_{\text{need}}$ |
| Accept | "If you offered help now, would the user accept it? Answer yes or no." | $p_{\text{accept}}$ |

### From the model's answer to a probability

Read the log-probabilities of the tokens "yes" and "no" ($l_{\text{yes}}$, $l_{\text{no}}$) and normalize over just those two:

$$
p = \frac{e^{\ell_{\text{yes}}}}{e^{\ell_{\text{yes}}} + e^{\ell_{\text{no}}}} = \sigma(\ell_{\text{yes}} - \ell_{\text{no}})
$$

Why two tokens only: the model also puts probability on other tokens ("Yes", spaces, punctuation). Renormalizing over yes and no turns the answer into a proper binary probability.

### Resampling for borderline cases

When logprobs are unavailable, or p is near the bar, sample the answer k times at temperature above 0 and use the share of yes:

$$
\hat{p} = \frac{1}{k}\sum_{j=1}^{k} \mathbb{1}[\text{answer}_j = \text{yes}], \qquad \mathrm{SE}(\hat{p}) = \sqrt{\frac{\hat{p}(1-\hat{p})}{k}}
$$

With k = 5 the standard error near p = 0.5 is about 0.22, wider than the 0.1 wait margin. Use k ≥ 10 if the share is the main signal (standard error about 0.16), or prefer logprobs.

### Temperature calibration

Raw model probabilities are usually over- or under-confident. Rescale the log-odds by one constant per probe:

$$
\hat{p} = \sigma\!\left(\frac{\mathrm{logit}(p)}{T}\right)
$$

T < 1 makes probabilities more extreme, T > 1 pulls them toward 0.5, and T = 1 changes nothing. Fit T on a held-out labeled set by minimizing the negative log-likelihood:

$$
T^{*} = \arg\min_{T > 0} \; -\sum_{i} \Big[ y_i \ln \hat{p}_i(T) + (1 - y_i) \ln\big(1 - \hat{p}_i(T)\big) \Big]
$$

Here $y_i$ is 1 if help was needed (hotkey press or chatbot visit soon after), else 0. Fit $T_{\text{need}}$ and $T_{\text{accept}}$ separately. PRISM found $T_{\text{need}}$ = 0.5 and $T_{\text{accept}}$ = 0.7 for its model; yours will differ.

Why it matters: in PRISM's no-calibration ablation, a need-dependent threshold scored F1 70.3 against 80.7 for a fixed 0.5 cut. A moving bar only helps if the probabilities feeding it are honest.

### Safety clip

Keep probabilities away from exactly 0 and 1 so the threshold and log-odds stay finite:

$$
p \leftarrow \min(1 - \epsilon, \max(\epsilon, p)), \qquad \epsilon = 10^{-3}
$$

### LLM-free stand-ins for testing

| Input | Stand-in |
| --- | --- |
| $p_{\text{need}}$ | Logistic regression on features that are not already inside the classifier score (for example search activity or time since the last save), or a constant |
| $p_{\text{accept}}$ | The fraction of past offers accepted, or a constant such as 0.7 |

Do not build $p_{\text{need}}$ from the stuck score itself, or stuck is counted twice: once in the probability and once in the cost.

## 3. The gate: why the threshold has this form

Speak when the expected cost of speaking is lower than the expected cost of staying silent. This is the Bayes-optimal rule under asymmetric costs (PRISM, Appendix A), and the threshold follows from it in four lines.

**Step 1: list what can go wrong.** Correct decisions cost 0.

| Action | Goes wrong when | Probability | Cost |
| --- | --- | --- | --- |
| Speak | The offer is not accepted | 1 − $p_{\text{accept}}$ | $C_{\text{FA}}$ |
| Stay silent | Help was needed and would have been accepted | $p_{\text{need}}$ · $p_{\text{accept}}$ | $C_{\text{FN}}$ |

The silent row multiplies the two probabilities, which assumes need and acceptance are independent given the context. PRISM makes the same assumption.

**Step 2: expected cost of each action.**

$$
\mathbb{E}[\text{cost} \mid \text{speak}] = (1 - p_{\text{accept}})\, C_{FA}
$$

$$
\mathbb{E}[\text{cost} \mid \text{silent}] = p_{\text{accept}}\, p_{\text{need}}\, C_{FN}
$$

**Step 3: speak when speaking is no worse.**

$$
(1 - p_{\text{accept}})\, C_{FA} \le p_{\text{accept}}\, p_{\text{need}}\, C_{FN}
$$

**Step 4: solve for $p_{\text{accept}}$.** Expand, move every $p_{\text{accept}}$ term to one side, and divide by the positive bracket:

$$
C_{FA} \le p_{\text{accept}}\,\big(C_{FA} + p_{\text{need}}\, C_{FN}\big) \;\;\Longrightarrow\;\; p_{\text{accept}} \ge \tau = \frac{C_{FA}}{C_{FA} + p_{\text{need}}\, C_{FN}}
$$

### The same rule in log-odds

Divide both sides of Step 3 by (1 − $p_{\text{accept}}$) · $p_{\text{need}}$ · $C_{\text{FN}}$ and take logs:

$$
\mathrm{logit}(p_{\text{accept}}) \ge \ln C_{FA} - \ln C_{FN} - \ln p_{\text{need}}
$$

This form shows what each input does in one line. The bar in log-odds is a sum of three terms, so each one pushes independently. It is also the form the state-aware costs in section 4 plug into most cleanly.

### Reading the threshold

| Case | τ | Behavior |
| --- | --- | --- |
| $p_{\text{need}}$ → 0 | → 1 | Never speak: no help is needed |
| $p_{\text{need}}$ = 1, $C_{\text{FA}}$ = $C_{\text{FN}}$ | 0.5 | Coin-flip bar |
| $C_{\text{FN}}$ ≫ $C_{\text{FA}}$ | → 0 | Speak at almost any acceptance |
| $C_{\text{FA}}$ ≫ $C_{\text{FN}}$ | → 1 | Speak only when acceptance is near certain |
| Both costs × k | unchanged | Only the ratio $C_{\text{FA}}$ : $C_{\text{FN}}$ matters |

### Why this formula and not a fixed cut

- **It is derived, not chosen.** Any other rule has a higher expected cost under the same costs and probabilities.
- **The costs are interpretable knobs.** $C_{\text{FA}}$ : $C_{\text{FN}}$ says how many false alarms you would trade for one missed moment of help.
- **It is PRISM's gate.** Using it unchanged keeps the comparison to the base paper clean, so any gain comes from the state-aware costs alone.

The last point carries a condition: the gate is optimal only when the costs are right. PRISM fixes them for the whole session, and its authors list that as a limitation. Section 4 removes that limitation.

## 4. State-aware costs (the contribution)

Use multiplicative (log-linear) costs as the main form. They turn the whole gate into one sigmoid of a sum, so every knob has a meaning that does not depend on the others.

$$
C_{FA}(s_t) = c_0 \, e^{\alpha \cdot \mathrm{flow}_t}, \qquad C_{FN}(s_t) = m_0 \, e^{\beta \cdot \mathrm{stuck}_t}
$$

The reasoning is the office-colleague argument from the methodology. Interrupting deep focus is costlier than usual, so $C_{\text{FA}}$ grows with flow. Missing someone who is stuck is costlier than usual, so $C_{\text{FN}}$ grows with stuck. With α = β = 0 both costs are constant and the gate is exactly PRISM's.

### The full gate in one line

Plug the costs into the log-odds rule from section 3:

$$
L_t = \ln\frac{c_0}{m_0} + \alpha\,(1-s_t) - \beta\, s_t - \ln p_{\text{need}} \;=\; \ln\frac{c_0 e^{\alpha}}{m_0} - (\alpha+\beta)\, s_t - \ln p_{\text{need}}
$$

$$
\tau_t = \sigma(L_t) = \frac{1}{1 + e^{-L_t}}, \qquad \text{speak} \iff \mathrm{logit}(p_{\text{accept}}) \ge L_t
$$

Why τ = σ(L): τ = $C_{\text{FA}}$ / ($C_{\text{FA}}$ + $p_{\text{need}}$ $C_{\text{FN}}$) = 1 / (1 + $p_{\text{need}}$ $C_{\text{FN}}$ / $C_{\text{FA}}$), and $p_{\text{need}}$ $C_{\text{FN}}$ / $C_{\text{FA}}$ = e^(−L).

| Term in $L_t$ | Meaning | Direction |
| --- | --- | --- |
| ln($c_0$ / $m_0$) | Base strictness with no state information | Fixed |
| α · flow | Focus raises the bar | Up |
| −β · stuck | Being stuck lowers the bar | Down |
| −ln $p_{\text{need}}$ | Likely need lowers the bar | Down |

### Proof that the bar moves the intended way

The sigmoid is increasing, so τ moves the same way as L:

$$
\frac{\partial L_t}{\partial s_t} = -(\alpha+\beta) \le 0, \qquad \frac{\partial L_t}{\partial\, p_{\text{need}}} = -\frac{1}{p_{\text{need}}} < 0
$$

So the bar rises with focus (low $s_t$), falls when stuck (high $s_t$), and falls with need, for every value of the other inputs. The simulation in section 6 confirms this on a grid for both cost forms.

### What α and β mean

At full flow, false alarms cost e^α times their base. At full stuck, misses cost e^β times their base. So set them from a plain statement:

$$
\alpha = \ln k_{\text{flow}}, \qquad \beta = \ln k_{\text{stuck}}
$$

| Statement | Value |
| --- | --- |
| "Interrupting full flow is 3 times worse than usual" | α = ln 3 ≈ 1.10 |
| "Missing a stuck user is 2 times worse than usual" | β = ln 2 ≈ 0.69 |

These are starting values to tune on held-out days.

### Only the sum α + β matters

Because flow = 1 − s, the gate depends on the state only through one slope and one offset:

$$
L_t = \underbrace{\ln\frac{c_0 e^{\alpha}}{m_0}}_{\text{offset}} \; - \; \underbrace{(\alpha+\beta)}_{\text{slope}}\, s_t \; - \; \ln p_{\text{need}}
$$

Once $c_0 : m_0$ is retuned for a target recall, α and β are not separately identifiable: only $\gamma=\alpha+\beta$ changes the gate. The split is still useful for explaining the costs ("interrupting flow is $e^{\alpha}$ times worse, missing a stuck user is $e^{\beta}$ times worse") but not for ablations.

### How far state can move the bar

State is bounded, so the bar is bounded. At a given $p_{\text{need}}$:

$$
\sigma\!\left(\ln\tfrac{c_0}{m_0} - \beta - \ln p_{\text{need}}\right) \le \tau_t \le \sigma\!\left(\ln\tfrac{c_0}{m_0} + \alpha - \ln p_{\text{need}}\right)
$$

Worked example with $c_0$ : $m_0$ = 1 : 2 (PRISM's middle setting), α = 1.10, β = 0.69, $p_{\text{need}}$ = 0.6, at stuck scores taken from the classifier's measured range (section 1):

| Situation | $s_t$ | $L_t$ | τ |
| --- | --- | --- | --- |
| Perfect flow (limit) | 0 | 0.92 | 0.71 |
| Typical flow (median) | 0.04 | 0.84 | 0.70 |
| Typical stuck (median) | 0.10 | 0.74 | 0.68 |
| Upper-quartile stuck | 0.45 | 0.11 | 0.53 |
| Full stuck (limit) | 1 | −0.88 | 0.29 |

The bar can move from 0.71 to 0.29 on state alone. Note the shape: most flow ticks and the weaker stuck ticks all sit at a bar of about 0.7, and the bar falls only when the classifier score becomes large (about 0.4 and above). With the current weights, strong stuck moments get a low bar and everything else stays strict.

### Why multiplicative rather than additive

The additive form $C_{\text{FA}}$ = $c_0$ + α · flow behaves the same in simulation (section 6), and it remains a valid ablation. Its derivative also has the right sign:

$$
\frac{\partial \tau}{\partial s} = -\frac{p_{\text{need}}\,\big(\alpha\, C_{FN} + \beta\, C_{FA}\big)}{(C_{FA} + p_{\text{need}} C_{FN})^2} < 0, \qquad C_{FA}=c_0+\alpha(1-s),\; C_{FN}=m_0+\beta s
$$

The additive form has one flaw: the meaning of α depends on $c_0$. In the check below, the same α = 2 multiplies the false-alarm cost by 5 when $c_0$ = 0.5 but by only 1.5 when $c_0$ = 4. The multiplicative form gives 3 times in every case, so tuning $c_0$ never silently changes what α does.

| $c_0$ | Additive (α = 2): full-flow multiplier | Multiplicative (α = 1.1): full-flow multiplier |
| --- | --- | --- |
| 0.5 | 5.0× | 3.0× |
| 1 | 3.0× | 3.0× |
| 4 | 1.5× | 3.0× |

Both forms keep costs positive. The multiplicative form keeps them positive even if α or β were negative.

## 5. Actions: speak, wait, quiet

The gate picks one of three actions. Wait is the only one PRISM does not have.

$$
a_t = \begin{cases} \text{speak} & p_{\text{accept}} \ge \tau_t \\ \text{wait} & \tau_t - \delta \le p_{\text{accept}} < \tau_t \;\text{ and }\; \mathrm{flow}_t > \theta_{\text{flow}} \\ \text{quiet} & \text{otherwise} \end{cases}
$$

| Symbol | Meaning | Start value |
| --- | --- | --- |
| δ | How close to the bar counts as a near miss | 0.1 (PRISM's best margin) |
| $\theta_{\text{flow}}$ | Flow level above which a near miss is deferred (flow = 1 − $s_t$, so 0.5 means $s_t$ < 0.5) | 0.5 |

The probes run only when $s_t \ge \theta_{\text{trig}}$ (section 1), so a wait can only arise for $\theta_{\text{trig}} \le s_t < 1-\theta_{\text{flow}}$, that is $0.10 \le s_t < 0.5$ with the hook profile. Below $\theta_{\text{trig}}$ the answer is quiet without calling the model.

Why wait exists: a near miss in flow usually means help is likely needed but this moment is costly. Dropping the offer throws away a likely need, and speaking now pays the flow cost. Waiting for a cheaper moment avoids both.

PRISM uses the same margin δ for a different purpose: to trigger an extra slow reasoning pass near the bar. You can use both. A near miss can first get a resample (section 2), and only a near miss that survives it is deferred.

### Natural break and re-check

A wait creates a pending offer. Re-run the whole gate, with fresh state scores and probes, at the first natural break:

- typing stops for at least 5 seconds after a burst
- a window switch
- a file save or a commit

At a break, flow is usually lower, so the bar has dropped. If $p_{\text{accept}}$ now clears it, speak. If not, the offer stays pending.

### Limits that keep the agent from nagging

| Rule | Formula | Start value |
| --- | --- | --- |
| Wait expiry | Drop a pending offer after $W_{\text{max}}$ seconds | 600 s |
| Cooldown after speaking | No new offer for R seconds after any offer | 300 s |
| One pending offer | A new near miss replaces the old one | — |

The cooldown matters for the headline metric. Without it, a stuck user who declines an offer can get the same offer at every event, which inflates false alarms.

## 6. Does it work? Checks run on these formulas

With a reliable state signal, the state-aware gate roughly halves unwanted interruptions during flow at the same recall as PRISM's fixed gate. With an unreliable signal it does harm. These results come from the script verify\_formulas.py: exact checks plus 40,000 synthetic decision points per setting.

> **Scope after the classifier change.** That simulation drew flow and stuck as two separate signals. The classifier gives one score with flow = 1 − s, so the "flow-only" and "stuck-only" rows below no longer isolate separate signals (see "Only the sum α + β matters" in section 4). Rerun verify\_formulas.py with $s$ drawn from the classifier's measured distribution (section 1) before quoting any number from this section.

**What the synthetic test assumes.** Need is rare in flow (8%), common when stuck (60%), and 25% otherwise. That is the methodology's hypothesis, built in. The test therefore shows the formulas do what they are designed to do. It is not evidence that real users behave this way, which is what your own data must show.

### Exact checks

| Check | Result |
| --- | --- |
| τ rises with flow, falls with stuck, falls with $p_{\text{need}}$ (101-point grid, both cost forms) | Pass |
| α = β = 0 reduces to PRISM's fixed gate | Pass (gate\_test.py) |
| Flow and stuck act independently in log-odds | Pass, both forms |
| α keeps one meaning when $c_0$ changes | Multiplicative only (table in section 4) |

### Matched-recall comparison

Both gates were tuned to the same recall by adjusting the base ratio $c_0$ : $m_0$, so the state-aware gate cannot win just by speaking less. Unwanted interruptions in flow are measured as false alarms per flow moment.

| State signal quality | Target recall | Flow false alarms, fixed | Flow false alarms, state-aware | Precision, fixed | Precision, state-aware |
| --- | --- | --- | --- | --- | --- |
| Clean | 0.80 | 0.86% | 0.48% | 0.64 | 0.56 |
| Clean | 0.90 | 2.35% | 1.27% | 0.50 | 0.42 |
| Noisy | 0.80 | 0.75% | 0.61% | 0.63 | 0.55 |
| Noisy | 0.90 | 2.33% | 2.65% | 0.48 | 0.41 |
| Pure noise | 0.80 | 0.81% | 1.33% | 0.62 | 0.58 |
| Pure noise | 0.90 | 2.54% | 4.94% | 0.48 | 0.44 |

State-aware rows use the multiplicative form with α = 1.1 and β = 0.7. The additive form gave nearly the same numbers (0.49% and 1.21% in the clean rows).

### What this means for the method

1. **The method protects focus.** With a clean signal, flow false alarms drop by 44% at recall 0.80 and by 46% at 0.90.
2. **It moves interruptions rather than removing them.** Overall precision falls by about 8 points. To keep recall the same while staying quiet in flow, the gate speaks more often at other times. Report this next to the headline metric, or a reviewer will find it.
3. **The flow term carries the gain.** Flow-only matched the full gate on flow false alarms (0.48% vs 0.48% at recall 0.80). Stuck-only barely moved them (0.82%). In this test stuck needs were already caught 98% of the time, so the stuck term mainly matters at lower recall settings.
4. **Signal quality decides everything.** With a noisy signal the gain shrinks, and at recall 0.90 it reverses. With a meaningless signal, flow false alarms almost double. This is why the go/no-go check on the state scores has to come before the cost logic.
5. **Calibration is fixable and necessary.** Making $p_{\text{need}}$ overconfident (log-odds × 2.5) dropped the fixed gate's precision from 0.57 to 0.39. Temperature scaling fitted on held-out data recovered T = 2.50, the injected distortion, and restored precision to 0.58. The state-aware gate kept its flow advantage in all three cases (0.67%, 0.60%, 0.70% vs 1.19%, 1.12%, 1.26%).

### Pass criteria for real data

Keep the state-aware gate only if it passes all four on held-out days:

- [ ] Stuck score predicts help-seeking: AUROC clearly above 0.5 against hotkey presses and chatbot visits
- [ ] $s_t$ thresholded at $\theta_{\text{trig}}$ agrees with human flow/stuck labels better than chance (Cohen's kappa well above 0)
- [ ] Fewer flow interruptions than the calibrated fixed gate at matched recall
- [ ] Precision loss reported, and small enough to accept

## 7. Evaluation metrics and baselines

The headline number is interruptions per flow-hour at matched recall of real help-seeking. Every label used here comes from behavior or humans, never from the cost assumption, so the method cannot win by construction.

### Labels

$$
y_t = 1 \iff \text{hotkey pressed, or a chatbot visit, within } \Delta \text{ after } t \quad (\Delta = 5 \text{ min to start})
$$

Flow periods come from end-of-day tags and the 300 human-labeled moments, not from the flow score.

### Decision metrics

TP, FP and FN count speak decisions against $y_t$. A cooldown-merged run of speak decisions counts once.

$$
\text{Precision} = \frac{TP}{TP + FP}, \qquad \text{Recall} = \frac{TP}{TP + FN}, \qquad F_1 = \frac{2 \cdot \text{Precision} \cdot \text{Recall}}{\text{Precision} + \text{Recall}}
$$

$$
\text{Interruptions per flow-hour} = \frac{\#\{\text{speak with } y_t = 0 \text{ inside human-labeled flow}\}}{\text{hours of human-labeled flow}}
$$

$$
\text{Stuck recall} = \frac{\#\{\text{speak within } \Delta \text{ before a hotkey press}\}}{\#\{\text{hotkey presses}\}}
$$

### Signal and calibration metrics

| Metric | Formula | Checks |
| --- | --- | --- |
| AUROC | Probability a random positive moment scores higher than a random negative one | Stuck score vs help-seeking |
| Cohen's kappa | κ = ($p_o$ − $p_e$) / (1 − $p_e$) | Agreement between human labelers, and of the scores with them |
| ECE | $\sum_{b} \frac{n_b}{n}\,\lvert \mathrm{acc}_b - \mathrm{conf}_b \rvert$ over probability bins $b$ | Calibration of $p_{\text{need}}$ and $p_{\text{accept}}$ |
| Brier score | $\frac{1}{n}\sum_{i} (p_i - y_i)^2$ | Calibration and sharpness together |

In kappa, $p_o$ is the observed agreement and $p_e$ the agreement expected by chance. With three or more labelers use Fleiss' kappa.

### Matched-recall comparison

Tune each system's base ratio $c_0$ : $m_0$ so its recall equals a shared target (for example 0.80 and 0.90), then compare interruptions per flow-hour and precision. Without matching, a gate that simply speaks less looks better on interruptions and worse on recall, and the comparison says nothing.

### Baselines and ablations

| System | Settings | Question it answers |
| --- | --- | --- |
| Always speak | τ = 0 | Upper bound on recall |
| Fixed cut | $p_{\text{accept}}$ ≥ 0.5 | Beat this or the cost machinery adds nothing (PRISM's uncalibrated ablation lost to it) |
| PRISM fixed costs | α = β = 0, calibrated | The main comparison |
| Slope sweep | α + β varied, $c_0$ : $m_0$ retuned for matched recall; α + β = 0 is the PRISM row | Does moving the bar with state help, and how steeply? (flow-only and stuck-only collapse into this because flow = 1 − s) |
| Full method | α, β > 0 | The contribution |
| Full method, additive costs | C = $c_0$ + α · flow | Does the cost form matter? |
| Full method, no calibration | T = 1 | Does it need calibration? |
| Full method, no wait | Wait mapped to quiet | Does deferring help? |

### Uncertainty

Report 95% confidence intervals by bootstrap over days (or users), 10,000 resamples, as PRISM did over events. Resample whole days, not single events, because events within a day are correlated. Tune all parameters on separate days from those you evaluate on, and leave one user out when you have several.

## 8. Open issues and caveats

- **PRISM's paper disagrees with itself.** Equation 3.1 and the Appendix A derivation give τ = $C_{\text{FA}}$ / ($C_{\text{FA}}$ + $p_{\text{need}}$ $C_{\text{FN}}$), which is the form used here and re-derived in section 3. Appendix B states the code's default as τ = $C_{\text{FN}}$ q / ($C_{\text{FA}}$ + $C_{\text{FN}}$ q), which rises with need instead of falling. The printed odds form (A.2) also reads $p_{\text{accept}}$ / (1 − $p_{\text{accept}}$) ≥ $p_{\text{need}}$ $C_{\text{FN}}$ / $C_{\text{FA}}$, the inverse of what the derivation gives (≥ $C_{\text{FA}}$ / ($p_{\text{need}}$ $C_{\text{FN}}$)). Check their released code before claiming equivalence, and say in the paper which form you use.
- **Independence assumption.** The silent-cost term $p_{\text{need}}$ · $p_{\text{accept}}$ assumes need and acceptance are independent given context. A user who needs help may also be more willing to accept it, so this can understate the miss cost.
- **Costs are relative, not measured.** $C_{\text{FA}}$ and $C_{\text{FN}}$ encode a preference between the two mistakes, not seconds or dollars. That is why every result should show a sweep over $c_0$ : $m_0$ (PRISM used 1:4 to 1.2:1).
- **The cost form is a modeling choice.** Nothing in PRISM or the data says costs are exponential or linear in state. Section 6 shows the two give near-identical results, so keep the additive form as an ablation.
- **The simulation is circular by design.** It assumes the hypothesis to test the math. Only real hotkey presses, chatbot visits and human labels can show the method helps people.
- **The precision trade-off is real.** At matched recall the state-aware gate lowered overall precision by about 8 points in simulation. The paper should report it beside interruptions per flow-hour.
- **The score is not a probability.** $s_t$ is an EMA of a hand-weighted logistic. It is monotone in stuckness but it is not calibrated, and it is spread out on stuck ticks (a quarter stay below 0.06). The cost formulas use it as a 0 to 1 intensity, and α and β absorb the scale. If you want a calibrated state, apply temperature scaling to $\mathrm{logit}(s_t)$ on held-out labels, as for the probes.
- **Flow is the complement.** There is no independent flow signal, so the gate cannot tell "focused" from "merely not stuck". The earlier draft's separate flow score (typing bins, switches, saves) is dropped, and typing has weight 0 in the classifier.
- **The state evidence is synthetic.** Every number about $s_t$ comes from our own generator, which also made the labels.
- **All start values need tuning.** Window length, caps, signal weights, α, β, δ, $\theta_{\text{flow}}$, cooldown and wait expiry are starting points, tuned on held-out days.

## Sources

- Fu, Tan, Hao, Zhan, Qiu. [PRISM: Festina Lente Proactivity](https://arxiv.org/pdf/2602.01532), ICLR 2026: gate (Eq. 3.1), derivation (Appendix A), ablations (Tables 2, 5), cost-ratio sweep (Table 11), temperatures (Table 13).
- Guo, Pleiss, Sun, Weinberger. On Calibration of Modern Neural Networks, ICML 2017: temperature scaling (cited by PRISM).
- Simulation scripts: verify\_formulas.py (sections 4 and 6) and gate\_test.py (worked examples), shared in chat.
