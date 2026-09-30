// Mono downmix → low-pass FIR → fractional resampling to 16 kHz → int16 frames.
//
// Pure (no Web Audio types): runs inside the AudioWorkletProcessor, inside the
// ScriptProcessorNode fallback, and in vitest.

export const OUT_RATE = 16_000;
export const FRAME_SAMPLES = 1_600; // 100 ms, protocol.FRAME_SAMPLES
export const TAPS = 31;
export const CUTOFF_HZ = 7_200;

/** Blackman-windowed sinc low-pass, unity DC gain. */
export function designLowPass(fs: number, cutoff: number, taps: number): Float32Array {
  const h = new Float32Array(taps);
  const m = (taps - 1) / 2;
  let sum = 0;
  for (let i = 0; i < taps; i++) {
    const x = i - m;
    const sinc = x === 0 ? (2 * cutoff) / fs : Math.sin((2 * Math.PI * cutoff * x) / fs) / (Math.PI * x);
    const w =
      0.42 - 0.5 * Math.cos((2 * Math.PI * i) / (taps - 1)) + 0.08 * Math.cos((4 * Math.PI * i) / (taps - 1));
    h[i] = sinc * w;
    sum += h[i] ?? 0;
  }
  for (let i = 0; i < taps; i++) h[i] = (h[i] ?? 0) / sum;
  return h;
}

export function toInt16(x: number): number {
  const v = Math.round(x * 32767);
  return v > 32767 ? 32767 : v < -32768 ? -32768 : v;
}

export class Downsampler {
  private readonly h: Float32Array;
  private readonly hist: Float32Array;
  private histPos = 0;
  private readonly step: number;
  private phase = 0; // position of the next output sample, in input samples, relative to `prev`
  private prev = 0;
  private out = new Int16Array(FRAME_SAMPLES);
  private outLen = 0;

  constructor(
    readonly inputRate: number,
    private readonly onFrame: (pcm: Int16Array) => void,
  ) {
    // Below 16 kHz input, no filtering is needed (and the cutoff would be above Nyquist).
    this.h = inputRate > OUT_RATE ? designLowPass(inputRate, CUTOFF_HZ, TAPS) : Float32Array.of(1);
    this.hist = new Float32Array(this.h.length);
    this.step = inputRate / OUT_RATE;
  }

  /** Feed planar channels of one render quantum (or any block). */
  push(channels: ArrayLike<number>[]): void {
    const nch = channels.length;
    if (nch === 0) return;
    const n = channels[0]?.length ?? 0;
    const taps = this.h.length;
    for (let i = 0; i < n; i++) {
      let x = 0;
      for (let c = 0; c < nch; c++) x += channels[c]?.[i] ?? 0;
      x /= nch;
      this.hist[this.histPos] = x;
      this.histPos = (this.histPos + 1) % taps;
      let y = 0;
      for (let k = 0; k < taps; k++) y += (this.h[k] ?? 0) * (this.hist[(this.histPos + k) % taps] ?? 0);
      // Output samples falling in (prev, y]: linear interpolation.
      while (this.phase <= 1) {
        this.emit(this.prev + (y - this.prev) * this.phase);
        this.phase += this.step;
      }
      this.phase -= 1;
      this.prev = y;
    }
  }

  private emit(v: number): void {
    this.out[this.outLen++] = toInt16(v);
    if (this.outLen === FRAME_SAMPLES) {
      const frame = this.out;
      this.out = new Int16Array(FRAME_SAMPLES);
      this.outLen = 0;
      this.onFrame(frame);
    }
  }

  /** Drop the partial frame (on discontinuity: the next frame starts clean). */
  reset(): void {
    this.outLen = 0;
    this.phase = 0;
    this.prev = 0;
    this.hist.fill(0);
  }
}
