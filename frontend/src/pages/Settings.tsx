import { CheckCircle2, Power, RefreshCw } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { AppPicker, blockList } from "../components/AppPicker";
import { ActivityWatchPanel, CheckRows, FeatureToggles, HookPanel, ModelForm, Note, PermissionsPanel, ReposPanel, type FeatureValues } from "../components/SetupPanels";
import { Button, Card, Field, Input, Toggle, cx } from "../components/ui";
import { post } from "../lib/api";
import { useJson } from "../lib/hooks";
import { useLive } from "../lib/store";
import type { AppsPayload, Check, Settings as SettingsT } from "../lib/types";

const SECTIONS = [
  ["model", "Model"],
  ["features", "What it reads"],
  ["chat", "Chat tools"],
  ["privacy", "Blocked apps"],
  ["chats", "Saved chats"],
  ["notifications", "Notifications"],
  ["activitywatch", "ActivityWatch"],
  ["repos", "Git & terminal"],
  ["behaviour", "Timing & gate"],
  ["startup", "Startup"],
  ["health", "Health"],
] as const;

function Section({ id, title, lead, children, right }: { id: string; title: string; lead?: ReactNode; children: ReactNode; right?: ReactNode }) {
  return (
    <Card id={id} className="scroll-mt-24 p-6">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-[17px] font-semibold tracking-tight text-ink">{title}</h2>
          {lead && <p className="mt-1 max-w-[64ch] text-[13.5px] leading-relaxed text-muted">{lead}</p>}
        </div>
        {right}
      </div>
      {children}
    </Card>
  );
}

function SavedFlash({ show }: { show: boolean }) {
  return (
    <AnimatePresence>
      {show && (
        <motion.span initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} className="flex items-center gap-1.5 text-[13px] text-flow">
          <CheckCircle2 size={15} /> Saved, agent reloaded
        </motion.span>
      )}
    </AnimatePresence>
  );
}

function useFlash(): [boolean, () => void] {
  const [on, setOn] = useState(false);
  return [on, () => {
    setOn(true);
    setTimeout(() => setOn(false), 2400);
  }];
}

function Features({ s, reload }: { s: SettingsT; reload: () => void }) {
  const initial = { typed: s.agent.monitor_typed_content, screen: s.llm.read_screen, titles: s.llm.include_titles };
  const [v, setV] = useState<FeatureValues>(initial);
  const [busy, setBusy] = useState(false);
  const [flash, show] = useFlash();
  useEffect(() => setV({ typed: s.agent.monitor_typed_content, screen: s.llm.read_screen, titles: s.llm.include_titles }), [s]);
  const save = async (next: FeatureValues) => {
    setV(next);
    setBusy(true);
    await post("/api/settings", { agent: { monitor_typed_content: next.typed }, llm: { read_screen: next.screen, include_titles: next.titles } }).catch(() => {});
    setBusy(false);
    show();
    reload();
  };
  return (
    <Section id="features" title="What the model can read" lead="Each switch takes effect at once: the agent reloads with the new setting." right={busy ? <RefreshCw size={15} className="animate-spin text-muted" /> : <SavedFlash show={flash} />}>
      <FeatureToggles value={v} onChange={save} />
      <div className="mt-5">
        <PermissionsPanel typed={v.typed} screen={v.screen} />
      </div>
    </Section>
  );
}

const PROVIDERS = [
  ["duckduckgo", "DuckDuckGo (no key, unofficial)"],
  ["brave", "Brave Search (API key)"],
  ["tavily", "Tavily (API key)"],
] as const;

