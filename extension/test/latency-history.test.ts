import { describe, expect, test } from "vitest";
import { LatencyTracker, LatencyWindow, percentile } from "../src/content/latency";
import { Transcript, toSrt, toVtt } from "../src/content/history";

describe("latency", () => {
  test("percentile interpolates like numpy", () => {
    expect(percentile([1, 2, 3, 4], 0.5)).toBe(2.5);
    expect(percentile([10], 0.95)).toBe(10);
    expect(percentile([], 0.5)).toBeNull();
  });

  test("window drops old samples and aberrations", () => {
    const w = new LatencyWindow(1000);
    w.add(100, 0);
    w.add(300, 900);
    w.add(-5, 900); // seek artefact
    expect(w.summary(950)).toEqual({ p50: 200, p95: 290, n: 2 });
    expect(w.summary(1500).n).toBe(1);
  });

  test("tracker counts each segment once per language", () => {
    const t = new LatencyTracker();
    t.onFinal(1, 10.0, 11.2, 0);
    t.onFinal(1, 10.0, 12.0, 0); // re-render of the same final
    t.onTranslation(1, 10.0, 12.1, 0);
    const s = t.summary(1);
    expect(s.ja.n).toBe(1);
    expect(s.ja.p50).toBeCloseTo(1200);
    expect(s.en.p50).toBeCloseTo(2100);
  });
});

describe("transcript & export", () => {
  function sample(): Transcript {
    const t = new Transcript({ videoId: "v", title: null, startedAt: 0, entries: [] });
    t.onFinal(1, "こんにちは", 3661.5, 3663.0);
    t.onTranslation(1, "Hello");
    t.onFinal(2, "ありがとう", 3663.4, 3665.0);
    t.onFinal(3, "消えた", 3670, 3671);
    t.onFinal(3, "", 3670, 3671); // retracted
    return t;
  }

  test("entries, search", () => {
    const t = sample();
    expect(t.entries.map((e) => e.segId)).toEqual([1, 2]);
    expect(t.search("hello").map((e) => e.segId)).toEqual([1]);
    expect(t.search("ありがと").map((e) => e.segId)).toEqual([2]);
  });

  test("SRT, bilingual, cues do not overlap", () => {
    expect(toSrt(sample().entries, "both")).toBe(
      "1\n01:01:01,500 --> 01:01:03,400\nこんにちは\nHello\n\n2\n01:01:03,400 --> 01:01:06,000\nありがとう\n",
    );
  });

  test("VTT English only skips untranslated lines", () => {
    expect(toVtt(sample().entries, "en")).toBe("WEBVTT\n\n01:01:01.500 --> 01:01:04.000\nHello\n");
  });
});
