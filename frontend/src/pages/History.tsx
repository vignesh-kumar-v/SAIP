import { Download } from "lucide-react";
import { motion } from "motion/react";
import { useMemo, useState } from "react";
import { Card, CardHead, Empty, Pill, cx } from "../components/ui";
import { dateTime, duration, two } from "../lib/format";
import { useJson } from "../lib/hooks";
import { useLive } from "../lib/store";
import type { DecisionRow, OfferRow } from "../lib/types";

const KINDS = ["all", "offer", "deferred", "quiet"] as const;
const OUTCOME_TONE: Record<string, "flow" | "stuck" | "muted"> = { accepted: "flow", dismissed: "stuck", ignored: "muted", unanswered: "muted" };

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "flow" | "stuck" }) {
  return (
    <Card className="px-5 py-4">
      <div className="text-[12px] text-faint">{label}</div>
      <div className={cx("num mt-1.5 text-[30px] leading-none font-medium", tone === "flow" ? "text-flow" : tone === "stuck" ? "text-stuck" : "text-ink")}>{value}</div>
      {sub && <div className="mt-1.5 text-[12px] text-muted">{sub}</div>}
    </Card>
  );
}

function ExportButtons({ what }: { what: "decisions" | "offers" }) {
  return (
    <div className="flex gap-1.5">
      {(["csv", "jsonl"] as const).map((f) => (
        <a key={f} href={`/api/export?what=${what}&format=${f}`} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line px-2.5 text-[12px] text-muted transition-colors hover:border-line-strong hover:text-ink">
          <Download size={13} /> {f.toUpperCase()}
        </a>
      ))}
    </div>
  );
}

