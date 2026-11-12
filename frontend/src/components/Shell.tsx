import * as Menu from "@radix-ui/react-dropdown-menu";
import { Activity, History, Monitor, Moon, PauseCircle, PlayCircle, ScanSearch, Settings2, Sun } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState, type ReactNode } from "react";
import { post } from "../lib/api";
import { clock } from "../lib/format";
import { Link, usePath } from "../lib/router";
import { STATE_WORDS, stateOf, useLive } from "../lib/store";
import { AskStack } from "./Signals";
import { Mark, STATE_COLOR, cx } from "./ui";

const NAV = [
  { to: "/", label: "Live", icon: Activity },
  { to: "/history", label: "History", icon: History },
  { to: "/inspector", label: "Inspector", icon: ScanSearch },
  { to: "/settings", label: "Settings", icon: Settings2 },
];

type Theme = "system" | "dark" | "light";

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("saip-theme") as Theme) || "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    const el = document.documentElement;
    if (theme === "system") delete el.dataset.theme;
    else el.dataset.theme = theme;
    try {
      if (theme === "system") localStorage.removeItem("saip-theme");
      else localStorage.setItem("saip-theme", theme);
    } catch {
      /* private window: the choice lasts until reload */
    }
  }, [theme]);
  const next = () => setTheme((t) => (t === "system" ? "dark" : t === "dark" ? "light" : "system"));
  return [theme, next];
}

function PauseMenu() {
  const quietUntil = useLive((s) => s.quietUntil);
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 15000);
    return () => clearInterval(t);
  }, []);
  const quiet = quietUntil > now;
  const tomorrow = () => {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(9, 0, 0, 0);
    return Math.max(1, (d.getTime() / 1000 - Date.now() / 1000) / 60);
  };
  const pause = (m: number) => post("/api/pause", { minutes: m }).catch(() => {});
  return (
    <Menu.Root>
      <Menu.Trigger asChild>
        <button
          className={cx(
            "flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left text-[13px] transition-colors",
            quiet ? "bg-stuck-soft text-stuck" : "text-muted hover:bg-raised hover:text-ink",
          )}
        >
          {quiet ? <PlayCircle size={16} /> : <PauseCircle size={16} />}
          <span className="truncate">{quiet ? `Offers paused · ${clock(quietUntil)}` : "Pause offers"}</span>
        </button>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content side="top" align="start" sideOffset={6} className="z-50 min-w-[200px] rounded-xl border border-line bg-raised p-1 text-[13px] shadow-2xl">
          {[
            ["For 30 minutes", 30],
            ["For an hour", 60],
            ["Until tomorrow morning", -1],
          ].map(([label, m]) => (
            <Menu.Item
              key={label as string}
              onSelect={() => pause(m === -1 ? tomorrow() : (m as number))}
              className="cursor-pointer rounded-lg px-3 py-2 text-ink outline-none data-[highlighted]:bg-canvas"
            >
              {label}
            </Menu.Item>
          ))}
          {quiet && (
            <>
              <Menu.Separator className="my-1 h-px bg-line" />
              <Menu.Item onSelect={() => pause(0)} className="cursor-pointer rounded-lg px-3 py-2 text-flow outline-none data-[highlighted]:bg-canvas">
                Let offers through again
              </Menu.Item>
            </>
          )}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}

