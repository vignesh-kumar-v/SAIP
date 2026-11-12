import { scaleLinear } from "d3-scale";
import { area, curveMonotoneX, line } from "d3-shape";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { clockS, duration, two } from "../lib/format";
import type { Decision, Episode, Tick } from "../lib/types";

type Props = { ticks: Tick[]; decisions: Decision[]; episodes: Episode[]; span: number; height?: number };

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(800);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.floor(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

export function ScoreChart({ ticks, decisions, episodes, span, height = 260 }: Props) {
  const [wrap, W] = useWidth<HTMLDivElement>();
  const id = useId().replace(/:/g, "");
  const H = height;
  const L = 8, R = 46, T = 26, B = 24;
  const end = ticks.length ? ticks[ticks.length - 1].time : Date.now() / 1000;
  const start = end - span;
  const seen = useMemo(() => ticks.filter((t) => t.time >= start - 10), [ticks, start]);
  const x = scaleLinear().domain([start, end]).range([L, W - R]);
  const y = scaleLinear().domain([0, 1]).range([H - B, T]);
  const trigger = seen.length ? seen[seen.length - 1].trigger : 0.2;
  const ty = y(trigger);

  const live = seen.filter((t) => !t.paused);
  const segs: Tick[][] = [];
  let cur: Tick[] = [];
  for (const t of seen) {
    if (t.paused) {
      if (cur.length) segs.push(cur);
      cur = [];
    } else cur.push(t);
  }
  if (cur.length) segs.push(cur);

  const mkLine = line<Tick>().x((t) => x(t.time)).y((t) => y(t.score)).curve(curveMonotoneX);
  const mkArea = area<Tick>().x((t) => x(t.time)).y0(H - B).y1((t) => y(t.score)).curve(curveMonotoneX);

  const paused: [number, number][] = [];
  let from: number | null = null;
  seen.forEach((t, i) => {
    if (t.paused && from === null) from = t.time;
    if (from !== null && (!t.paused || i === seen.length - 1)) {
      paused.push([from, t.time]);
      from = null;
    }
  });

  const eps = episodes.filter((e) => (e.state === "open" ? end : e.end) >= start);
  const marks = decisions.filter((d) => d.at >= start && d.decision !== "recheck-quiet");
  const last = live[live.length - 1];

  const [hover, setHover] = useState<Tick | null>(null);
  const onMove = (e: React.MouseEvent<SVGRectElement>) => {
    const r = (e.target as SVGRectElement).getBoundingClientRect();
    const t = x.invert(e.clientX - r.left + L);
    let best: Tick | null = null;
    for (const k of seen) if (!best || Math.abs(k.time - t) < Math.abs(best.time - t)) best = k;
    setHover(best);
  };

  const ticksX = span <= 300 ? [0, 60, 120, 180, 240, 300] : span <= 1800 ? [0, 300, 600, 900, 1200, 1500, 1800] : [0, 600, 1200, 1800, 2400, 3000, 3600];

  return (
    <div ref={wrap} className="relative w-full select-none">
      <svg width={W} height={H} className="block overflow-visible" role="img" aria-label="Stuck score over time">
        <defs>
          <linearGradient id={`g-flow-${id}`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="var(--color-flow)" stopOpacity=".32" />
            <stop offset="1" stopColor="var(--color-flow)" stopOpacity="0" />
          </linearGradient>
          <linearGradient id={`g-stuck-${id}`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="var(--color-stuck)" stopOpacity=".45" />
            <stop offset="1" stopColor="var(--color-stuck)" stopOpacity=".05" />
          </linearGradient>
          <clipPath id={`above-${id}`}><rect x={0} y={0} width={W} height={Math.max(0, ty)} /></clipPath>
          <clipPath id={`below-${id}`}><rect x={0} y={ty} width={W} height={Math.max(0, H - ty)} /></clipPath>
        </defs>

        {[0, 0.25, 0.5, 0.75, 1].map((s) => (
          <g key={s}>
            <line x1={L} x2={W - R} y1={y(s)} y2={y(s)} stroke="var(--grid)" strokeWidth={1} />
            {(s === 0 || s === 1 || s === 0.5) && (
              <text x={W - R + 8} y={y(s) + 4} className="num" fontSize={10.5} fill="var(--faint)">{s.toFixed(1)}</text>
            )}
          </g>
        ))}

        {paused.map(([a, b], i) => (
          <rect key={i} x={x(a)} y={T} width={Math.max(2, x(b) - x(a))} height={H - T - B} fill="var(--grid)" opacity={0.9} />
        ))}

        {eps.map((e) => {
          const a = Math.max(x(e.start), L), b = x(e.state === "open" ? end : e.end);
          const w = Math.max(3, b - a);
          const label = `${e.app ? `Stuck in ${e.app}` : "Stuck"} · ${duration((e.state === "open" ? end : e.end) - e.start)}`;
          return (
            <g key={e.episode}>
              <rect x={a} y={T} width={w} height={H - T - B} fill="var(--color-stuck)" fillOpacity={0.08} rx={3} />
              <line x1={a} x2={a} y1={T - 2} y2={H - B} stroke="var(--color-stuck)" strokeOpacity={0.5} strokeDasharray="2 3" />
              {w > 60 || e.state === "open" ? (
                <text x={Math.min(a + 6, W - R - label.length * 6.4)} y={T - 8} fontSize={11.5} fontWeight={500} fill="var(--color-stuck)">{label}</text>
              ) : null}
            </g>
          );
        })}

        {segs.map((s, i) => (
          <g key={i}>
            <path d={mkArea(s) || ""} fill={`url(#g-flow-${id})`} clipPath={`url(#below-${id})`} />
            <path d={mkArea(s) || ""} fill={`url(#g-stuck-${id})`} clipPath={`url(#above-${id})`} />
            <path d={mkLine(s) || ""} fill="none" stroke="var(--color-flow)" strokeWidth={2} clipPath={`url(#below-${id})`} />
            <path d={mkLine(s) || ""} fill="none" stroke="var(--color-stuck)" strokeWidth={2.2} clipPath={`url(#above-${id})`} />
          </g>
        ))}

        <line x1={L} x2={W - R} y1={ty} y2={ty} stroke="var(--color-stuck)" strokeWidth={1.2} strokeDasharray="5 5" opacity={0.75} />
        <text x={W - R + 8} y={ty + 4} className="num" fontSize={10.5} fill="var(--color-stuck)">{two(trigger)}</text>

        {marks.map((d) => {
          const tick = seen.reduce<Tick | null>((b, k) => (!b || Math.abs(k.time - d.at) < Math.abs(b.time - d.at) ? k : b), null);
          const cx = x(d.at), cy = y(tick?.score ?? d.score);
          if (cx < L) return null;
          return d.decision === "offer" ? (
            <g key={d.id}>
              <circle cx={cx} cy={cy} r={9} fill="var(--color-stuck)" opacity={0.18} />
              <circle cx={cx} cy={cy} r={4.5} fill="var(--color-stuck)" stroke="var(--surface)" strokeWidth={1.5} />
            </g>
          ) : (
            <circle key={d.id} cx={cx} cy={cy} r={3.6} fill="var(--surface)" stroke={d.decision === "deferred" ? "var(--color-stuck)" : "var(--muted)"} strokeWidth={1.5} />
          );
        })}

        {last && (
          <g>
            <motion.circle
              cx={x(last.time)}
              cy={y(last.score)}
              r={10}
              fill={last.score >= last.trigger ? "var(--color-stuck)" : "var(--color-flow)"}
              animate={{ opacity: [0.35, 0], scale: [0.6, 1.8] }}
              transition={{ duration: 1.8, repeat: Infinity, ease: "easeOut" }}
              style={{ transformOrigin: `${x(last.time)}px ${y(last.score)}px` }}
            />
            <circle cx={x(last.time)} cy={y(last.score)} r={4} fill={last.score >= last.trigger ? "var(--color-stuck)" : "var(--color-flow)"} stroke="var(--surface)" strokeWidth={2} />
          </g>
        )}

        {ticksX.map((m) => (
          <text key={m} x={x(end - m)} y={H - 6} fontSize={10.5} fill="var(--faint)" textAnchor={m === 0 ? "end" : "middle"} className="num">
            {m === 0 ? "now" : `−${Math.round(m / 60)}m`}
          </text>
        ))}

        {hover && (
          <g pointerEvents="none">
            <line x1={x(hover.time)} x2={x(hover.time)} y1={T} y2={H - B} stroke="var(--line-strong)" />
            {!hover.paused && <circle cx={x(hover.time)} cy={y(hover.score)} r={4} fill="var(--ink)" />}
          </g>
        )}
        <rect x={L} y={T} width={Math.max(0, W - L - R)} height={H - T - B} fill="transparent" onMouseMove={onMove} onMouseLeave={() => setHover(null)} />
      </svg>

      <AnimatePresence>
        {hover && (
          <motion.div
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.12 }}
            className="pointer-events-none absolute top-0 z-10 rounded-lg border border-line bg-raised px-3 py-2 text-[12px] shadow-xl"
            style={{ left: Math.min(Math.max(x(hover.time) - 80, 0), W - 180), width: 170 }}
          >
            <div className="num text-muted">{clockS(hover.time)}</div>
            {hover.paused ? (
              <div className="mt-0.5 text-ink">Paused</div>
            ) : (
              <div className="mt-0.5 flex items-baseline justify-between">
                <span className="text-ink">{hover.app || "—"}</span>
                <span className="num" style={{ color: hover.score >= hover.trigger ? "var(--color-stuck)" : "var(--color-flow)" }}>{two(hover.score)}</span>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
