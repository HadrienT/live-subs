// Spike resampler: windowed-sinc low-pass (31 taps, 7.2 kHz) then fractional
// decimation to 16 kHz. Posts 100 ms Int16 frames (1 600 samples).
const TAPS = 31;
const OUT_RATE = 16000;
const FRAME = 1600;

function designLowPass(fs, cutoff, taps) {
  const h = new Float32Array(taps);
  const m = (taps - 1) / 2;
  let sum = 0;
  for (let i = 0; i < taps; i++) {
    const x = i - m;
    const sinc = x === 0 ? 2 * cutoff / fs : Math.sin(2 * Math.PI * cutoff * x / fs) / (Math.PI * x);
    const w = 0.42 - 0.5 * Math.cos(2 * Math.PI * i / (taps - 1)) + 0.08 * Math.cos(4 * Math.PI * i / (taps - 1));
    h[i] = sinc * w;
    sum += h[i];
  }
  for (let i = 0; i < taps; i++) h[i] /= sum;
  return h;
}

class SpikeDownsampler extends AudioWorkletProcessor {
  constructor() {
    super();
    this.h = designLowPass(sampleRate, 7200, TAPS);
    this.hist = new Float32Array(TAPS);
    this.histPos = 0;
    this.step = sampleRate / OUT_RATE;
    this.phase = 0;
    this.prev = 0;
    this.out = new Int16Array(FRAME);
    this.outLen = 0;
  }
  filtered(x) {
    this.hist[this.histPos] = x;
    this.histPos = (this.histPos + 1) % TAPS;
    let acc = 0;
    for (let i = 0; i < TAPS; i++) acc += this.h[i] * this.hist[(this.histPos + i) % TAPS];
    return acc;
  }
  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const n = input[0].length;
    for (let i = 0; i < n; i++) {
      let x = 0;
      for (let c = 0; c < input.length; c++) x += input[c][i];
      const y = this.filtered(x / input.length);
      // linear interpolation between prev (t=-1) and y (t=0) at fractional phase
      while (this.phase <= 1) {
        const v = this.prev + (y - this.prev) * this.phase;
        this.out[this.outLen++] = Math.max(-32768, Math.min(32767, Math.round(v * 32767)));
        if (this.outLen === FRAME) {
          this.port.postMessage(this.out.buffer, [this.out.buffer]);
          this.out = new Int16Array(FRAME);
          this.outLen = 0;
        }
        this.phase += this.step;
      }
      this.phase -= 1;
      this.prev = y;
    }
    return true;
  }
}
registerProcessor("spike-downsampler", SpikeDownsampler);
