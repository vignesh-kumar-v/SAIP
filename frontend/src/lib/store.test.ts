import { describe, expect, it } from "vitest";
import { empty, reduce, stateOf, stuckByApp, TICKS_KEPT } from "./store";
import type { Tick } from "./types";

const tick = (time: number, score: number, app = "Code", paused = false): Tick =>
  ({ id: time, kind: "tick", at: time, time, score, paused, reason: null, trigger: 0.2, app });

describe("the live store", () => {
  it("draws itself from a snapshot, then follows events", () => {
    let s = reduce(empty, "snapshot", {
      ticks: [tick(1, 0.1)],
      log: [{ id: 2, kind: "episode", at: 2, state: "open", episode: 1, start: 1, end: 2, peak: 0.3, app: "Code", apps: {} }],
      asks: [{ id: 3, kind: "ask", at: 3, title: "SAIP", message: "?", buttons: ["No", "Yes"], timeout_s: 45 }],
      quiet_until: 0,
      meta: { demo: true },
    });
    expect(s.demo).toBe(true);
    expect(s.episodes).toHaveLength(1);
    s = reduce(s, "episode", { id: 4, kind: "episode", at: 4, state: "closed", episode: 1, start: 1, end: 4, peak: 0.5, app: "Code", apps: {} });
    expect(s.episodes).toHaveLength(1);
    expect(s.episodes[0].state).toBe("closed");
    s = reduce(s, "ask-closed", { id: 5, kind: "ask-closed", at: 5, ask: 3, answer: "Yes" });
    expect(s.asks).toHaveLength(0);
  });

  it("keeps a bounded number of ticks", () => {
    let s = empty;
    for (let i = 0; i < TICKS_KEPT + 10; i++) s = reduce(s, "tick", tick(i, 0.1));
    expect(s.ticks).toHaveLength(TICKS_KEPT);
    expect(s.ticks[0].time).toBe(10);
  });

  it("reads a tick the way the tray does", () => {
    expect(stateOf(undefined, "live")).toBe("waiting");
    expect(stateOf(tick(1, 0.3), "live")).toBe("stuck");
    expect(stateOf(tick(1, 0.1), "live")).toBe("flow");
    expect(stateOf(tick(1, 0.3, "Code", true), "live")).toBe("paused");
    expect(stateOf(tick(1, 0.3), "lost")).toBe("offline");
  });

  it("adds up stuck time per app", () => {
    const ticks = [tick(0, 0.1), tick(5, 0.3, "Code"), tick(10, 0.3, "Chrome"), tick(15, 0.3, "Chrome"), tick(20, 0.1, "Code")];
    expect(stuckByApp(ticks, 0)).toEqual([{ app: "Chrome", seconds: 10 }, { app: "Code", seconds: 5 }]);
  });
});