function ChatTools({ s, reload }: { s: SettingsT; reload: () => void }) {
  const tools = !!s.llm.chat_tools;
  const web = !!s.llm.web_search;
  const [provider, setProvider] = useState(s.llm.search_provider || "duckduckgo");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [flash, show] = useFlash();
  useEffect(() => setProvider(s.llm.search_provider || "duckduckgo"), [s.llm.search_provider]);
  const keyed = provider === "brave" || provider === "tavily";
  const dirty = provider !== (s.llm.search_provider || "duckduckgo") || key !== "";
  const save = async (llm: Record<string, unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await post("/api/settings", { llm });
      show();
      reload();
      return true;
    } catch (e: any) {
      setErr(e.message);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const saveSearch = async () => {
    if (await save({ search_provider: provider, search_api_key: keyed && key ? key : null })) setKey("");
  };
  const row = (title: string, hint: string, on: boolean, disabled: boolean, change: (v: boolean) => void) => (
    <div className="flex items-center gap-4 rounded-xl border border-line px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="text-[14px] text-ink">{title}</div>
        <div className="text-[12px] text-faint">{hint}</div>
      </div>
      <Toggle checked={on} disabled={disabled || busy} onChange={change} label={title} />
    </div>
  );
  return (
    <Section id="chat" title="Chat tools" lead="Let the chat look things up and propose changes. Nothing is changed without your approval." right={busy ? <RefreshCw size={15} className="animate-spin text-muted" /> : <SavedFlash show={flash} />}>
      <div className="space-y-3">
        {row(
          "Let the chat use tools",
          "Reading your screen, activity, git and files in your watched folders. Copying to the clipboard and writing text files are only proposed: you approve each one.",
          tools,
          false,
          (v) => save({ chat_tools: v }),
        )}
        {row("Web search", "Your search words go to the search service. Off unless you switch it on.", web, !tools, (v) => save({ web_search: v }))}
        {tools && web && (
          <div className="space-y-3 rounded-xl border border-line px-4 py-3">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Search service">
                <select
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                  className="h-10 w-full rounded-xl border border-line bg-canvas px-3 text-[14px] text-ink transition-colors focus:border-flow focus:outline-none"
                >
                  {PROVIDERS.map(([v, label]) => (
                    <option key={v} value={v}>
                      {label}
                    </option>
                  ))}
                </select>
              </Field>
              {keyed && (
                <Field label="API key" hint={s.llm.search_api_key_set ? "A key is saved. Leave empty to keep it." : "Needed for this service."}>
                  <Input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder={s.llm.search_api_key_set ? "•••••••• saved" : "API key"} autoComplete="new-password" />
                </Field>
              )}
            </div>
            <Button variant="primary" size="sm" disabled={!dirty} busy={busy} onClick={saveSearch}>
              Save
            </Button>
          </div>
        )}
        {err && <p className="text-[13px] text-danger">{err}</p>}
        <Note>
          What leaves this machine: file and diff text you let the chat read goes to your model endpoint, and search words go to the search service you pick. Nothing else is sent.
        </Note>
      </div>
    </Section>
  );
}

function Privacy({ reload }: { reload: () => void }) {
  const { data } = useJson<AppsPayload>("/api/apps");
  const [sel, setSel] = useState<Set<string> | null>(null);
  const [busy, setBusy] = useState(false);
  const [flash, show] = useFlash();
  useEffect(() => {
    if (data && !sel) setSel(new Set(data.apps.filter((r) => r.blocked).map((r) => r.value)));
  }, [data, sel]);
  const before = useMemo(() => new Set(data?.apps.filter((r) => r.blocked).map((r) => r.value) || []), [data]);
  const dirty = !!sel && (sel.size !== before.size || [...sel].some((x) => !before.has(x)));
  const save = async () => {
    if (!data || !sel) return;
    setBusy(true);
    await post("/api/settings", { llm: { private_apps: blockList(data, sel) } }).catch(() => {});
    setBusy(false);
    show();
    reload();
  };
  return (
    <Section
      id="privacy"
      title="Blocked apps"
      lead="One list for the typed-text monitor, screen text and the activity summary. Listed from the apps installed on this machine."
      right={
        <div className="flex items-center gap-3">
          <SavedFlash show={flash} />
          <Button variant="primary" size="sm" disabled={!dirty} busy={busy} onClick={save}>
            Save list
          </Button>
        </div>
      }
    >
      {data && sel ? <AppPicker data={data} selected={sel} onChange={setSel} /> : <div className="h-40 animate-pulse rounded-xl bg-raised" />}
    </Section>
  );
}

const NUMBERS: { section: "agent" | "llm" | "gate"; key: string; label: string; unit?: string; hint: string }[] = [
  { section: "agent", key: "assess_interval_s", label: "Ask the model at most every", unit: "s", hint: "while the score stays above the line" },
  { section: "agent", key: "cooldown_s", label: "Quiet after an offer", unit: "s", hint: "stretched after ignored or dismissed offers" },
  { section: "agent", key: "offer_timeout_s", label: "An offer waits", unit: "s", hint: "then counts as ignored" },
  { section: "agent", key: "tick_s", label: "Score every", unit: "s", hint: "how often ActivityWatch is read" },
  { section: "agent", key: "typed_text_max_chars", label: "Typed text kept", unit: "chars", hint: "newest characters handed to the model" },
  { section: "llm", key: "screen_chars", label: "Screen text kept", unit: "chars", hint: "around the cursor" },
  { section: "llm", key: "lookback_s", label: "Activity shown to the model", unit: "s", hint: "how far back the summary goes" },
  { section: "gate", key: "c0", label: "Cost of a false alarm", hint: "PRISM c₀ (base)" },
  { section: "gate", key: "m0", label: "Cost of a missed stuck", hint: "PRISM m₀ (base)" },
  { section: "gate", key: "delta", label: "Near-miss margin", hint: "close calls are asked again" },
  { section: "gate", key: "theta_flow", label: "Flow that defers a near miss", hint: "0 to 1" },
];

function Behaviour({ s, reload }: { s: SettingsT; reload: () => void }) {
  const read = () => Object.fromEntries(NUMBERS.map((n) => [`${n.section}.${n.key}`, String((s as any)[n.section][n.key])]));
  const [v, setV] = useState<Record<string, string>>(read);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [flash, show] = useFlash();
  useEffect(() => setV(read()), [s]); // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = NUMBERS.some((n) => v[`${n.section}.${n.key}`] !== String((s as any)[n.section][n.key]));
  const save = async () => {
    const body: Record<string, Record<string, number>> = {};
    for (const n of NUMBERS) {
      const raw = v[`${n.section}.${n.key}`];
      const num = Number(raw);
      if (raw.trim() === "" || Number.isNaN(num) || num < 0) {
        setErr(`“${n.label}” must be a number`);
        return;
      }
      (body[n.section] ||= {})[n.key] = num;
    }
    setErr(null);
    setBusy(true);
    try {
      await post("/api/settings", body);
      show();
      reload();
    } catch (e: any) {
      setErr(e.message);
    }
    setBusy(false);
  };
  return (
    <Section
      id="behaviour"
      title="Timing & gate"
      lead="How often SAIP looks, how long it stays quiet, and what an interruption costs. The defaults follow the PRISM gate in docs/State_Aware_Interruption_Formulas.md."
      right={
        <div className="flex items-center gap-3">
          <SavedFlash show={flash} />
          <Button variant="primary" size="sm" disabled={!dirty} busy={busy} onClick={save}>
            Save
          </Button>
        </div>
      }
    >
      <div className="grid gap-x-6 gap-y-4 sm:grid-cols-2">
        {NUMBERS.map((n) => (
          <label key={n.key} className="grid grid-cols-[1fr_120px] items-center gap-3">
            <span>
              <span className="block text-[13.5px] text-ink">{n.label}</span>
              <span className="block text-[12px] text-faint">{n.hint}</span>
            </span>
            <span className="relative">
              <Input value={v[`${n.section}.${n.key}`]} onChange={(e) => setV({ ...v, [`${n.section}.${n.key}`]: e.target.value })} className="num pr-12 text-right" inputMode="decimal" />
              {n.unit && <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-[11.5px] text-faint">{n.unit}</span>}
            </span>
          </label>
        ))}
      </div>
      {err && <p className="mt-4 text-[13px] text-danger">{err}</p>}
    </Section>
  );
}

function SavedChats({ s, reload }: { s: SettingsT; reload: () => void }) {
  const list = useJson<{ chats: { id: number }[]; keeping: boolean; path: string | null }>("/api/chats");
  const [busy, setBusy] = useState(false);
  const [sure, setSure] = useState(false);
  const [flash, show] = useFlash();
  const on = !!s.agent.keep_chats;
  const count = list.data?.chats.length ?? 0;
  const set = async (v: boolean) => {
    setBusy(true);
    await post("/api/settings", { agent: { keep_chats: v } }).catch(() => {});
    setBusy(false);
    show();
    reload();
    list.reload();
  };
  const wipe = async () => {
    setBusy(true);
    await post("/api/chats/clear").catch(() => {});
    setBusy(false);
    setSure(false);
    list.reload();
  };
  return (
    <Section id="chats" title="Saved chats" lead="Chats are kept in memory and disappear when SAIP stops, unless you keep them." right={<SavedFlash show={flash} />}>
      <div className="flex items-center gap-4 rounded-xl border border-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="text-[14px] text-ink">Keep chats between runs</div>
          <div className="text-[12px] leading-relaxed text-faint">
            Saves them to <span className="num">chats.jsonl</span> in the settings folder, readable only by you. Chats can hold your screen text, typed text and file contents. Turning this off deletes the file.
          </div>
        </div>
        <Toggle checked={on} disabled={busy} onChange={set} label="Keep chats between runs" />
      </div>
      <div className="mt-3 flex items-center gap-4 rounded-xl border border-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="text-[14px] text-ink">{count} {count === 1 ? "chat" : "chats"} {on ? "saved" : "this run"}</div>
          {list.data?.path && <div className="num truncate text-[12px] text-faint">{list.data.path}</div>}
        </div>
        {sure ? (
          <div className="flex items-center gap-2">
            <span className="text-[12.5px] text-muted">Delete all {count}?</span>
            <Button size="sm" variant="danger" busy={busy} onClick={wipe}>Yes, delete</Button>
            <Button size="sm" variant="ghost" onClick={() => setSure(false)}>Cancel</Button>
          </div>
        ) : (
          <Button size="sm" variant="danger" disabled={count === 0 || busy} onClick={() => setSure(true)}>Delete all chats</Button>
        )}
      </div>
    </Section>
  );
}

function Notifications({ s, reload }: { s: SettingsT; reload: () => void }) {
  const [busy, setBusy] = useState(false);
  const [flash, show] = useFlash();
  const set = async (on: boolean) => {
    setBusy(true);
    await post("/api/settings", { agent: { notify_offers: on } }).catch(() => {});
    setBusy(false);
    show();
    reload();
  };
  return (
    <Section id="notifications" title="Notifications" lead="A system notification with each offer, so you see it even when the dashboard is in the background." right={<SavedFlash show={flash} />}>
      <div className="flex items-center gap-4 rounded-xl border border-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="text-[14px] text-ink">Notify me when SAIP offers help</div>
          <div className="text-[12px] text-faint">
            On macOS the first one may need approving in System Settings → Notifications, for the app SAIP runs from (Script Editor or your terminal).
          </div>
        </div>
        <Toggle checked={s.agent.notify_offers} disabled={busy} onChange={set} label="Notify me when SAIP offers help" />
      </div>
    </Section>
  );
}

function Startup() {
  const app = useLive((s) => s.app);
  const { data, reload } = useJson<{ enabled: boolean; where: string }>("/api/autostart");
  const [busy, setBusy] = useState(false);
  const toggle = async (on: boolean) => {
    setBusy(true);
    await post("/api/autostart", { enabled: on }).catch(() => {});
    await reload();
    setBusy(false);
  };
  const [stopping, setStopping] = useState(false);
  return (
    <Section id="startup" title="Startup" lead="SAIP can start when you log in, quietly, with the tray icon and no browser window.">
      <div className="flex items-center gap-4 rounded-xl border border-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="text-[14px] text-ink">Launch at login</div>
          <div className="num truncate text-[12px] text-faint">{data?.where}</div>
        </div>
        <Toggle checked={!!data?.enabled} disabled={busy || !data} onChange={toggle} label="Launch at login" />
      </div>
      <div className="mt-3 flex items-center gap-4 rounded-xl border border-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="text-[14px] text-ink">Stop SAIP</div>
          <div className="text-[12px] text-faint">Stops the agent, this dashboard and the tray icon. Start again with <code className="num">saip run</code>.</div>
        </div>
        <Button
          variant="danger"
          size="sm"
          busy={stopping}
          disabled={app?.demo}
          onClick={async () => {
            setStopping(true);
            await post("/api/shutdown").catch(() => {});
          }}
        >
          <Power size={14} /> Stop
        </Button>
      </div>
    </Section>
  );
}

function Health() {
  const { data, reload, loading } = useJson<{ checks: Check[] }>("/api/health");
  return (
    <Section
      id="health"
      title="Health"
      lead="What `saip doctor` checks: ActivityWatch's watchers, the terminal hook, git hooks and the model."
      right={
        <Button size="sm" variant="ghost" onClick={reload} busy={loading}>
          <RefreshCw size={14} /> Check again
        </Button>
      }
    >
      {data ? <CheckRows checks={data.checks} /> : <div className="h-32 animate-pulse rounded-xl bg-raised" />}
    </Section>
  );
}

export function Settings() {
  const { data: s, reload } = useJson<SettingsT>("/api/settings");
  const loadApp = useLive((st) => st.loadApp);
  const [active, setActive] = useState<string>("model");

  useEffect(() => {
    if (!s) return;
    const id = location.hash.slice(1);
    if (id) setTimeout(() => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
  }, [s]);
  useEffect(() => {
    const io = new IntersectionObserver((es) => es.forEach((e) => e.isIntersecting && setActive(e.target.id)), { rootMargin: "-30% 0px -60% 0px" });
    SECTIONS.forEach(([id]) => {
      const el = document.getElementById(id);
      if (el) io.observe(el);
    });
    return () => io.disconnect();
  }, [s]);

  const refresh = () => {
    reload();
    loadApp();
  };

  return (
    <div>
      <header className="mb-6">
        <h1 className="text-[30px] font-semibold tracking-[-0.025em]">Settings</h1>
        <p className="mt-1 num text-[12.5px] text-faint">{s?.path || "…"}</p>
      </header>
      <div className="grid gap-8 lg:grid-cols-[180px_1fr]">
        <nav className="sticky top-8 hidden self-start lg:block">
          <ul className="space-y-0.5">
            {SECTIONS.map(([id, label]) => (
              <li key={id}>
                <a
                  href={`#${id}`}
                  onClick={(e) => {
                    e.preventDefault();
                    history.replaceState(null, "", `#${id}`);
                    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
                  }}
                  className={cx("block rounded-lg px-3 py-1.5 text-[13px] transition-colors", active === id ? "bg-raised text-ink" : "text-muted hover:text-ink")}
                >
                  {label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
        {!s ? (
          <div className="h-96 animate-pulse rounded-2xl bg-surface" />
        ) : (
          <div className="min-w-0 space-y-5">
            <Section id="model" title="Model" lead="An OpenAI-compatible endpoint that returns log-probabilities. Changes are tested before they're saved.">
              <ModelForm initial={{ base_url: s.llm.base_url, model: s.llm.model, api_key_set: s.llm.api_key_set }} onSaved={refresh} />
            </Section>
            <Features s={s} reload={refresh} />
            <ChatTools s={s} reload={refresh} />
            <Privacy reload={refresh} />
            <SavedChats s={s} reload={refresh} />
            <Notifications s={s} reload={refresh} />
            <Section id="activitywatch" title="ActivityWatch" lead="Where SAIP's activity data comes from.">
              <ActivityWatchPanel />
            </Section>
            <Section id="repos" title="Git & terminal" lead="Commits count as progress; failed terminal commands count toward being stuck.">
              <ReposPanel />
              <div className="mt-5">
                <HookPanel />
              </div>
            </Section>
            <Behaviour s={s} reload={refresh} />
            <Startup />
            <Health />
          </div>
        )}
      </div>
    </div>
  );
}
