import { Keyboard, ScanSearch } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import { Card, Empty, Pill, cx } from "../components/ui";
import { clockS, two } from "../lib/format";
import { useJson } from "../lib/hooks";
import { useLive } from "../lib/store";
import type { InspectorEntry } from "../lib/types";

/** Highlight the parts of the context that came from the optional readers. */
function ContextText({ text }: { text: string }) {
  return (
    <pre className="num scroll-thin max-h-[440px] overflow-auto rounded-xl border border-line bg-canvas p-4 text-[12.5px] leading-[1.7] whitespace-pre-wrap text-muted">
      {text.split("\n").map((line, k) => {
        const typed = line.startsWith("Recently typed");
        const head = /^(Activity over|Windows:|Input:|Terminal:|Commits:)/.test(line);
        return (
          <span key={k} className={cx("block", typed && "-mx-2 rounded-md bg-flow-soft px-2 text-ink", head && "text-ink")}>
            {line || " "}
          </span>
        );
      })}
    </pre>
  );
}

export function Inspector() {
  const count = useLive((s) => s.inspectorCount);
  const { data } = useJson<{ entries: InspectorEntry[] }>(`/api/inspector?v=${count}`);
  const entries = data?.entries || [];
  const [sel, setSel] = useState(0);
  useEffect(() => setSel(0), [entries.length]);
  const e = entries[sel];

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-[30px] font-semibold tracking-[-0.025em]">Inspector</h1>
        <p className="mt-1 max-w-[70ch] text-[14px] text-muted">
          Exactly what the model was shown at each check, what it answered and what was offered. The last 50 checks, kept in memory only: nothing here is written to disk.
        </p>
      </header>
      {entries.length === 0 ? (
        <Card className="py-10">
          <div className="flex flex-col items-center gap-3 text-center">
            <ScanSearch size={28} className="text-faint" />
            <Empty>No model checks since SAIP started. They appear here when the stuck score crosses the line.</Empty>
          </div>
        </Card>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[300px_1fr]">
          <Card className="scroll-thin max-h-[70vh] overflow-auto p-2">
            <ul className="space-y-0.5">
              {entries.map((x, k) => (
                <li key={x.time + x.decision}>
                  <button onClick={() => setSel(k)} className={cx("relative w-full rounded-xl px-3 py-2.5 text-left transition-colors", k === sel ? "text-ink" : "text-muted hover:text-ink")}>
                    {k === sel && <motion.span layoutId="insp-on" className="absolute inset-0 rounded-xl bg-raised" transition={{ type: "spring", stiffness: 400, damping: 34 }} />}
                    <span className="relative flex items-center justify-between gap-2">
                      <span className="num text-[12px]">{clockS(x.time)}</span>
                      <Pill tone={x.decision === "offer" ? "stuck" : "muted"}>{x.decision}</Pill>
                    </span>
                    <span className="relative mt-1 flex items-center gap-2 text-[12px] text-faint">
                      <span className="truncate">{x.app || "—"}</span>
                      <span className="num ml-auto">{two(x.p_accept)} / {two(x.bar)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
          <AnimatePresence mode="wait">
            {e && (
              <motion.div key={e.time + e.decision} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }} className="min-w-0 space-y-5">
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  {[
                    ["stuck score", two(e.score)],
                    ["needs help", two(e.p_need)],
                    ["would accept", two(e.p_accept)],
                    ["bar", two(e.bar)],
                  ].map(([k, v]) => (
                    <Card key={k} className="px-4 py-3">
                      <div className="text-[11.5px] text-faint">{k}</div>
                      <div className={cx("num mt-1 text-[22px]", k === "would accept" && e.p_accept >= e.bar ? "text-stuck" : "text-ink")}>{v}</div>
                    </Card>
                  ))}
                </div>
                <Card className="p-5">
                  <div className="mb-3 flex flex-wrap items-center gap-2">
                    <h2 className="text-[13px] font-medium text-ink">Context sent to the model</h2>
                    {e.typed && (
                      <Pill tone="flow">
                        <Keyboard size={12} /> includes typed text
                      </Pill>
                    )}
                    {e.resampled && <Pill>asked again (close call)</Pill>}
                  </div>
                  <ContextText text={e.context} />
                </Card>
                {e.screen && (
                  <Card className="p-5">
                    <h2 className="mb-3 text-[13px] font-medium text-ink">Screen text read for the offer · {e.screen.app}</h2>
                    <pre className="num scroll-thin max-h-60 overflow-auto rounded-xl border border-line bg-canvas p-4 text-[12.5px] leading-relaxed whitespace-pre-wrap text-muted">{e.screen.text}</pre>
                  </Card>
                )}
                {e.offer && (
                  <Card className="border-stuck/40 p-5">
                    <h2 className="text-[13px] font-medium text-stuck">Offer</h2>
                    <p className="mt-2 text-[15px] leading-relaxed text-ink">{e.offer.message}</p>
                    <p className="mt-2 text-[12.5px] text-faint">
                      task: {e.offer.task}
                      {!e.offer.generated && " · the plain fallback (the model's own offer could not be used)"}
                    </p>
                  </Card>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
