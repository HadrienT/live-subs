import { describe, expect, test } from "vitest";
import { Downsampler, FRAME_SAMPLES, OUT_RATE, designLowPass, toInt16 } from "../src/worklet/downsampler";

function sine(freq: number, rate: number, seconds: number, amp = 0.5): Float32Array {
  const n = Math.round(rate * seconds);
  return Float32Array.from({ length: n }, (_, i) => amp * Math.sin((2 * Math.PI * freq * i) / rate));
}

function run(rate: number, input: Float32Array, block = 128): Int16Array {
  const frames: Int16Array[] = [];
  const ds = new Downsampler(rate, (f) => frames.push(f));
  for (let i = 0; i < input.length; i += block) ds.push([input.subarray(i, i + block)]);
  const out = new Int16Array(frames.length * FRAME_SAMPLES);
  frames.forEach((f, k) => out.set(f, k * FRAME_SAMPLES));
  return out;
}

function rms(x: Int16Array, skip = 400): number {
  let s = 0;
  for (let i = skip; i < x.length; i++) s += ((x[i] ?? 0) / 32768) ** 2;
  return Math.sqrt(s / (x.length - skip));
}

describe("downsampler", () => {
  test("filter has unity DC gain", () => {
    const h = designLowPass(48_000, 7_200, 31);
    expect(h.reduce((a, b) => a + b, 0)).toBeCloseTo(1, 6);
  });

  test.each([48_000, 44_100])("keeps the rate: 2 s at %i Hz → 32 000 samples", (rate) => {
    const out = run(rate, sine(440, rate, 2.0));
    expect(out.length).toBe(Math.floor((2 * OUT_RATE) / FRAME_SAMPLES) * FRAME_SAMPLES);
  });

  test.each([48_000, 44_100])("passes speech band at %i Hz", (rate) => {
    const out = run(rate, sine(1000, rate, 1.0, 0.5));
    expect(rms(out)).toBeCloseTo(0.5 / Math.SQRT2, 1);
  });

  test.each([48_000, 44_100])("attenuates above 8 kHz (no audible aliasing) at %i Hz", (rate) => {
    // 12 kHz would fold to 4 kHz without the low-pass filter.
    const out = run(rate, sine(12_000, rate, 1.0, 0.5));
    const atten = 20 * Math.log10(rms(out) / (0.5 / Math.SQRT2));
    expect(atten).toBeLessThan(-40);
  });

  test("downmixes stereo", () => {
    const frames: Int16Array[] = [];
    const ds = new Downsampler(48_000, (f) => frames.push(f));
    const l = sine(500, 48_000, 0.2, 0.4);
    const r = l.map((v) => -v); // opposite phase cancels
    ds.push([l, r]);
    expect(frames.length).toBe(2);
    expect(Math.max(...(frames[1] ?? []).map(Math.abs))).toBe(0);
  });

  test("saturates instead of wrapping", () => {
    expect(toInt16(2)).toBe(32767);
    expect(toInt16(-2)).toBe(-32768);
  });

  test("reset drops the partial frame", () => {
    const frames: Int16Array[] = [];
    const ds = new Downsampler(48_000, (f) => frames.push(f));
    ds.push([new Float32Array(3000)]); // ~1000 output samples, no frame yet
    ds.reset();
    ds.push([new Float32Array(4800)]); // exactly 1 600 output samples
    expect(frames.length).toBe(1);
  });
});