export function History() {
  const outcomes = useLive((s) => s.log.filter((e) => e.kind === "outcome").length);
  const decisionsLive = useLive((s) => s.decisions.length);
  const offers = useJson<{ summary: string; offers: OfferRow[] }>(`/api/offers?v=${outcomes}`);
  const decisions = useJson<{ rows: DecisionRow[]; total: number }>(`/api/decisions?v=${decisionsLive}`);
  const [kind, setKind] = useState<(typeof KINDS)[number]>("all");

  const o = offers.data?.offers || [];
  const d = decisions.data?.rows || [];
  const stats = useMemo(() => {
    const answered = o.filter((r) => r.outcome === "accepted" || r.outcome === "dismissed");
    const accepted = o.filter((r) => r.outcome === "accepted").length;
    const latency = o.filter((r) => r.answered_after_s != null).map((r) => r.answered_after_s as number);
    const byApp = new Map<string, number>();
    d.forEach((r) => r.app && byApp.set(r.app, (byApp.get(r.app) || 0) + 1));
    return {
      offers: o.length,
      rate: answered.length ? accepted / answered.length : null,
      checks: decisions.data?.total || 0,
      offerShare: d.length ? d.filter((r) => r.decision === "offer").length / d.length : null,
      latency: latency.length ? latency.reduce((a, b) => a + b, 0) / latency.length : null,
      topApp: [...byApp.entries()].sort((a, b) => b[1] - a[1])[0],
    };
  }, [o, d, decisions.data]);

  const rows = d.filter((r) => kind === "all" || r.decision === kind).slice().reverse().slice(0, 300);

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-[30px] font-semibold tracking-[-0.025em]">History</h1>
        <p className="mt-1 text-[14px] text-muted">Every time the model was asked, and what happened to each offer. Stored on this machine; export for analysis.</p>
      </header>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Model checks" value={String(stats.checks)} sub={stats.offerShare != null ? `${Math.round(stats.offerShare * 100)}% became offers` : undefined} />
        <Stat label="Offers" value={String(stats.offers)} sub={offers.data?.summary} />
        <Stat label="Accepted when answered" value={stats.rate == null ? "–" : `${Math.round(stats.rate * 100)}%`} tone="flow" />
        <Stat label="Most checks in" value={stats.topApp?.[0] || "–"} sub={stats.latency != null ? `answers take ${duration(stats.latency)} on average` : undefined} tone="stuck" />
      </div>

      <Card>
        <CardHead
          title="Offers"
          hint="What was offered and how you answered."
          right={<ExportButtons what="offers" />}
        />
        {o.length === 0 ? (
          <Empty>No offers yet.</Empty>
        ) : (
          <div className="scroll-thin overflow-x-auto px-5 pb-4">
            <table className="w-full min-w-[720px] text-[13px]">
              <thead>
                <tr className="text-left text-[11.5px] text-faint">
                  <th className="py-2 pr-4 font-normal">When</th>
                  <th className="py-2 pr-4 font-normal">Offer</th>
                  <th className="py-2 pr-4 font-normal">App</th>
                  <th className="py-2 pr-4 text-right font-normal">Score</th>
                  <th className="py-2 pr-4 text-right font-normal">Accept / bar</th>
                  <th className="py-2 font-normal">Answer</th>
                </tr>
              </thead>
              <tbody>
                {o.slice().reverse().slice(0, 50).map((r, k) => (
                  <motion.tr key={r.time} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: Math.min(k * 0.015, 0.3) }} className="border-t border-line align-top">
                    <td className="num py-2.5 pr-4 whitespace-nowrap text-muted">{dateTime(r.time)}</td>
                    <td className="py-2.5 pr-4 text-ink">{r.message}</td>
                    <td className="py-2.5 pr-4 whitespace-nowrap text-muted">{r.app || "—"}</td>
                    <td className="num py-2.5 pr-4 text-right">{two(r.stuck_score)}</td>
                    <td className="num py-2.5 pr-4 text-right whitespace-nowrap">
                      {two(r.p_accept)} <span className="text-faint">/ {two(r.bar)}</span>
                    </td>
                    <td className="py-2.5 whitespace-nowrap">
                      <Pill tone={OUTCOME_TONE[r.outcome] || "muted"}>{r.outcome}</Pill>
                      {r.answered_after_s != null && <span className="num ml-2 text-[11.5px] text-faint">{Math.round(r.answered_after_s)}s</span>}
                    </td>
                  </motion.tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <CardHead
          title="Every model check"
          hint="The gate's numbers each time the score crossed the line (decisions.jsonl)."
          right={
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex rounded-xl border border-line bg-canvas p-0.5">
                {KINDS.map((k) => (
                  <button key={k} onClick={() => setKind(k)} className={cx("rounded-[10px] px-2.5 py-1 text-[12px] capitalize transition-colors", kind === k ? "bg-raised text-ink" : "text-muted hover:text-ink")}>
                    {k}
                  </button>
                ))}
              </div>
              <ExportButtons what="decisions" />
            </div>
          }
        />
        {rows.length === 0 ? (
          <Empty>No model checks recorded yet.</Empty>
        ) : (
          <div className="scroll-thin max-h-[560px] overflow-auto px-5 pb-4">
            <table className="w-full min-w-[640px] text-[13px]">
              <thead className="sticky top-0 bg-surface">
                <tr className="text-left text-[11.5px] text-faint">
                  <th className="py-2 pr-4 font-normal">When</th>
                  <th className="py-2 pr-4 font-normal">Decision</th>
                  <th className="py-2 pr-4 font-normal">App</th>
                  <th className="py-2 pr-4 text-right font-normal">Score</th>
                  <th className="py-2 pr-4 text-right font-normal">Needs help</th>
                  <th className="py-2 pr-4 text-right font-normal">Would accept</th>
                  <th className="py-2 text-right font-normal">Bar</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.time + r.decision} className="border-t border-line">
                    <td className="num py-2 pr-4 whitespace-nowrap text-muted">{dateTime(r.time)}</td>
                    <td className="py-2 pr-4">
                      <Pill tone={r.decision === "offer" ? "stuck" : "muted"}>{r.decision}</Pill>
                      {r.resampled && <span className="ml-1.5 text-[11px] text-faint">re-asked</span>}
                    </td>
                    <td className="py-2 pr-4 text-muted">{r.app || "—"}</td>
                    <td className="num py-2 pr-4 text-right">{two(r.score)}</td>
                    <td className="num py-2 pr-4 text-right">{two(r.p_need)}</td>
                    <td className={cx("num py-2 pr-4 text-right", r.p_accept >= r.bar ? "text-stuck" : "")}>{two(r.p_accept)}</td>
                    <td className="num py-2 text-right text-muted">{two(r.bar)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
