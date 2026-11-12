import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import { post } from "../lib/api";
import { FEATURE_WORDS, two } from "../lib/format";
import type { Ask, Decision, Features } from "../lib/types";
import { Button, Mark, Tip, cx } from "./ui";

/** What pushed the score at the latest tick: each feature's weight x value, drawn from a centre line. */
export function WhyBars({ features }: { features: Features | null | undefined }) {
  if (!features) return <p className="px-5 pb-5 text-[13px] text-muted">Waiting for the first scored tick.</p>;
  const items = Object.entries(features.items).filter(([, v]) => v.weight !== 0);
  const max = Math.max(2, ...items.map(([, v]) => Math.abs(v.push)));
  items.sort((a, b) => Math.abs(b[1].push) - Math.abs(a[1].push));
  return (
    <ul className="space-y-2.5 px-5 pb-5">
      {items.map(([name, v]) => {
        const w = (Math.abs(v.push) / max) * 50;
        const up = v.push >= 0;
        const words = FEATURE_WORDS[name] || { name, hint: "" };
        return (
          <motion.li layout key={name} transition={{ type: "spring", stiffness: 300, damping: 30 }} className="grid grid-cols-[92px_1fr_44px] items-center gap-3">
            <Tip content={<span><b className="font-medium">{words.name}</b>: {words.hint}. Value {two(v.value)} × weight {v.weight}.</span>}>
              <span className="cursor-help truncate text-[12.5px] text-muted">{words.name}</span>
            </Tip>
            <div className="relative h-2 rounded-full bg-raised">
              <div className="absolute inset-y-[-3px] left-1/2 w-px bg-line-strong" />
              <motion.div
                className="absolute inset-y-0 rounded-full"
                initial={false}
                animate={{ width: `${w}%`, left: up ? "50%" : `${50 - w}%` }}
                transition={{ type: "spring", stiffness: 140, damping: 22 }}
                style={{ background: up ? "var(--color-stuck)" : "var(--color-flow)" }}
              />
            </div>
            <span className={cx("num text-right text-[12px]", up ? "text-stuck" : "text-flow")}>
              {v.push > 0 ? "+" : ""}
              {v.push.toFixed(2)}
            </span>
          </motion.li>
        );
      })}
      <li className="flex justify-between pt-1 text-[11.5px] text-faint">
        <span>← toward flow</span>
        <span>toward stuck →</span>
      </li>
    </ul>
  );
}

const VERDICT: Record<string, (d: Decision) => string> = {
  offer: (d) => `Offered help: the chance you'd accept (${two(d.p_accept)}) cleared the bar (${two(d.bar)}).`,
  deferred: (d) => `Held back until your next pause: close (${two(d.p_accept)} vs ${two(d.bar)}) and you were in flow.`,
  quiet: (d) => `Stayed quiet: ${two(d.p_accept)} was under the bar (${two(d.bar)}).`,
  "recheck-quiet": (d) => `Looked again at a pause and stayed quiet (${two(d.p_accept)} vs ${two(d.bar)}).`,
};

