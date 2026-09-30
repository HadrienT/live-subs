// AudioWorkletProcessor: resamples the captured stream and posts 100 ms Int16 frames.
// The main thread can send "reset" (discontinuity) through the port.
import { Downsampler } from "./downsampler";

declare const sampleRate: number;
declare function registerProcessor(name: string, ctor: unknown): void;
declare class AudioWorkletProcessor {
  readonly port: MessagePort;
}

class LiveSubsDownsampler extends AudioWorkletProcessor {
  private readonly ds: Downsampler;

  constructor() {
    super();
    this.ds = new Downsampler(sampleRate, (pcm) => this.port.postMessage(pcm.buffer, [pcm.buffer]));
    this.port.onmessage = (e: MessageEvent) => {
      if (e.data === "reset") this.ds.reset();
    };
  }

  process(inputs: Float32Array[][]): boolean {
    const input = inputs[0];
    if (input && input.length > 0) this.ds.push(input);
    return true;
  }
}

registerProcessor("live-subs-downsampler", LiveSubsDownsampler);
