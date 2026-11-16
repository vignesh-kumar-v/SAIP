import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";
import { ScoreChart } from "../components/ScoreChart";
import { GateMeter, WhyBars } from "../components/Signals";
import { Card, CardHead, Count, Empty, STATE_COLOR, cx } from "../components/ui";
import { post } from "../lib/api";
import { clock, clockS, duration, two } from "../lib/format";
import { useJson } from "../lib/hooks";
import { PAUSE_WORDS, STATE_WORDS, stateOf, stuckByApp, useLive } from "../lib/store";
import { GATE_PRESETS, type Episode, type LogEvent, type Settings } from "../lib/types";

const RANGES = [
  { label: "5 min", s: 300 },
  { label: "30 min", s: 1800 },
  { label: "1 hour", s: 3600 },
];

function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

function Hero() {
  const link = useLive((s) => s.link);
  const tick = useLive((s) => s.ticks[s.ticks.length - 1]);
  const quietUntil = useLive((s) => s.quietUntil);
  const episodes = useLive((s) => s.episodes);
  const ticks = useLive((s) => s.ticks);
  const now = useNow(5000);
  const state = stateOf(tick, link);
  const latest = episodes[episodes.length - 1];
  const open = latest?.state === "open" ? latest : undefined;
  const where = state === "stuck" ? open?.app || tick?.app : null;

  let detail = "Nothing has been scored yet. SAIP needs ActivityWatch's window, AFK and input watchers.";
  if (state === "offline") detail = "The dashboard can't reach the agent. Start it again with `saip run`.";
  else if (tick?.paused) detail = `Not scoring because ${PAUSE_WORDS[tick.reason || ""] || tick.reason || "nothing is being reported"}.`;
  else if (state === "stuck" && open) {
    const peak = Math.max(open.peak, ...ticks.filter((t) => t.time >= open.start && !t.paused).map((t) => t.score));
    detail = `For ${duration(tick!.time - open.start)} so far, peaking at ${two(peak)}. The model is being asked whether to step in.`;
  } else if (state === "stuck") detail = "The model is being asked whether to step in.";
  else if (tick) detail = `Working in ${tick.app || "an app"}. The model is asked only above ${two(tick.trigger)}.`;
  if (quietUntil > now && state !== "offline") detail += ` Offers are paused until ${clock(quietUntil)}.`;

  return (
    <section className="grid gap-6 pb-6 md:grid-cols-[1fr_auto] md:items-end">
      <div className="min-w-0">
        <div className="mb-3 flex items-center gap-2 text-[12px] font-medium uppercase tracking-[0.12em] text-faint">
          <span className="h-px w-6 bg-line-strong" /> Right now
        </div>
        <AnimatePresence mode="wait" initial={false}>
          <motion.h1
            key={state + (where || "")}
            initial={{ opacity: 0, y: 14, filter: "blur(6px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: -10, filter: "blur(6px)" }}
            transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
            className="text-[clamp(38px,6vw,64px)] leading-[1.02] font-semibold tracking-[-0.035em]"
            style={{ color: STATE_COLOR[state] }}
          >
            {STATE_WORDS[state]}
            {where && <span className="block text-[0.62em] font-medium tracking-[-0.02em] text-ink/80">in {where}</span>}
          </motion.h1>
        </AnimatePresence>
        <p className="mt-4 max-w-[58ch] text-[15px] leading-relaxed text-muted">{detail}</p>
      </div>
      {tick && !tick.paused && state !== "offline" && (
        <div className="flex items-end gap-6 md:text-right">
          <div>
            <div className="text-[11.5px] font-medium uppercase tracking-[0.12em] text-faint">stuck score</div>
            <Count value={tick.score} className="block text-[52px] leading-none font-medium" />
          </div>
          <div className="pb-1.5">
            <div className="text-[11.5px] text-faint">model asked above</div>
            <div className="num text-[18px] text-stuck">{two(tick.trigger)}</div>
          </div>
        </div>
      )}
    </section>
  );
}

function Stretches() {
  const episodes = useLive((s) => s.episodes);
  const ticks = useLive((s) => s.ticks);
  const now = useNow(1000);
  const list = [...episodes].reverse().slice(0, 6);
  const end = ticks.length ? ticks[ticks.length - 1].time : now;
  const byApp = useMemo(() => stuckByApp(ticks, end - 3600), [ticks, end]);
  const most = byApp[0]?.seconds || 1;
  return (
    <Card>
      <CardHead title="Where you got stuck" hint="Each stretch above the line, named after the app you were in." />
      {list.length === 0 ? (
        <Empty>No stuck stretches yet. When the score crosses the dashed line, the stretch shows up here with the app it happened in.</Empty>
      ) : (
        <ul className="divide-y divide-line px-5">
          <AnimatePresence initial={false}>
            {list.map((e: Episode) => {
              const open = e.state === "open";
              const stop = open ? end : e.end;
              return (
                <motion.li key={e.episode} layout initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} className="flex items-center gap-3 py-3">
                  <span className="relative flex h-2.5 w-2.5 shrink-0">
                    {open && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-stuck opacity-60" />}
                    <span className={cx("relative inline-flex h-2.5 w-2.5 rounded-full", open ? "bg-stuck" : "bg-stuck/40")} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[14px] text-ink">
                      Stuck in <span className="font-medium">{e.app || "an app"}</span>
                      {Object.keys(e.apps || {}).length > 1 && (
                        <span className="text-muted"> · also {Object.keys(e.apps).filter((a) => a !== e.app).slice(0, 2).join(", ")}</span>
                      )}
                    </div>
                    <div className="num mt-0.5 text-[12px] text-faint">
                      {clockS(e.start)} {open ? "→ now" : `→ ${clockS(e.end)}`}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="num text-[13px] text-ink">{duration(stop - e.start)}</div>
                    <div className="num text-[11.5px] text-stuck">peak {two(e.peak)}</div>
                  </div>
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ul>
      )}
      {byApp.length > 0 && (
        <div className="border-t border-line px-5 pt-4 pb-5">
          <div className="mb-3 text-[12px] font-medium text-muted">Time stuck by app, last hour</div>
          <ul className="space-y-2">
            {byApp.slice(0, 5).map((r) => (
              <li key={r.app} className="grid grid-cols-[120px_1fr_60px] items-center gap-3 text-[12.5px]">
                <span className="truncate text-ink">{r.app}</span>
                <div className="h-1.5 rounded-full bg-raised">
                  <motion.div className="h-full rounded-full bg-stuck" initial={{ width: 0 }} animate={{ width: `${(r.seconds / most) * 100}%` }} transition={{ type: "spring", stiffness: 120, damping: 20 }} />
                </div>
                <span className="num text-right text-muted">{duration(r.seconds)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

const LINE: Record<string, (d: any) => string | null> = {
  decision: (d) =>
    ({ offer: "Decided to offer help", deferred: "Held back until your next pause", quiet: "Checked and stayed quiet", "recheck-quiet": "Looked again and stayed quiet" } as Record<string, string>)[d.decision] +
    (d.app ? ` · ${d.app}` : ""),
  offer: (d) => `Offered: ${d.message}`,
  outcome: (d) =>
    (({ accepted: "You accepted", dismissed: "You said not now", ignored: "The offer went unanswered", unanswered: "Offer printed in the terminal" } as Record<string, string>)[d.outcome] || d.outcome) +
    (d.cooldown_mult > 1 ? ` · quiet period now ×${d.cooldown_mult}` : ""),
  help: (d) => `Help: ${d.text}`,
  status: (d) => d.text,
  episode: (d) => (d.state === "open" ? `Started looking stuck${d.app ? ` in ${d.app}` : ""}` : `Back in flow after ${duration(d.end - d.start)}${d.app ? ` in ${d.app}` : ""}`),
  quiet: (d) => (d.until ? `Offers paused until ${clock(d.until)}` : "Offers let through again"),
};

function Feed() {
  const log = useLive((s) => s.log);
  const rows = log.filter((e) => LINE[e.kind]).slice(-14).reverse();
  return (
    <Card>
      <CardHead title="What happened" hint="Checks, offers, your answers and stuck stretches, newest first." />
      {rows.length === 0 ? (
        <Empty>Nothing yet.</Empty>
      ) : (
        <ul className="scroll-thin max-h-[340px] overflow-auto px-5 pb-4">
          <AnimatePresence initial={false}>
            {rows.map((e: LogEvent) => (
              <motion.li key={e.id} layout initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} className="grid grid-cols-[64px_1fr] gap-3 border-b border-line/60 py-2 text-[13px] last:border-0">
                <time className="num pt-px text-[11.5px] text-faint">{clock(e.at)}</time>
                <span className={cx("leading-snug", e.kind === "status" && (e as any).level === "error" ? "text-stuck" : e.kind === "offer" || e.kind === "help" ? "text-ink" : "text-muted")}>
                  {LINE[e.kind](e)}
                </span>
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      )}
    </Card>
  );
}

export function Live() {
  const ticks = useLive((s) => s.ticks);
  const decisions = useLive((s) => s.decisions);
  const episodes = useLive((s) => s.episodes);
  const { data: settings, reload: reloadSettings } = useJson<Settings>("/api/settings");
  const [range, setRange] = useState(1800);
  const last = ticks[ticks.length - 1];
  const lastDecision = decisions[decisions.length - 1];

  const currentM0 = settings?.gate?.m0;
  const activePreset = GATE_PRESETS.find((p) => currentM0 !== undefined && Math.abs(p.gate.m0 - currentM0) < 0.05);

  const applyPreset = async (gate: Record<string, number>) => {
    try {
      await post("/api/settings", { gate });
      reloadSettings();
    } catch {}
  };

  return (
    <div className="space-y-5">
      <Hero />
      <Card>
        <CardHead
          title="Stuck score"
          hint="Above the dashed line the model is asked whether to offer help. Shaded stretches show where you got stuck."
          right={
            <div className="flex shrink-0 rounded-xl border border-line bg-canvas p-0.5">
              {RANGES.map((r) => (
                <button key={r.s} onClick={() => setRange(r.s)} className={cx("relative rounded-[10px] px-3 py-1 text-[12.5px] transition-colors", range === r.s ? "text-ink" : "text-muted hover:text-ink")}>
                  {range === r.s && <motion.span layoutId="range-on" className="absolute inset-0 rounded-[10px] bg-raised" transition={{ type: "spring", stiffness: 400, damping: 34 }} />}
                  <span className="relative">{r.label}</span>
                </button>
              ))}
            </div>
          }
        />
        <div className="px-3 pb-3">
          <ScoreChart ticks={ticks} decisions={decisions} episodes={episodes} span={range} />
        </div>
        <div className="flex flex-wrap gap-x-5 gap-y-1.5 border-t border-line px-5 py-3 text-[12px] text-muted">
          <span className="flex items-center gap-2"><i className="h-0.5 w-4 rounded bg-flow" /> in flow</span>
          <span className="flex items-center gap-2"><i className="h-0.5 w-4 rounded bg-stuck" /> above the line</span>
          <span className="flex items-center gap-2"><i className="h-2.5 w-2.5 rounded-full bg-stuck" /> offered help</span>
          <span className="flex items-center gap-2"><i className="h-2.5 w-2.5 rounded-full border-[1.5px] border-stuck" /> held back</span>
          <span className="flex items-center gap-2"><i className="h-2.5 w-4 rounded-sm bg-[var(--grid)]" /> paused</span>
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHead title="Why the score is where it is" hint="What pushed the score at the latest tick (weight × signal). Updates every few seconds." />
          <WhyBars features={last?.features} />
        </Card>
        <Stretches />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHead
            title="Last model check"
            hint="Speak only if the chance you'd accept clears a bar set by how stuck you look."
            right={
              <div className="flex items-center rounded-xl border border-line bg-canvas p-0.5">
                {GATE_PRESETS.map((p) => {
                  const isCurrent = activePreset?.id === p.id;
                  return (
                    <button
                      key={p.id}
                      onClick={() => applyPreset(p.gate)}
                      title={p.caption}
                      className={cx(
                        "rounded-[10px] px-2.5 py-1 text-[12px] font-medium transition-colors",
                        isCurrent ? "bg-raised text-ink shadow-xs" : "text-muted hover:text-ink"
                      )}
                    >
                      {p.name}
                    </button>
                  );
                })}
                {!activePreset && currentM0 !== undefined && (
                  <span className="px-2 py-1 text-[11.5px] text-faint">Custom</span>
                )}
              </div>
            }
          />
          <GateMeter d={lastDecision} />
          <div className="border-t border-line px-5 py-2.5 text-[12px] text-faint">
            <span className="font-medium text-ink">{activePreset ? activePreset.name : "Custom"} sensitivity:</span>{" "}
            {activePreset ? activePreset.caption : `Custom threshold (m₀ = ${two(currentM0 ?? 0)}).`}
          </div>
        </Card>
        <Feed />
      </div>
    </div>
  );
}