/** The last gate check: did the chance of a yes clear the bar? */
export function GateMeter({ d }: { d: Decision | undefined }) {
  if (!d) {
    return (
      <p className="px-5 pb-5 text-[13px] leading-relaxed text-muted">
        The model hasn't been asked yet. It's asked only when the stuck score crosses the dashed line.
      </p>
    );
  }
  const pct = (v: number) => `${Math.min(98, Math.max(2, v * 100))}%`;
  const cleared = d.p_accept >= d.bar;
  return (
    <div className="px-5 pb-5">
      <p className="text-[14.5px] leading-snug text-ink">{(VERDICT[d.decision] || VERDICT.quiet)(d)}</p>
      <div className="relative mt-7 h-10">
        <div className="absolute inset-x-0 top-4 h-1.5 rounded-full bg-raised" />
        <motion.div
          className="absolute top-4 h-1.5 rounded-l-full"
          initial={false}
          animate={{ width: pct(d.bar) }}
          style={{ background: "color-mix(in oklab, var(--color-pause) 35%, transparent)" }}
        />
        <motion.div className="absolute -top-3 -translate-x-1/2 text-center" initial={false} animate={{ left: pct(d.bar) }}>
          <span className="num block text-[11px] text-muted">bar {two(d.bar)}</span>
          <span className="mx-auto mt-0.5 block h-5 w-[2px] rounded bg-ink" />
        </motion.div>
        <motion.div className="absolute top-[7px] -translate-x-1/2" initial={false} animate={{ left: pct(d.p_accept) }} transition={{ type: "spring", stiffness: 160, damping: 20 }}>
          <span className={cx("block h-4 w-4 rounded-full border-2", cleared ? "border-stuck bg-stuck" : "border-muted bg-surface")} />
          <span className="num mt-1 block -translate-x-1/4 text-[11px] text-ink">{two(d.p_accept)}</span>
        </motion.div>
      </div>
      <dl className="mt-4 grid grid-cols-3 gap-3 text-[12px]">
        <div><dt className="text-faint">stuck score</dt><dd className="num text-ink">{two(d.score)}</dd></div>
        <div><dt className="text-faint">needs help</dt><dd className="num text-ink">{two(d.p_need)}</dd></div>
        <div><dt className="text-faint">in</dt><dd className="truncate text-ink">{d.app || "—"}</dd></div>
      </dl>
      {d.resampled && <p className="mt-3 text-[12px] text-faint">Close call, so the model was asked again.</p>}
    </div>
  );
}

/** Open questions from the agent (offers, help text, new repositories), answerable from any page. */
export function AskStack({ asks }: { asks: Ask[] }) {
  return (
    <div className="pointer-events-none fixed right-4 bottom-4 z-40 flex w-[min(400px,calc(100vw-32px))] flex-col gap-3" aria-live="assertive">
      <AnimatePresence initial={false}>
        {asks.map((a) => (
          <AskCard key={a.id} a={a} />
        ))}
      </AnimatePresence>
    </div>
  );
}

function AskCard({ a }: { a: Ask }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [left, setLeft] = useState(() => Math.max(0, a.timeout_s - (Date.now() / 1000 - a.at)));
  useEffect(() => {
    const t = setInterval(() => setLeft(Math.max(0, a.timeout_s - (Date.now() / 1000 - a.at))), 250);
    return () => clearInterval(t);
  }, [a]);
  const answer = async (b: string) => {
    setBusy(b);
    try {
      await post(`/api/asks/${a.id}`, { button: b });
    } catch {
      setBusy(null);
    }
  };
  const isOffer = a.buttons.includes("Help me");
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 24, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, x: 40, transition: { duration: 0.2 } }}
      transition={{ type: "spring", stiffness: 320, damping: 28 }}
      className="pointer-events-auto overflow-hidden rounded-2xl border border-line-strong bg-raised shadow-2xl shadow-black/30"
    >
      <div className="flex gap-3 p-4">
        <div className="mt-0.5 shrink-0">
          <Mark state={isOffer ? "stuck" : "flow"} size={22} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-[11.5px] font-medium uppercase tracking-[0.08em] text-faint">{a.title || "SAIP"}</div>
          <p className="mt-1 text-[14px] leading-relaxed whitespace-pre-wrap text-ink">{a.message}</p>
          <div className="mt-3.5 flex flex-wrap gap-2">
            {a.buttons.map((b, i) => (
              <Button key={b} size="sm" variant={i === a.buttons.length - 1 ? (isOffer ? "stuck" : "primary") : "ghost"} busy={busy === b} onClick={() => answer(b)}>
                {b}
              </Button>
            ))}
          </div>
        </div>
      </div>
      <motion.div className="h-[3px] origin-left bg-stuck" style={{ scaleX: left / a.timeout_s }} transition={{ duration: 0.25, ease: "linear" }} />
    </motion.div>
  );
}
