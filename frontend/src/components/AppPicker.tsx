import { Check, Search } from "lucide-react";
import { motion } from "motion/react";
import { useMemo, useState } from "react";
import type { AppRow, AppsPayload } from "../lib/types";
import { Button, Input, cx } from "./ui";

/** Pick apps for the block list, from what is installed here and what ActivityWatch has seen. */
export function AppPicker({ data, selected, onChange }: { data: AppsPayload; selected: Set<string>; onChange: (s: Set<string>) => void }) {
  const [q, setQ] = useState("");
  const [only, setOnly] = useState<string | null>(null);
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return data.apps.filter((r) => (!needle || r.label.toLowerCase().includes(needle) || r.value.toLowerCase().includes(needle)) && (!only || r.group === only));
  }, [data.apps, q, only]);
  const groups = data.groups.filter((g) => data.apps.some((r) => r.group === g.id));
  const toggle = (r: AppRow) => {
    const next = new Set(selected);
    if (next.has(r.value)) next.delete(r.value);
    else next.add(r.value);
    onChange(next);
  };
  const addGroup = (g: string) => {
    const next = new Set(selected);
    data.apps.filter((r) => r.group === g).forEach((r) => next.add(r.value));
    onChange(next);
  };
  const suggested = () => onChange(new Set(data.apps.filter((r) => r.suggested || r.blocked).map((r) => r.value)));

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[220px] flex-1">
          <Search size={15} className="absolute top-1/2 left-3 -translate-y-1/2 text-faint" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${data.apps.length} apps`} className="pl-9" />
        </div>
        <Button size="sm" variant="ghost" onClick={suggested}>Suggested</Button>
        <Button size="sm" variant="ghost" onClick={() => addGroup("browser")}>Block browsers too</Button>
        <Button size="sm" variant="ghost" onClick={() => onChange(new Set())}>Clear</Button>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        <button onClick={() => setOnly(null)} className={cx("rounded-full border px-3 py-1 text-[12px] transition-colors", !only ? "border-ink bg-ink text-canvas" : "border-line text-muted hover:text-ink")}>
          All
        </button>
        {groups.map((g) => (
          <button key={g.id} onClick={() => setOnly(only === g.id ? null : g.id)} className={cx("rounded-full border px-3 py-1 text-[12px] transition-colors", only === g.id ? "border-ink bg-ink text-canvas" : "border-line text-muted hover:text-ink")}>
            {g.label}
            <span className="num ml-1.5 opacity-60">{data.apps.filter((r) => r.group === g.id && selected.has(r.value)).length}</span>
          </button>
        ))}
      </div>
      <ul className="scroll-thin mt-3 grid max-h-[340px] grid-cols-1 gap-1 overflow-auto pr-1 sm:grid-cols-2">
        {rows.map((r) => {
          const on = selected.has(r.value);
          return (
            <li key={r.value}>
              <button
                onClick={() => toggle(r)}
                className={cx(
                  "flex w-full items-center gap-3 rounded-xl border px-3 py-2 text-left transition-colors",
                  on ? "border-stuck/50 bg-stuck-soft" : "border-transparent hover:border-line hover:bg-raised",
                )}
              >
                <span className={cx("flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-md border transition-colors", on ? "border-stuck bg-stuck text-[#1a1004]" : "border-line-strong")}>
                  {on && (
                    <motion.span initial={{ scale: 0 }} animate={{ scale: 1 }}>
                      <Check size={12} strokeWidth={3} />
                    </motion.span>
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13.5px] text-ink">{r.label}</span>
                  <span className="block truncate text-[11.5px] text-faint">
                    {r.label !== r.value ? `${r.value} · ` : ""}
                    {r.seen ? "used lately" : r.installed ? "installed" : "on your list"}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
        {rows.length === 0 && <li className="col-span-2 py-6 text-center text-[13px] text-muted">No app matches “{q}”.</li>}
      </ul>
      <p className="mt-3 text-[12.5px] text-muted">
        <span className="num text-ink">{selected.size}</span> blocked. In these apps nothing you type is kept, no screen text is read, and the model sees only “Private app”.
      </p>
    </div>
  );
}

/** The names to save: the picks, plus names already on the list that this machine doesn't show. */
export function blockList(data: AppsPayload, selected: Set<string>): string[] {
  const shown = new Set(data.apps.map((r) => r.value));
  const kept = data.blocked.filter((b) => !shown.has(b));
  return [...new Set([...selected, ...kept])];
}
