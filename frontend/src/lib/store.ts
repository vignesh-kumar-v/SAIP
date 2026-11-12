// One live store fed by the server's event stream. `reduce` is pure so it can be tested on its own.
import { create } from "zustand";
import { get } from "./api";
import type { AppInfo, Ask, Decision, Episode, LogEvent, Snapshot, Tick } from "./types";

export const TICKS_KEPT = 720;
export const LOG_KEPT = 300;

export type Live = {
  link: "connecting" | "live" | "lost";
  demo: boolean;
  ticks: Tick[];
  log: LogEvent[];
  decisions: Decision[];
  episodes: Episode[]; // latest view of each stretch, oldest first
  asks: Ask[];
  quietUntil: number;
  inspectorCount: number;
  app: AppInfo | null;
};

export const empty: Live = {
  link: "connecting",
  demo: false,
  ticks: [],
  log: [],
  decisions: [],
  episodes: [],
  asks: [],
  quietUntil: 0,
  inspectorCount: 0,
  app: null,
};

function upsertEpisode(list: Episode[], ep: Episode): Episode[] {
  const i = list.findIndex((e) => e.episode === ep.episode);
  if (i === -1) return [...list, ep].slice(-100);
  const next = list.slice();
  next[i] = ep;
  return next;
}

export function reduce(s: Live, kind: string, data: any): Live {
  switch (kind) {
    case "snapshot": {
      const snap = data as Snapshot;
      let episodes: Episode[] = [];
      for (const e of snap.log) if (e.kind === "episode") episodes = upsertEpisode(episodes, e as Episode);
      return {
        ...s,
        demo: !!snap.meta?.demo,
        ticks: snap.ticks.slice(-TICKS_KEPT),
        log: snap.log.slice(-LOG_KEPT),
        decisions: snap.log.filter((e): e is Decision => e.kind === "decision"),
        episodes,
        asks: snap.asks,
        quietUntil: snap.quiet_until || 0,
      };
    }
    case "tick": {
      const ticks = s.ticks.length >= TICKS_KEPT ? s.ticks.slice(1) : s.ticks.slice();
      ticks.push(data as Tick);
      return { ...s, ticks };
    }
    case "ask":
      return s.asks.some((a) => a.id === data.id) ? s : { ...s, asks: [...s.asks, data as Ask] };
    case "ask-closed":
      return { ...s, asks: s.asks.filter((a) => a.id !== data.ask), log: [...s.log, data].slice(-LOG_KEPT) };
    case "quiet":
      return { ...s, quietUntil: data.until || 0, log: [...s.log, data].slice(-LOG_KEPT) };
    case "app": {
      const { id: _i, kind: _k, at: _a, ...rest } = data;
      return { ...s, app: s.app ? ({ ...s.app, ...rest } as AppInfo) : s.app };
    }
    case "inspector":
      return { ...s, inspectorCount: data.count };
    case "decision":
      return { ...s, decisions: [...s.decisions, data].slice(-500), log: [...s.log, data].slice(-LOG_KEPT) };
    case "episode":
      return { ...s, episodes: upsertEpisode(s.episodes, data), log: [...s.log, data].slice(-LOG_KEPT) };
    default:
      return { ...s, log: [...s.log, data].slice(-LOG_KEPT) };
  }
}

type Store = Live & {
  loadApp: () => Promise<AppInfo | null>;
};

export const useLive = create<Store>((set) => ({
  ...empty,
  loadApp: async () => {
    try {
      const app = await get<AppInfo>("/api/app");
      set({ app });
      return app;
    } catch {
      return null;
    }
  },
}));

const KINDS = ["snapshot", "tick", "decision", "episode", "offer", "outcome", "help", "status", "quiet", "ask",
  "ask-closed", "app", "inspector"];

let source: EventSource | null = null;

export function connect(): () => void {
  if (source) return () => {};
  const es = new EventSource("/api/events");
  source = es;
  es.onopen = () => {
    useLive.setState({ link: "live" });
    useLive.getState().loadApp();
  };
  es.onerror = () => useLive.setState({ link: "lost" });
  for (const kind of KINDS) {
    es.addEventListener(kind, (e) => {
      const data = JSON.parse((e as MessageEvent).data);
      useLive.setState((s) => reduce(s, kind, data));
    });
  }
  return () => {
    es.close();
    source = null;
  };
}

// ---- readings shared by the pages and the shell ----
export type State = "flow" | "stuck" | "paused" | "waiting" | "offline";

export function stateOf(t: Tick | undefined, link: Live["link"]): State {
  if (link === "lost") return "offline";
  if (!t) return "waiting";
  if (t.paused) return "paused";
  return t.score >= t.trigger ? "stuck" : "flow";
}

export const STATE_WORDS: Record<State, string> = {
  flow: "In flow",
  stuck: "Possibly stuck",
  paused: "Paused",
  waiting: "Waiting for activity",
  offline: "Agent not running",
};

export const PAUSE_WORDS: Record<string, string> = {
  afk: "you are away",
  locked: "the screen is locked",
  meeting: "you are in a meeting",
  no_window: "no window is being reported",
  no_input: "no input is being reported",
  stale: "ActivityWatch has gone quiet",
};

/** Seconds spent stuck in each app, from the ticks in view. */
export function stuckByApp(ticks: Tick[], since: number): { app: string; seconds: number }[] {
  const out = new Map<string, number>();
  for (let i = 1; i < ticks.length; i++) {
    const t = ticks[i];
    if (t.time < since || t.paused || t.score < t.trigger || !t.app) continue;
    const dt = Math.min(Math.max(t.time - ticks[i - 1].time, 0), 15);
    out.set(t.app, (out.get(t.app) || 0) + dt);
  }
  return [...out.entries()].map(([app, seconds]) => ({ app, seconds })).sort((a, b) => b.seconds - a.seconds);
}
