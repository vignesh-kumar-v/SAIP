// Shapes of what the local API (src/saip/server.py) sends.

export type FeatureItem = { value: number; weight: number; push: number };
export type Features = { bias: number; items: Record<string, FeatureItem> };

export type Tick = {
  id: number;
  kind: "tick";
  at: number;
  time: number;
  score: number;
  paused: boolean;
  reason: string | null;
  trigger: number;
  app?: string | null;
  features?: Features | null;
};

export type Decision = {
  id: number;
  kind: "decision";
  at: number;
  decision: "offer" | "deferred" | "quiet" | "recheck-quiet";
  score: number;
  p_need: number;
  p_accept: number;
  bar: number;
  resampled: boolean;
  app?: string | null;
};

export type Episode = {
  id: number;
  kind: "episode";
  at: number;
  state: "open" | "closed";
  episode: number;
  start: number;
  end: number;
  peak: number;
  app: string | null;
  apps: Record<string, number>;
};

export type Ask = {
  id: number;
  kind: "ask";
  at: number;
  title: string;
  message: string;
  buttons: string[];
  timeout_s: number;
};

export type LogEvent =
  | Decision
  | Episode
  | { id: number; kind: "offer"; at: number; message: string; task: string; generated: boolean; app?: string | null }
  | { id: number; kind: "outcome"; at: number; outcome: string; cooldown_mult: number }
  | { id: number; kind: "help"; at: number; text: string; session?: number; task?: string }
  | { id: number; kind: "status"; at: number; level: string; text: string }
  | { id: number; kind: "quiet"; at: number; until: number }
  | { id: number; kind: "ask-closed"; at: number; ask: number; answer: string | null }
  | { id: number; kind: "inspector"; at: number; count: number }
  | { id: number; kind: "app"; at: number; [k: string]: unknown };

export type Snapshot = {
  ticks: Tick[];
  log: LogEvent[];
  asks: Ask[];
  quiet_until: number;
  meta: { demo?: boolean };
};

export type RuntimeStatus = {
  running: boolean;
  mode: "llm" | "mock" | "score-only" | "stopped" | "demo";
  model: string | null;
  error: string | null;
  started_at: number | null;
  screen: boolean;
  titles: boolean;
  monitor: { enabled: boolean; running: boolean; presses?: number; error: string | null };
};

export type AppInfo = {
  app: "saip";
  version: string;
  platform: string;
  demo: boolean;
  port: number | null;
  ready: boolean;
  setup_done: boolean;
  model: string | null;
  config: string | null;
  runtime: RuntimeStatus | null;
  autostart: boolean;
  monitor_typed_content: boolean;
  quiet_until: number;
};

export type Check = { name: string; status: "ok" | "changed" | "skipped" | "needs-you" | "failed"; detail: string };

export type Settings = {
  path: string | null;
  llm: {
    base_url: string;
    model: string;
    api_key_set: boolean;
    timeout_s: number;
    samples: number;
    sample_temperature: number;
    lookback_s: number;
    include_titles: boolean;
    private_apps: string[];
    read_screen: boolean;
    screen_chars: number;
    reasoning_effort: string;
    chat_tools?: boolean;
    web_search?: boolean;
    search_provider?: string;
    search_api_key_set?: boolean;
  };
  gate: Record<string, number>;
  agent: {
    tick_s: number;
    history_min: number;
    assess_interval_s: number;
    cooldown_s: number;
    offer_timeout_s: number;
    pending_expiry_s: number;
    break_idle_s: number;
    repos: string[];
    watch_roots: string[];
    exclude: string[];
    rescan_s: number;
    new_repos: string;
    monitor_typed_content: boolean;
    typed_text_max_chars: number;
    notify_offers: boolean;
    keep_chats?: boolean;
  };
};

export type AppRow = {
  label: string;
  value: string;
  installed: boolean;
  seen: boolean;
  group: string;
  blocked: boolean;
  suggested: boolean;
};

export type AppsPayload = { apps: AppRow[]; blocked: string[]; groups: { id: string; label: string }[] };

export type OfferRow = {
  time: number;
  stuck_score: number;
  p_need: number;
  p_accept: number;
  bar: number;
  resampled: boolean;
  task: string;
  message: string;
  generated: boolean;
  outcome: string;
  answered_after_s: number | null;
  app?: string | null;
};

export type DecisionRow = {
  time: number;
  decision: string;
  score: number;
  p_need: number;
  p_accept: number;
  bar: number;
  resampled: boolean;
  app?: string | null;
};

export type InspectorEntry = DecisionRow & {
  context: string;
  typed: boolean;
  offer: { message: string; task: string; generated: boolean } | null;
  screen: { app: string | null; text: string } | null;
  feedback?: "up" | "down" | null;
};

export type RepoRow = { path: string; state: string; detail: string };

export const GATE_PRESETS = [
  {
    id: "quiet",
    name: "Quiet",
    caption: "Offers help less often",
    gate: { c0: 1.0, m0: 1.2, alpha: 1.1, beta: 0.69 },
  },
  {
    id: "balanced",
    name: "Balanced",
    caption: "The default",
    gate: { c0: 1.0, m0: 2.0, alpha: 1.1, beta: 0.69 },
  },
  {
    id: "eager",
    name: "Eager",
    caption: "Offers help sooner, and more often",
    gate: { c0: 1.0, m0: 4.5, alpha: 1.1, beta: 0.69 },
  },
] as const;

// ---- chat ----
export type ActionState = "pending" | "approved" | "declined" | "undone" | "failed";
export type ChatAction = {
  name: "write_file" | "copy_to_clipboard";
  title: string;
  path: string | null;
  content: string;
  diff: string | null;
  exists: boolean;
  warning?: string | null;
  after?: string[];
  state: ActionState;
  detail?: string;
  undoable: boolean;
};
export type ChatItem = {
  id: number;
  kind: "user" | "assistant" | "tool" | "action";
  at: number;
  text: string;
  tool?: { name: string; label: string; ok: boolean; flag?: string[] | null };
  action?: ChatAction;
  risky?: RiskyCommand[];
  claim?: string | null;
};
export type RiskyCommand = { command: string; why: string };
export type ChatMessage = { role: "user" | "assistant"; text: string; at: number };
export type ChatFull = {
  id: number;
  title: string;
  created: number;
  has_screen: boolean;
  tools?: string[];
  items?: ChatItem[];
  messages: ChatMessage[];
  context?: string;
  screen?: string;
};
export type ChatView = ChatFull;
export type RewindPreview = { item: number; drops: number; undo: { path: string; effect: "restore" | "delete" }[]; cannot_undo: string[] };
export type RewindResult = { chat: ChatView; undone: string[]; skipped: string[] };
