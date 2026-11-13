// Pieces shared by the setup wizard and the settings page. Each loads and saves through the local API.
import { AlertTriangle, CheckCircle2, FolderGit2, Keyboard, KeyRound, MonitorSmartphone, RefreshCw, ScrollText, Terminal, Type, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState, type ReactNode } from "react";
import { post } from "../lib/api";
import { useJson } from "../lib/hooks";
import type { Check, RepoRow } from "../lib/types";
import { Button, Field, Input, Pill, StatusDot, Toggle, cx } from "./ui";

export function Note({ tone = "muted", icon, children }: { tone?: "muted" | "warn" | "ok"; icon?: ReactNode; children: ReactNode }) {
  return (
    <div
      className={cx(
        "flex gap-2.5 rounded-xl border px-3.5 py-2.5 text-[13px] leading-relaxed",
        tone === "warn" ? "border-stuck/35 bg-stuck-soft text-ink" : tone === "ok" ? "border-flow/35 bg-flow-soft text-ink" : "border-line bg-raised text-muted",
      )}
    >
      {icon && <span className={cx("mt-0.5 shrink-0", tone === "warn" ? "text-stuck" : tone === "ok" ? "text-flow" : "text-faint")}>{icon}</span>}
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export function CheckRows({ checks }: { checks: Check[] }) {
  return (
    <ul className="divide-y divide-line rounded-xl border border-line">
      {checks.map((c) => (
        <li key={c.name} className="flex items-start gap-3 px-3.5 py-2.5 text-[13px]">
          <span className="mt-1.5">
            <StatusDot status={c.status} />
          </span>
          <span className="w-36 shrink-0 font-medium text-ink">{c.name}</span>
          <span className="min-w-0 flex-1 break-words text-muted">{c.detail || (c.status === "ok" ? "ok" : c.status)}</span>
        </li>
      ))}
    </ul>
  );
}

// ---- ActivityWatch ----
type AwStatus = { running: boolean; installed: boolean; url: string; checks: Check[]; permission_hint: string; ok: boolean };

export function ActivityWatchPanel() {
  const { data, reload } = useJson<AwStatus>("/api/setup/aw", 4000);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const act = async (action: "install" | "start") => {
    setBusy(action);
    setMsg(null);
    try {
      const r = await post<{ ok: boolean; detail: string }>("/api/setup/aw", { action });
      setMsg(r.ok ? null : r.detail);
    } catch (e: any) {
      setMsg(e.message);
    }
    setBusy(null);
    reload();
  };
  if (!data) return <div className="h-24 animate-pulse rounded-xl bg-raised" />;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Pill tone={data.running ? "flow" : data.installed ? "stuck" : "danger"}>
          {data.running ? "Running on localhost:5600" : data.installed ? "Installed, not running" : "Not installed"}
        </Pill>
        {!data.installed && (
          <Button size="sm" variant="primary" busy={busy === "install"} onClick={() => act("install")}>
            Install ActivityWatch
          </Button>
        )}
        {data.installed && !data.running && (
          <Button size="sm" variant="primary" busy={busy === "start"} onClick={() => act("start")}>
            Start it
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={reload}>
          <RefreshCw size={14} /> Check again
        </Button>
      </div>
      {msg && <Note tone="warn" icon={<AlertTriangle size={15} />}>{msg}. You can also download it from <a className="underline" href={data.url} target="_blank" rel="noreferrer">activitywatch.net</a>.</Note>}
      {data.checks.length > 0 && <CheckRows checks={data.checks} />}
      {data.checks.some((c) => c.name === "input watcher" && c.status !== "ok") && (
        <Note icon={<AlertTriangle size={15} />}>
          The input watcher doesn't start by itself: right-click the ActivityWatch icon in the menu bar or tray, open <b className="text-ink">Modules</b> and tick <b className="text-ink">aw-watcher-input</b>.
        </Note>
      )}
      {data.permission_hint && data.checks.some((c) => c.status === "needs-you") && <Note>{data.permission_hint}</Note>}
    </div>
  );
}

// ---- the model ----
export type ModelValues = { base_url: string; model: string; api_key_set: boolean };
type TestResult = { ok: boolean; detail: string; hint?: string; seconds?: number; model?: string; models?: string[]; reasoning_effort?: string };

const PRESETS = [
  { label: "vLLM", url: "http://localhost:8000/v1" },
  { label: "Ollama", url: "http://localhost:11434/v1" },
  { label: "llama.cpp", url: "http://localhost:8080/v1" },
];

/** URL, model and key; "Test and save" probes the endpoint for log-probabilities and saves only if it works. */
export function ModelForm({ initial, onSaved }: { initial: ModelValues; onSaved?: (v: ModelValues) => void }) {
  const [url, setUrl] = useState(initial.base_url);
  const [model, setModel] = useState(initial.model);
  const [key, setKey] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  const [saved, setSaved] = useState(Boolean(initial.base_url && initial.model));
  const dirty = url !== initial.base_url || model !== initial.model || key !== "";

  useEffect(() => setSaved(Boolean(initial.base_url && initial.model) && !dirty), [initial, dirty]);

  const testAndSave = async () => {
    setBusy(true);
    setResult(null);
    try {
      const r = await post<TestResult>("/api/setup/endpoint", { base_url: url, model: model.trim(), api_key: key || null });
      const resolvedModel = (r.model ?? model).trim();
      if (r.model) setModel(r.model);
      setModels(r.models && r.models.length > 1 ? r.models : []);
      setResult(r);
      if (r.ok && resolvedModel) {
        const llm: Record<string, unknown> = { base_url: url.trim(), model: resolvedModel, api_key: key ? key : null };
        if (r.reasoning_effort) llm.reasoning_effort = r.reasoning_effort;
        await post("/api/settings", { llm });
        setSaved(true);
        setKey("");
        setModels([]);
        onSaved?.({ base_url: url.trim(), model: resolvedModel, api_key_set: initial.api_key_set || !!key });
      }
    } catch (e: any) {
      setResult({ ok: false, detail: e.message, hint: "" });
    }
    setBusy(false);
  };

  return (
    <div className="space-y-4">
      <Field label="Endpoint URL" hint="Any OpenAI-compatible server that returns log-probabilities.">
        <Input value={url} onChange={(e) => { setUrl(e.target.value); setModels([]); }} placeholder="http://host:8000/v1" spellCheck={false} autoComplete="off" />
      </Field>
      <div className="-mt-2 flex flex-wrap gap-1.5">
        {PRESETS.map((p) => (
          <button key={p.label} onClick={() => { setUrl(p.url); setModels([]); }} className="rounded-full border border-line px-2.5 py-0.5 text-[11.5px] text-muted transition-colors hover:border-line-strong hover:text-ink">
            {p.label}
          </button>
        ))}
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Model name" hint="Leave blank to auto-detect from /models.">
          <Input value={model} onChange={(e) => setModel(e.target.value)} placeholder="auto-detect from URL, or enter model ID" spellCheck={false} autoComplete="off" />
        </Field>
        <Field label="API key" hint={initial.api_key_set ? "A key is saved. Leave empty to keep it." : "Leave empty if the server needs none."}>
          <Input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder={initial.api_key_set ? "•••••••• saved" : "optional"} autoComplete="new-password" />
        </Field>
      </div>
      {models.length > 1 && (
        <div className="-mt-1 flex flex-wrap gap-1.5">
          {models.map((m) => (
            <button
              key={m}
              onClick={() => setModel(m)}
              className={cx(
                "num rounded-full border px-2.5 py-0.5 text-[11.5px] transition-colors",
                model.trim() === m ? "border-ink bg-ink text-canvas" : "border-line text-muted hover:border-line-strong hover:text-ink",
              )}
            >
              {m}
            </button>
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" busy={busy} disabled={!url.trim()} onClick={testAndSave}>
          <KeyRound size={15} /> {saved && !dirty ? "Test again" : "Test and save"}
        </Button>
        <AnimatePresence mode="wait">
          {saved && !dirty && !result && (
            <motion.span key="saved" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex items-center gap-1.5 text-[13px] text-flow">
              <CheckCircle2 size={15} /> Saved
            </motion.span>
          )}
        </AnimatePresence>
      </div>
      <AnimatePresence>
        {result && (
          <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
            {result.ok ? (
              <Note tone="ok" icon={<CheckCircle2 size={15} />}>
                Connected{result.seconds != null && <span className="num"> in {result.seconds.toFixed(2)} s</span>} and saved. {result.detail}
              </Note>
            ) : (
              <Note tone="warn" icon={<AlertTriangle size={15} />}>
                <b className="font-medium">{result.hint || "That didn't work."}</b>
                <span className="mt-0.5 block break-words text-muted">{result.detail}</span>
              </Note>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ---- features ----
export type FeatureValues = { typed: boolean; screen: boolean; titles: boolean };

const FEATURES = [
  {
    key: "typed" as const,
    icon: Keyboard,
    title: "Typed-text monitor",
    body: "Keeps the last ~200 characters you typed, in memory only, so the model knows what you're writing. Off in blocked apps; Esc clears it, F1 turns it off.",
  },
  {
    key: "screen" as const,
    icon: Type,
    title: "Screen text",
    body: "When an offer is written, reads the text around your cursor in the front app, so the help is about your actual work. Never password fields or blocked apps.",
  },
  {
    key: "titles" as const,
    icon: MonitorSmartphone,
    title: "Window titles",
    body: "Includes window titles (file names, page titles) in the activity summary the model sees, not just app names.",
  },
];

export function FeatureToggles({ value, onChange }: { value: FeatureValues; onChange: (v: FeatureValues) => void }) {
  return (
    <div className="space-y-2.5">
      {FEATURES.map(({ key, icon: Icon, title, body }) => (
        <div key={key} className={cx("flex gap-4 rounded-2xl border p-4 transition-colors", value[key] ? "border-flow/40 bg-flow-soft/40" : "border-line bg-raised/50")}>
          <span className={cx("mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl", value[key] ? "bg-flow text-white" : "bg-raised text-muted")}>
            <Icon size={17} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-[14px] font-medium text-ink">{title}</div>
            <p className="mt-1 text-[13px] leading-relaxed text-muted">{body}</p>
          </div>
          <Toggle checked={value[key]} onChange={(v) => onChange({ ...value, [key]: v })} label={title} />
        </div>
      ))}
      {(value.typed || value.screen) && (
        <Note icon={<AlertTriangle size={15} />}>
          This text goes to the model endpoint you set. Obvious secrets (API keys, tokens, “password=…”) are masked first, but a password typed
          into an app you don't block can reach the buffer. Block password managers, chat and mail on the blocked-apps step; browsers too if that matters to you.
        </Note>
      )}
    </div>
  );
}

// ---- OS permissions ----
type PermRow = { id: string; label: string; needed_for: string; granted: boolean | null; how: string; can_request: boolean };

export function PermissionsPanel({ typed, screen }: { typed: boolean; screen: boolean }) {
  const { data, reload } = useJson<{ rows: PermRow[]; host_app: string }>(`/api/setup/permissions?typed=${typed ? 1 : 0}&screen=${screen ? 1 : 0}`, 5000);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<{ available: boolean; presses: number; detail: string } | null>(null);
  const ask = async (id: string) => {
    await post("/api/setup/permissions", { id }).catch(() => {});
    setTimeout(reload, 1500);
  };
  const keyTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      setTest(await post("/api/setup/keytest", { seconds: 6 }));
    } catch (e: any) {
      setTest({ available: false, presses: 0, detail: e.message });
    }
    setTesting(false);
  };
  if (!data) return <div className="h-24 animate-pulse rounded-xl bg-raised" />;
  return (
    <div className="space-y-3">
      {data.rows.length === 0 ? (
        <Note tone="ok" icon={<CheckCircle2 size={15} />}>Nothing to grant on this system.</Note>
      ) : (
        <ul className="space-y-2">
          {data.rows.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-line px-4 py-3">
              <StatusDot status={r.granted ? "ok" : r.granted === false ? "needs-you" : "skipped"} />
              <div className="min-w-0 flex-1">
                <div className="text-[13.5px] font-medium text-ink">
                  {r.label} <span className="font-normal text-faint">· for the {r.needed_for}</span>
                </div>
                <div className="mt-0.5 text-[12.5px] text-muted">{r.granted ? "Granted." : r.how}</div>
              </div>
              {!r.granted && r.can_request && (
                <Button size="sm" onClick={() => ask(r.id)}>
                  Open settings
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {typed && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-dashed border-line-strong px-4 py-3">
          <Keyboard size={16} className="text-muted" />
          <span className="min-w-0 flex-1 text-[13px] text-muted">
            Check that key presses reach SAIP: start the test, then type a few keys anywhere. Only a count is kept.
          </span>
          <Button size="sm" variant="primary" busy={testing} onClick={keyTest}>
            Test the keyboard
          </Button>
          {test && (
            <span className={cx("w-full text-[13px]", test.presses > 0 ? "text-flow" : "text-stuck")}>
              {test.presses > 0 ? `Received ${test.presses} key presses. The monitor will work.` : test.detail || "No keys arrived."}
            </span>
          )}
        </div>
      )}
      {data.rows.some((r) => !r.granted) && (
        <Note>
          macOS grants these to <b className="text-ink">{data.host_app}</b>, the app SAIP was started from. After you switch one on, restart SAIP for it to take effect. You can carry on and do it later.
        </Note>
      )}
    </div>
  );
}

// ---- terminal hook ----
type Hook = { shell: string; profile: string | null; supported: boolean; installed: boolean };

export function HookPanel() {
  const { data, reload } = useJson<Hook>("/api/setup/hook");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const act = async (action: "install" | "remove") => {
    setBusy(true);
    const r = await post<{ ok: boolean; detail: string }>("/api/setup/hook", { action }).catch((e) => ({ ok: false, detail: e.message }));
    setMsg(r.detail);
    setBusy(false);
    reload();
  };
  if (!data) return <div className="h-20 animate-pulse rounded-xl bg-raised" />;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-line px-4 py-3">
        <Terminal size={16} className="text-muted" />
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] text-ink">
            {data.shell} <span className="text-faint">· {data.profile || "no start-up file for this shell"}</span>
          </div>
          <div className="text-[12.5px] text-muted">{data.installed ? "Installed. New terminal tabs report failed commands." : "Not installed."}</div>
        </div>
        {data.supported &&
          (data.installed ? (
            <Button size="sm" variant="ghost" busy={busy} onClick={() => act("remove")}>
              Remove
            </Button>
          ) : (
            <Button size="sm" variant="primary" busy={busy} onClick={() => act("install")}>
              Add the hook
            </Button>
          ))}
      </div>
      {msg && <p className="text-[12.5px] text-muted">{msg}</p>}
    </div>
  );
}

// ---- git folders ----
type Repos = { roots: string[]; exclude: string[]; suggestions: string[]; repos: RepoRow[]; summary: string; undecided: { path: string; state: string }[] };
type Preview = { folder: string; count: number; too_many: boolean; summary: string; repos: RepoRow[] };

export function ReposPanel({ onUndecided }: { onUndecided?: (n: number) => void }) {
  const { data, reload } = useJson<Repos>("/api/repos");
  useEffect(() => {
    if (data) onUndecided?.(data.undecided.length);
  }, [data, onUndecided]);
  const decideAll = async (action: "add" | "decline") => {
    setBusy("all-" + action);
    setError(null);
    try {
      for (const r of data?.roots || []) await post("/api/repos", { action, folder: r });
    } catch (e: any) {
      setError(e.message);
    }
    setBusy(null);
    reload();
  };
  const [folder, setFolder] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const look = async (f: string) => {
    setFolder(f);
    setBusy("preview");
    setError(null);
    try {
      setPreview(await post<Preview>("/api/repos/preview", { folder: f }));
    } catch (e: any) {
      setError(e.message);
      setPreview(null);
    }
    setBusy(null);
  };
  const act = async (action: "add" | "remove", f: string) => {
    setBusy(action + f);
    setError(null);
    try {
      await post("/api/repos", { action, folder: f });
      setPreview(null);
      setFolder("");
    } catch (e: any) {
      setError(e.message);
    }
    setBusy(null);
    reload();
  };
  if (!data) return <div className="h-24 animate-pulse rounded-xl bg-raised" />;
  return (
    <div className="space-y-3">
      <AnimatePresence>
        {data.undecided.length > 0 && (
          <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden rounded-2xl border border-stuck/45 bg-stuck-soft p-4">
            <div className="flex items-start gap-3">
              <FolderGit2 size={18} className="mt-0.5 shrink-0 text-stuck" />
              <div className="min-w-0 flex-1">
                <div className="text-[14.5px] font-medium text-ink">
                  Found {data.undecided.length} git {data.undecided.length === 1 ? "repository" : "repositories"} you haven't decided on
                </div>
                <p className="mt-1 text-[13px] leading-relaxed text-muted">
                  Add them and SAIP puts a small git hook in each that records only the word “commit” when you commit. Skip them and SAIP never asks about these again.
                </p>
                <ul className="scroll-thin mt-2.5 max-h-28 overflow-auto">
                  {data.undecided.map((r) => (
                    <li key={r.path} className="num truncate py-0.5 text-[12px] text-muted">{r.path}</li>
                  ))}
                </ul>
                <div className="mt-3.5 flex flex-wrap gap-2">
                  <Button size="sm" variant="primary" busy={busy === "all-add"} onClick={() => decideAll("add")}>
                    Add all {data.undecided.length}
                  </Button>
                  <Button size="sm" variant="subtle" busy={busy === "all-decline"} onClick={() => decideAll("decline")}>
                    Don't add these
                  </Button>
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      {data.roots.length > 0 && (
        <ul className="space-y-1.5">
          {data.roots.map((r) => (
            <li key={r} className="flex items-center gap-3 rounded-xl border border-line px-4 py-2.5">
              <FolderGit2 size={16} className="text-flow" />
              <span className="num min-w-0 flex-1 truncate text-[13px] text-ink">{r}</span>
              <Button size="sm" variant="ghost" busy={busy === "remove" + r} onClick={() => act("remove", r)} aria-label={`Stop watching ${r}`}>
                <X size={14} />
              </Button>
            </li>
          ))}
          <li className="px-1 text-[12.5px] text-muted">{data.summary}</li>
        </ul>
      )}
      <div className="flex gap-2">
        <Input value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="~/Developer" spellCheck={false} onKeyDown={(e) => e.key === "Enter" && folder && look(folder)} className="num" />
        <Button busy={busy === "preview"} disabled={!folder.trim()} onClick={() => look(folder)}>
          Look inside
        </Button>
      </div>
      {data.suggestions.filter((s) => !data.roots.includes(s)).length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {data.suggestions
            .filter((s) => !data.roots.includes(s))
            .map((s) => (
              <button key={s} onClick={() => look(s)} className="num rounded-full border border-line px-2.5 py-0.5 text-[12px] text-muted transition-colors hover:border-line-strong hover:text-ink">
                {s}
              </button>
            ))}
        </div>
      )}
      {error && <Note tone="warn" icon={<AlertTriangle size={15} />}>{error}</Note>}
      <AnimatePresence>
        {preview && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
            <div className="rounded-xl border border-line">
              <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2.5">
                <ScrollText size={15} className="text-muted" />
                <span className="flex-1 text-[13px] text-ink">
                  {preview.folder}: {preview.summary}
                </span>
                <Button size="sm" variant="primary" disabled={preview.count === 0} busy={busy === "add" + preview.folder} onClick={() => act("add", preview.folder)}>
                  Watch this folder
                </Button>
              </div>
              <ul className="scroll-thin max-h-44 overflow-auto px-4 py-2">
                {preview.repos.map((r) => (
                  <li key={r.path} className="flex items-center gap-2 py-1 text-[12.5px]">
                    <StatusDot status={r.state === "skip" ? "skipped" : "ok"} />
                    <span className="num truncate text-muted">{r.path}</span>
                    {r.detail && <span className="truncate text-faint">· {r.detail}</span>}
                  </li>
                ))}
              </ul>
              {preview.too_many && <div className="border-t border-line px-4 py-2 text-[12.5px] text-stuck">That's a lot of repositories. A narrower folder may be better.</div>}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <p className="text-[12.5px] text-faint">Each repository gets a small git hook that records only the word “commit” when you commit: SAIP counts commits as progress.</p>
    </div>
  );
}
