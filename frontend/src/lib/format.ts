export const two = (x: number | null | undefined) => (x == null || Number.isNaN(x) ? "–" : Number(x).toFixed(2));

export const clock = (s: number) => new Date(s * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

export const clockS = (s: number) =>
  new Date(s * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export const dateTime = (s: number) =>
  new Date(s * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

export function ago(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 10) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  return `${Math.floor(s / 3600)} h ago`;
}

export const FEATURE_WORDS: Record<string, { name: string; hint: string }> = {
  circling: { name: "Circling", hint: "returning again and again to the same few windows" },
  revisits: { name: "Revisits", hint: "coming back to windows seen in the last five minutes" },
  toggling: { name: "Switching", hint: "app switches per minute" },
  stalling: { name: "Stalling", hint: "on a work window with no keys or clicks" },
  reaching: { name: "Reaching", hint: "opening new windows rather than old ones" },
  typing: { name: "Typing", hint: "share of the last minute with key presses" },
  ai_share: { name: "AI tools", hint: "time in an AI chat or agent" },
  failures: { name: "Failures", hint: "terminal commands that failed" },
  repeats: { name: "Repeats", hint: "the same failing command again" },
  progress: { name: "Progress", hint: "recent commits" },
};
