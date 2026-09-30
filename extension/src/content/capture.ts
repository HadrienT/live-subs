// Audio capture of YouTube's <video> (ADR-001): source → resampler → 100 ms int16 frames.
//
// Every step has the plan B of the WP02 spike behind a setting ("auto" tries in order):
//   source:  mozCaptureStream/captureStream (the element keeps playing; optional
//            re-route if a Firefox mutes captured elements) | createMediaElementSource
//   worklet: extension URL | blob URL | ScriptProcessorNode
import { FRAME_SAMPLES, SAMPLE_RATE, encodeFrame } from "../protocol";
import type { CaptureMethod, WorkletMode } from "../shared/settings";
import { Downsampler } from "../worklet/downsampler";

export interface CaptureOptions {
  method: CaptureMethod;
  worklet: WorkletMode;
  // Play the captured stream through Web Audio too. Only for a Firefox that mutes
  // an element once captured: otherwise the sound is heard twice (WP02 Q2).
  reroute: boolean;
  onFrame: (buf: ArrayBuffer) => void;
  log?: (msg: string) => void;
}

type Resampler = { node: AudioNode; reset: () => void };

// createMediaElementSource() works once per element: keep it for the element's lifetime.
const elementSources = new WeakMap<HTMLVideoElement, { ctx: AudioContext; node: MediaElementAudioSourceNode }>();

/**
 * Speakers path whose gain follows the player's volume and mute button: audio
 * played through Web Audio would otherwise ignore YouTube's controls.
 */
function playbackGain(ctx: AudioContext, video: HTMLVideoElement): { node: GainNode; release: () => void } {
  const node = ctx.createGain();
  const sync = () => node.gain.setTargetAtTime(video.muted ? 0 : video.volume, ctx.currentTime, 0.015);
  node.gain.value = video.muted ? 0 : video.volume;
  video.addEventListener("volumechange", sync);
  node.connect(ctx.destination);
  return { node, release: () => video.removeEventListener("volumechange", sync) };
}

export class Capture {
  private ctx: AudioContext | null = null;
  private source: AudioNode | null = null;
  private resampler: Resampler | null = null;
  private stream: MediaStream | null = null;
  private reroute: { node: GainNode; release: () => void } | null = null;
  private sampleIdx = 0;
  private pendingDiscontinuity = false;
  private sending = true;
  private ownsContext = true;
  readonly how: string[] = [];

  constructor(
    readonly video: HTMLVideoElement,
    private readonly opts: CaptureOptions,
  ) {}

  /** sample_idx keeps growing for the whole session, across discontinuities. */
  get nextSampleIdx(): number {
    return this.sampleIdx;
  }

  async start(firstSampleIdx = 0): Promise<void> {
    this.sampleIdx = firstSampleIdx;
    const cached = elementSources.get(this.video);
    const ctx = cached?.ctx ?? new AudioContext();
    this.ctx = ctx;
    this.ownsContext = !cached;
    if (ctx.state !== "running") await ctx.resume().catch(() => undefined);
    this.source = this.makeSource(ctx);
    this.resampler = await this.makeResampler(ctx);
    this.source.connect(this.resampler.node);
    this.log(`capture: ${this.how.join(", ")}, ${ctx.sampleRate} Hz`);
  }

  private log(msg: string): void {
    this.opts.log?.(msg);
  }

  private makeSource(ctx: AudioContext): AudioNode {
    const cached = elementSources.get(this.video);
    if (cached) {
      this.how.push("mediaElementSource (reused)");
      return cached.node;
    }
    const v = this.video as HTMLVideoElement & {
      mozCaptureStream?: () => MediaStream;
      captureStream?: () => MediaStream;
    };
    if (this.opts.method !== "mediaElementSource") {
      const capture = v.captureStream ?? v.mozCaptureStream;
      if (typeof capture === "function") {
        try {
          const stream = capture.call(v);
          this.stream = stream;
          const node = ctx.createMediaStreamSource(stream);
          this.how.push(v.captureStream ? "captureStream" : "mozCaptureStream");
          if (this.opts.reroute) {
            this.reroute = playbackGain(ctx, this.video);
            node.connect(this.reroute.node);
            this.how.push("rerouted");
          }
          return node;
        } catch (e) {
          this.log(`captureStream failed: ${String(e)}`);
          if (this.opts.method === "captureStream") throw e;
        }
      }
    }
    const node = ctx.createMediaElementSource(this.video);
    // The element now only plays through the graph, for its whole life.
    node.connect(playbackGain(ctx, this.video).node);
    elementSources.set(this.video, { ctx, node });
    this.ownsContext = false;
    this.how.push("mediaElementSource");
    return node;
  }

