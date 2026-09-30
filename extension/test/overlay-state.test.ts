import { describe, expect, test } from "vitest";
import { MODE_CODE_BANNER, TIMING, initialState, reduce, view, type OverlayState } from "../src/content/overlay-state";
import type { ServerMessage } from "../src/protocol";

function apply(msgs: ServerMessage[], start: OverlayState = initialState, now = 1000): OverlayState {
  return msgs.reduce((s, msg, i) => reduce(s, { type: "server", msg, now: now + i }), start);
}

const partial = (seg: number, stable: string, unstable: string, t0 = seg * 10): ServerMessage => ({
  type: "partial", seg_id: seg, ja_stable: stable, ja_unstable: unstable, t0, t1: t0 + 1,
});
const final = (seg: number, ja: string, t0 = seg * 10): ServerMessage => ({
  type: "final", seg_id: seg, ja, t0, t1: t0 + 2, asr_ms: 100,
});

describe("overlay reducer", () => {
  test("partials are replaced by the final", () => {
    const s = apply([partial(1, "きょう", "は"), partial(1, "きょうは", "いい"), final(1, "今日はいい天気")]);
    expect(s.lines).toHaveLength(1);
    expect(s.lines[0]).toMatchObject({ jaStable: "今日はいい天気", jaUnstable: "", final: true });
  });

  test("a late partial never overwrites the final", () => {
    const s = apply([final(1, "確定"), partial(1, "古い", "")]);
    expect(s.lines[0]?.jaStable).toBe("確定");
  });

  test("an empty final retracts the partials (hallucination dropped)", () => {
    const s = apply([partial(1, "ごめん", ""), final(1, "")]);
    expect(s.lines).toHaveLength(0);
  });

  test("translation deltas accumulate, then the translation closes the line", () => {
    const s = apply([
      final(1, "こんにちは"),
      { type: "translation_delta", seg_id: 1, en_delta: "Hel" },
      { type: "translation_delta", seg_id: 1, en_delta: "lo" },
    ]);
    expect(s.lines[0]).toMatchObject({ en: "Hello", enDone: false });
    const done = apply([{ type: "translation", seg_id: 1, en: "Hello!", mt_ms: 300, merged: [] }], s);
    expect(done.lines[0]).toMatchObject({ en: "Hello!", enDone: true });
  });

  test("merged segments are closed without their own English", () => {
    const s = apply([
      final(1, "いち"),
      final(2, "に"),
      { type: "translation", seg_id: 2, en: "one two", mt_ms: 900, merged: [1] },
    ]);
    expect(s.lines.map((l) => [l.segId, l.en, l.enDone])).toEqual([[1, "", true], [2, "one two", true]]);
  });

  test("model inactive shows the banner, a translation clears it", () => {
    const s = apply([final(1, "はい"), { type: "error", code: "mt_model_inactive", message: "x", fatal: false, seg_id: 1 }]);
    expect(s.banner).toBe(MODE_CODE_BANNER);
    expect(s.lines[0]).toMatchObject({ enDone: true, enFailed: true });
    expect(apply([final(2, "え"), { type: "translation", seg_id: 2, en: "Eh", mt_ms: 1 }], s).banner).toBeNull();
  });

  test("timeout leaves the English line empty", () => {
    const s = apply([final(3, "はい"), { type: "error", code: "mt_timeout", message: "x", fatal: false, seg_id: 3 }]);
    expect(s.lines[0]).toMatchObject({ en: "", enDone: true, enFailed: true });
  });

  test("keeps a bounded number of lines", () => {
    const s = apply(Array.from({ length: 50 }, (_, i) => final(i + 1, `s${i}`)));
    expect(s.lines).toHaveLength(40);
    expect(s.lines[0]?.segId).toBe(11);
  });
});