function Banner() {
  const link = useLive((s) => s.link);
  const app = useLive((s) => s.app);
  const demo = useLive((s) => s.demo);
  let tone: "warn" | "info" | null = null;
  let text: ReactNode = null;
  if (link === "lost") {
    tone = "warn";
    text = "Lost the agent. Reconnecting… (is `saip run` still going?)";
  } else if (app?.runtime?.error) {
    tone = "warn";
    text = `The agent stopped: ${app.runtime.error}`;
  } else if (app?.runtime?.mode === "score-only") {
    tone = "info";
    text = (
      <>
        No model is configured, so scores are shown but nothing is decided.{" "}
        <Link to="/settings#model" className="underline underline-offset-2">Add one in Settings</Link>.
      </>
    );
  } else if (app?.runtime?.mode === "mock") {
    tone = "info";
    text = "Running with the mock model (--mock-llm): its numbers and offers are canned.";
  } else if (demo) {
    tone = "info";
    text = "Demo: made-up activity. Nothing on this machine is read or changed.";
  }
  return (
    <AnimatePresence>
      {tone && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          className="overflow-hidden"
        >
          <div className={cx("mb-5 rounded-xl border px-4 py-2.5 text-[13px]", tone === "warn" ? "border-stuck/40 bg-stuck-soft text-stuck" : "border-line bg-raised text-muted")}>
            {text}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const path = usePath();
  const link = useLive((s) => s.link);
  const tick = useLive((s) => s.ticks[s.ticks.length - 1]);
  const asks = useLive((s) => s.asks);
  const app = useLive((s) => s.app);
  const inspectorCount = useLive((s) => s.inspectorCount);
  const [theme, nextTheme] = useTheme();
  const state = stateOf(tick, link);
  const ThemeIcon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor;

  useEffect(() => {
    document.title = state === "stuck" ? "SAIP · possibly stuck" : "SAIP";
  }, [state]);

  return (
    <div className="grain relative flex min-h-full">
      <aside className="sticky top-0 z-20 hidden h-screen w-[232px] shrink-0 flex-col border-r border-line bg-surface/60 px-3 py-5 backdrop-blur md:flex">
        <Link to="/" className="flex items-center gap-2.5 px-3 pb-6">
          <Mark state={state} size={26} draw />
          <span className="text-[15px] font-semibold tracking-tight">SAIP</span>
          {app?.demo && <span className="ml-auto rounded-md bg-stuck-soft px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-wider text-stuck">demo</span>}
        </Link>
        <nav className="flex flex-col gap-0.5">
          {NAV.map(({ to, label, icon: Icon }) => {
            const on = to === "/" ? path === "/" : path.startsWith(to);
            return (
              <Link key={to} to={to} className={cx("relative flex items-center gap-2.5 rounded-xl px-3 py-2 text-[13.5px] transition-colors", on ? "text-ink" : "text-muted hover:text-ink")}>
                {on && <motion.span layoutId="nav-on" className="absolute inset-0 rounded-xl bg-raised" transition={{ type: "spring", stiffness: 400, damping: 34 }} />}
                <Icon size={16} className="relative" />
                <span className="relative">{label}</span>
                {to === "/inspector" && inspectorCount > 0 && <span className="num relative ml-auto text-[11px] text-faint">{inspectorCount}</span>}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto space-y-1">
          <div className="mb-3 rounded-xl border border-line bg-canvas/50 px-3 py-2.5">
            <div className="flex items-center gap-2 text-[12.5px]">
              <span className="relative flex h-2 w-2">
                {link === "live" && <span className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60" style={{ background: STATE_COLOR[state] }} />}
                <span className="relative inline-flex h-2 w-2 rounded-full" style={{ background: STATE_COLOR[state] }} />
              </span>
              <span className="text-ink">{STATE_WORDS[state]}</span>
            </div>
            <div className="mt-1 truncate text-[11.5px] text-faint">
              {app?.runtime?.model ? `model ${app.runtime.model}` : link === "live" ? "connected" : "connecting…"}
            </div>
          </div>
          <PauseMenu />
          <button onClick={nextTheme} className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-[13px] text-muted transition-colors hover:bg-raised hover:text-ink">
            <ThemeIcon size={16} />
            <span className="capitalize">{theme === "system" ? "System theme" : `${theme} theme`}</span>
          </button>
        </div>
      </aside>

      <div className="relative z-10 min-w-0 flex-1">
        <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-line bg-canvas/80 px-4 py-3 backdrop-blur md:hidden">
          <Mark state={state} size={22} />
          <span className="font-semibold">SAIP</span>
          <nav className="ml-auto flex gap-1">
            {NAV.map(({ to, icon: Icon, label }) => (
              <Link key={to} to={to} aria-label={label} className={cx("rounded-lg p-2", (to === "/" ? path === "/" : path.startsWith(to)) ? "bg-raised text-ink" : "text-muted")}>
                <Icon size={17} />
              </Link>
            ))}
          </nav>
        </header>
        <main className="mx-auto w-full max-w-[1240px] px-4 py-6 md:px-8 md:py-8">
          <Banner />
          {children}
        </main>
      </div>
      <AskStack asks={asks} />
    </div>
  );
}
