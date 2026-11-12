import { ArrowLeft, ArrowRight, Check, Eye, GitCommit, Keyboard, MousePointerClick, Sparkles, SquareTerminal } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { AppPicker, blockList } from "../components/AppPicker";
import { ActivityWatchPanel, CheckRows, FeatureToggles, HookPanel, ModelForm, Note, PermissionsPanel, ReposPanel, type FeatureValues, type ModelValues } from "../components/SetupPanels";
import { Button, Mark, Spinner, cx } from "../components/ui";
import { get, post } from "../lib/api";
import { navigate } from "../lib/router";
import { useLive } from "../lib/store";
import type { AppsPayload, Check as CheckRow } from "../lib/types";

type Status = {
  ready: boolean;
  done: boolean;
  model_set: boolean;
  api_key_set: boolean;
  llm: { base_url: string; model: string };
  features: FeatureValues;
  platform: string;
};

const STEPS = [
  { id: "welcome", title: "Welcome" },
  { id: "aw", title: "ActivityWatch" },
  { id: "model", title: "Model" },
  { id: "features", title: "What it can read" },
  { id: "permissions", title: "Permissions" },
  { id: "blocked", title: "Blocked apps" },
  { id: "hook", title: "Terminal" },
  { id: "repos", title: "Git folders" },
  { id: "review", title: "Start" },
] as const;

const KEY = "saip-welcome-step";

const READS = [
  { icon: Eye, a: "Which app and window is in front", b: "from ActivityWatch" },
  { icon: MousePointerClick, a: "How many keys and clicks", b: "counts, not keys" },
  { icon: SquareTerminal, a: "Whether terminal commands fail", b: "exit status only" },
  { icon: GitCommit, a: "When you commit", b: "the word “commit”" },
  { icon: Keyboard, a: "What you're typing, and screen text", b: "optional, next steps" },
  { icon: Sparkles, a: "Sent to your own model endpoint", b: "you set it next" },
];

function Step({ kicker, title, lead, children }: { kicker: string; title: string; lead?: ReactNode; children: ReactNode }) {
  return (
    <div>
      <div className="text-[12px] font-medium uppercase tracking-[0.14em] text-flow">{kicker}</div>
      <h1 className="mt-2 text-[30px] leading-tight font-semibold tracking-[-0.025em] text-ink">{title}</h1>
      {lead && <p className="mt-2.5 max-w-[62ch] text-[14.5px] leading-relaxed text-muted">{lead}</p>}
      <div className="mt-7">{children}</div>
    </div>
  );
}