describe("view: anime-style timing", () => {
  // t0 = seg * 10, t1 = t0 + 2 (see final()); translation done as it arrives
  const done = (seg: number, en: string): ServerMessage => ({ type: "translation", seg_id: seg, en, mt_ms: 1 });
  const at = (msgs: ServerMessage[], mediaTime: number, start = initialState) =>
    msgs.reduce((s, msg) => reduce(s, { type: "server", msg, now: 0, mediaTime }), start);

  test("appears when the speech starts, not before (ahead mode)", () => {
    const s = at([final(1, "一", 100), done(1, "One")], 90);
    expect(view(s, 99).current).toBeNull();
    expect(view(s, 100).current?.segId).toBe(1);
  });

  test("stays after the speech ends, then leaves", () => {
    const s = at([final(1, "こんにちは"), done(1, "Hello")], 5); // t0 10, t1 12
    expect(view(s, 12 + TIMING.lingerS - 0.05).current?.segId).toBe(1);
    expect(view(s, 12 + TIMING.lingerS + 0.05).current).toBeNull();
  });

  test("a long translation keeps its reading time", () => {
    const en = "x".repeat(90); // 6 s at 15 characters per second
    const s = at([final(1, "はい"), done(1, en)], 5);
    expect(view(s, 10 + 5.9).current?.segId).toBe(1);
    expect(view(s, 10 + 6.1).current).toBeNull();
  });

  test("reading time starts when the text arrives (capture mode, late text)", () => {
    const s = at([final(1, "はい"), done(1, "Yes")], 13); // speech 10–12, text at 13
    expect(view(s, 13 + TIMING.minS - 0.05).current?.segId).toBe(1);
  });

  test("never cut off while the English is still streaming", () => {
    const s = at([final(1, "はい"), { type: "translation_delta", seg_id: 1, en_delta: "Ye" }], 12);
    expect(view(s, 60).current?.segId).toBe(1);
  });

  test("a short gap is bridged instead of blinking", () => {
    // seg 1: 10–12, gone at 13; seg 2 starts at 14 (gap 1 s < bridge)
    const s = at([final(1, "一"), done(1, "One"), final(2, "二", 14), done(2, "Two")], 5);
    expect(view(s, 13.5).current?.segId).toBe(1);
    expect(view(s, 14).current?.segId).toBe(2);
  });

  test("a long gap is left empty", () => {
    const s = at([final(1, "一"), done(1, "One"), final(2, "二", 20), done(2, "Two")], 5);
    expect(view(s, 16).current).toBeNull();
  });

  test("the next line starting early is stacked, not replacing", () => {
    const en = "x".repeat(60); // 4 s of reading
    const s = at([final(1, "一"), done(1, en), final(2, "二", 11), done(2, "Two")], 5);
    const v = view(s, 11.5);
    expect(v.current?.segId).toBe(2);
    expect(v.previous?.segId).toBe(1);
    expect(v.overlap).toBe(true);
    expect(view(s, 14.5).overlap).toBe(false); // seg 1 read by then
  });

  test("seek back forgets the future (no ghost subtitles)", () => {
    const s = apply([final(1, "一"), final(2, "二"), final(3, "三")], initialState, 0); // t0 10, 20, 30
    const after = reduce(s, { type: "seek", mediaTime: 12 });
    expect(after.lines.map((l) => l.segId)).toEqual([1]);
  });
});

describe("dragging the subtitles", () => {
  const player = { left: 100, bottom: 800, width: 1000, height: 600 };

  test("keeps the grabbed point under the pointer", async () => {
    const { positionFromPointer } = await import("../src/content/overlay");
    // pointer at 60 % across, 25 % up; grabbed 20 px right of centre, 10 px above the bottom
    expect(positionFromPointer(player, 720, 640, { dx: 20, dy: 10 })).toEqual({ centerPct: 60, bottomPct: 25 });
  });

  test("stays inside the player", async () => {
    const { positionFromPointer } = await import("../src/content/overlay");
    expect(positionFromPointer(player, -500, 5000, { dx: 0, dy: 0 })).toEqual({ centerPct: 10, bottomPct: 0 });
    expect(positionFromPointer(player, 5000, -500, { dx: 0, dy: 0 })).toEqual({ centerPct: 90, bottomPct: 90 });
  });
});