  private async makeResampler(ctx: AudioContext): Promise<Resampler> {
    const mode = this.opts.worklet;
    const url = browser.runtime.getURL("worklet.js");
    const attempt = async (moduleUrl: string, label: string): Promise<Resampler | null> => {
      try {
        await ctx.audioWorklet.addModule(moduleUrl);
        const node = new AudioWorkletNode(ctx, "live-subs-downsampler");
        node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => this.onPcm(new Int16Array(e.data));
        // Only nodes pulled by the destination are sure to run: connect through silence.
        node.connect(this.silentSink(ctx));
        this.how.push(`AudioWorklet (${label})`);
        return { node, reset: () => node.port.postMessage("reset") };
      } catch (e) {
        this.log(`AudioWorklet via ${label} failed: ${String(e)}`);
        return null;
      }
    };
    if (mode === "auto" || mode === "extension") {
      const r = await attempt(url, "extension URL");
      if (r) return r;
    }
    if (mode === "auto" || mode === "blob") {
      const src = await (await fetch(url)).text();
      const blobUrl = URL.createObjectURL(new Blob([src], { type: "text/javascript" }));
      const r = await attempt(blobUrl, "blob URL");
      if (r) return r;
    }
    // Deprecated but always there. Same DSP, on the main thread.
    const ds = new Downsampler(ctx.sampleRate, (pcm) => this.onPcm(pcm));
    const sp = ctx.createScriptProcessor(4096, 2, 1);
    sp.onaudioprocess = (e: AudioProcessingEvent) => {
      const b = e.inputBuffer;
      const chans: Float32Array[] = [];
      for (let c = 0; c < b.numberOfChannels; c++) chans.push(b.getChannelData(c));
      ds.push(chans);
    };
    sp.connect(this.silentSink(ctx)); // a ScriptProcessor only runs when pulled
    this.how.push("ScriptProcessorNode");
    return { node: sp, reset: () => ds.reset() };
  }

  private silentSink(ctx: AudioContext): AudioNode {
    const mute = ctx.createGain();
    mute.gain.value = 0;
    mute.connect(ctx.destination);
    return mute;
  }

  private onPcm(pcm: Int16Array): void {
    const idx = this.sampleIdx;
    this.sampleIdx += pcm.length;
    if (!this.sending) return;
    // The frame ends "now": its first sample is ~100 ms earlier in the video.
    const mediaTime = Math.max(0, this.video.currentTime - FRAME_SAMPLES / SAMPLE_RATE);
    const discontinuity = this.pendingDiscontinuity;
    this.pendingDiscontinuity = false;
    this.opts.onFrame(encodeFrame({ sampleIdx: idx, mediaTime, pcm, discontinuity }));
  }

  /** Next frame carries the discontinuity flag, and starts from a clean resampler. */
  markDiscontinuity(): void {
    this.pendingDiscontinuity = true;
    this.resampler?.reset();
  }

  /** Paused (ad, player paused): frames are produced but not sent. */
  setSending(on: boolean): void {
    if (on && !this.sending) this.markDiscontinuity();
    this.sending = on;
  }

  async stop(): Promise<void> {
    // Only the branch to the resampler: the speakers path (if any) stays as it is.
    if (this.source && this.resampler) this.source.disconnect(this.resampler.node);
    this.resampler?.node.disconnect();
    this.reroute?.release();
    this.reroute?.node.disconnect();
    this.reroute = null;
    // Ending the capture gives the element its own audio output back.
    for (const track of this.stream?.getTracks() ?? []) track.stop();
    this.stream = null;
    if (this.ownsContext) await this.ctx?.close().catch(() => undefined);
    this.ctx = null;
    this.source = null;
    this.resampler = null;
  }
}