export function Welcome() {
  const loadApp = useLive((s) => s.loadApp);
  const [status, setStatus] = useState<Status | null>(null);
  const [i, setI] = useState(() => {
    try {
      return Math.min(Number(localStorage.getItem(KEY) || 0), STEPS.length - 1);
    } catch {
      return 0;
    }
  });
  const [dir, setDir] = useState(1);
  const [model, setModel] = useState<ModelValues | null>(null);
  const [features, setFeatures] = useState<FeatureValues>({ typed: true, screen: true, titles: true });
  const [apps, setApps] = useState<AppsPayload | null>(null);
  const [blocked, setBlocked] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [health, setHealth] = useState<CheckRow[] | null>(null);
  const [finishing, setFinishing] = useState(false);
  const [finishError, setFinishError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    get<Status>("/api/setup/status").then((s) => {
      setStatus(s);
      setModel({ base_url: s.llm.base_url, model: s.llm.model, api_key_set: s.api_key_set });
      // A fresh setup starts with everything on; a repeat keeps what was chosen.
      if (s.done) setFeatures(s.features);
    });
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(KEY, String(i));
    } catch {
      /* fine */
    }
  }, [i]);
  useEffect(() => {
    if (STEPS[i].id === "blocked" && !apps) {
      get<AppsPayload>("/api/apps").then((d) => {
        setApps(d);
        setBlocked(new Set(d.apps.filter((r) => r.blocked || r.suggested).map((r) => r.value)));
      });
    }
    if (STEPS[i].id === "review") {
      setHealth(null);
      get<{ checks: CheckRow[] }>("/api/health").then((h) => setHealth(h.checks)).catch(() => setHealth([]));
    }
  }, [i, apps]);

  const step = STEPS[i].id;
  const modelOk = !!(model?.base_url && model?.model) && !!status;
  const canNext = step !== "model" || modelOk;

  const go = async (to: number) => {
    if (to > i) {
      setSaving(true);
      try {
        if (step === "features") {
          await post("/api/settings", { agent: { monitor_typed_content: features.typed }, llm: { read_screen: features.screen, include_titles: features.titles } });
        }
        if (step === "blocked" && apps) {
          await post("/api/settings", { llm: { private_apps: blockList(apps, blocked) } });
        }
      } catch {
        /* shown again on the review step */
      }
      setSaving(false);
    }
    setDir(to > i ? 1 : -1);
    setI(to);
  };

  const finish = async () => {
    setFinishing(true);
    setFinishError(null);
    try {
      const r = await post<{ ok: boolean; detail?: string }>("/api/setup/finish");
      if (!r.ok) throw new Error(r.detail || "could not finish");
      setDone(true);
      try {
        localStorage.removeItem(KEY);
      } catch {
        /* fine */
      }
      await loadApp();
      setTimeout(() => navigate("/", true), 1600);
    } catch (e: any) {
      setFinishError(e.message);
    }
    setFinishing(false);
  };

  const body = useMemo(() => {
    switch (step) {
      case "welcome":
        return (
          <div>
            <motion.div initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} transition={{ duration: 0.6 }} className="mb-8 flex h-16 w-16 items-center justify-center rounded-2xl border border-line bg-raised">
              <Mark size={40} draw />
            </motion.div>
            <h1 className="max-w-[18ch] text-[clamp(32px,4.4vw,46px)] leading-[1.05] font-semibold tracking-[-0.035em]">
              Notices when you're stuck. <span className="text-muted">Asks before it helps.</span>
            </h1>
            <p className="mt-5 max-w-[56ch] text-[15px] leading-relaxed text-muted">
              SAIP watches how you work, not what you work on, scores it every few seconds, and only when you look stuck asks a model whether stepping in is worth the interruption. This sets it up in a few minutes.
            </p>
            <div className="mt-8 grid max-w-[640px] gap-2.5 sm:grid-cols-2">
              {READS.map(({ icon: Icon, a, b }, k) => (
                <motion.div key={a} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 + k * 0.05 }} className="flex items-start gap-3 rounded-xl border border-line bg-surface px-3.5 py-3">
                  <Icon size={16} className="mt-0.5 shrink-0 text-flow" />
                  <div>
                    <div className="text-[13.5px] text-ink">{a}</div>
                    <div className="text-[12px] text-faint">{b}</div>
                  </div>
                </motion.div>
              ))}
            </div>
          </div>
        );
      case "aw":
        return (
          <Step kicker="Step 1" title="ActivityWatch" lead="SAIP reads your activity from ActivityWatch, a free, open-source tracker that keeps everything on this machine. It needs the window, AFK and input watchers.">
            <ActivityWatchPanel />
          </Step>
        );
      case "model":
        return (
          <Step kicker="Step 2 · required" title="Connect a model" lead="SAIP asks a language model two yes/no questions and reads its confidence from the log-probabilities, so the endpoint must return them. The key is stored in private.toml, readable only by you.">
            {model && <ModelForm initial={model} onSaved={(v) => setModel(v)} />}
            {!modelOk && <p className="mt-5 text-[12.5px] text-faint">Continue unlocks once a connection test passes.</p>}
          </Step>
        );
      case "features":
        return (
          <Step kicker="Step 3" title="What the model can read" lead="With more context the model's offers are about your actual work. All three are on; switch off any you'd rather keep on this machine.">
            <FeatureToggles value={features} onChange={setFeatures} />
          </Step>
        );
      case "permissions":
        return (
          <Step kicker="Step 4" title="Permissions" lead={status?.platform === "macOS" ? "macOS asks before an app may read keys or another app's text." : "What the features you picked need from this system."}>
            <PermissionsPanel typed={features.typed} screen={features.screen} />
          </Step>
        );
      case "blocked":
        return (
          <Step kicker="Step 5" title="Apps to keep private" lead="One list for everything: nothing typed in these apps is kept, their screen text is never read, and the model sees “Private app” instead of their name.">
            {apps ? <AppPicker data={apps} selected={blocked} onChange={setBlocked} /> : <div className="flex h-40 items-center justify-center text-muted"><Spinner /></div>}
          </Step>
        );
      case "hook":
        return (
          <Step kicker="Step 6 · optional" title="Terminal hook" lead="A few lines in your shell's start-up file report each command's exit status (never the command itself), so repeated failures count toward being stuck.">
            <HookPanel />
          </Step>
        );
      case "repos":
        return (
          <Step kicker="Step 7 · optional" title="Git folders" lead="Pick the folder that holds your projects. Commits count as progress, which pulls the score back down.">
            <ReposPanel />
          </Step>
        );
      case "review":
        return (
          <Step kicker="Last step" title={done ? "You're set." : "Ready to start"} lead={done ? "Opening your dashboard…" : "Here's how everything looks. Anything marked can be fixed later from Settings."}>
            {done ? (
              <motion.div initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ type: "spring", stiffness: 200, damping: 16 }} className="flex h-40 items-center justify-center">
                <div className="flex h-20 w-20 items-center justify-center rounded-full bg-flow text-white">
                  <Check size={38} strokeWidth={2.6} />
                </div>
              </motion.div>
            ) : (
              <div className="space-y-4">
                <dl className="grid gap-x-6 gap-y-2 rounded-xl border border-line px-4 py-3 text-[13px] sm:grid-cols-2">
                  {[
                    ["Model", model?.model || "not set"],
                    ["Typed-text monitor", features.typed ? "on" : "off"],
                    ["Screen text", features.screen ? "on" : "off"],
                    ["Window titles", features.titles ? "on" : "off"],
                    ["Blocked apps", apps ? String(blocked.size) : "unchanged"],
                  ].map(([k, v]) => (
                    <div key={k} className="flex justify-between gap-3">
                      <dt className="text-muted">{k}</dt>
                      <dd className="num truncate text-ink">{v}</dd>
                    </div>
                  ))}
                </dl>
                {health ? <CheckRows checks={health} /> : <div className="flex h-32 items-center justify-center gap-2 text-[13px] text-muted"><Spinner /> Checking everything (this asks your model once)…</div>}
                {finishError && <Note tone="warn">{finishError}</Note>}
              </div>
            )}
          </Step>
        );
    }
  }, [step, model, modelOk, features, status, apps, blocked, health, done, finishError]);

  if (!status) {
    return (
      <div className="flex h-screen items-center justify-center text-muted">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="grain relative min-h-screen overflow-hidden">
      <div aria-hidden className="pointer-events-none absolute -top-40 left-1/2 h-[520px] w-[900px] -translate-x-1/2 rounded-full opacity-[0.10] blur-3xl" style={{ background: "radial-gradient(closest-side, var(--color-flow), transparent)" }} />
      <div className="fixed inset-x-0 top-0 z-30 h-[3px] bg-transparent">
        <motion.div className="h-full bg-flow" animate={{ width: `${((i + 1) / STEPS.length) * 100}%` }} transition={{ type: "spring", stiffness: 120, damping: 24 }} />
      </div>
      <div className="relative z-10 mx-auto grid min-h-screen max-w-[1120px] gap-10 px-5 py-10 md:grid-cols-[220px_1fr] md:px-8 md:py-16">
        <aside className="hidden md:block">
          <div className="sticky top-16">
            <div className="mb-8 flex items-center gap-2.5">
              <Mark size={24} />
              <span className="text-[15px] font-semibold tracking-tight">SAIP setup</span>
            </div>
            <ol className="space-y-0.5">
              {STEPS.map((s, k) => (
                <li key={s.id}>
                  <button
                    disabled={k > i && !(k === i + 1 && canNext)}
                    onClick={() => go(k)}
                    className={cx("flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left text-[13px] transition-colors disabled:cursor-default", k === i ? "text-ink" : k < i ? "text-muted hover:text-ink" : "text-faint")}
                  >
                    <span className={cx("flex h-5 w-5 items-center justify-center rounded-full border text-[10.5px] transition-colors", k < i ? "border-flow bg-flow text-white" : k === i ? "border-ink text-ink" : "border-line-strong")}>
                      {k < i ? <Check size={11} strokeWidth={3} /> : <span className="num">{k + 1}</span>}
                    </span>
                    {s.title}
                  </button>
                </li>
              ))}
            </ol>
          </div>
        </aside>

        <main className="flex min-w-0 flex-col">
          <div className="relative flex-1">
            <AnimatePresence mode="wait" custom={dir} initial={false}>
              <motion.div
                key={step}
                custom={dir}
                initial={{ opacity: 0, x: dir * 28 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: dir * -28 }}
                transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1] }}
              >
                {body}
              </motion.div>
            </AnimatePresence>
          </div>
          {!done && (
            <div className="mt-10 flex items-center gap-3 border-t border-line pt-5">
              {i > 0 && (
                <Button variant="ghost" onClick={() => go(i - 1)}>
                  <ArrowLeft size={15} /> Back
                </Button>
              )}
              <span className="num ml-auto text-[12px] text-faint">
                {i + 1} / {STEPS.length}
              </span>
              {step === "review" ? (
                <Button size="lg" variant="primary" busy={finishing} disabled={!modelOk} onClick={finish}>
                  Start SAIP <ArrowRight size={16} />
                </Button>
              ) : (
                <Button size="lg" variant="primary" busy={saving} disabled={!canNext} onClick={() => go(i + 1)}>
                  {step === "welcome" ? "Set up SAIP" : "Continue"} <ArrowRight size={16} />
                </Button>
              )}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
