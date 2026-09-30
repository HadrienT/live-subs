// Rolling end-to-end latency, measured in the video's own time (WP09 §2):
// latency = media time when the subtitle is displayed − t1 (end of speech).
import type { LatencySummary } from "../shared/messages";

export const WINDOW_MS = 5 * 60 * 1000;

export class LatencyWindow {
  private samples: { at: number; ms: number }[] = [];

  constructor(private readonly windowMs = WINDOW_MS) {}

  add(ms: number, now: number): void {
    if (!Number.isFinite(ms) || ms < 0 || ms > 60_000) return; // seek or clock jump
    this.samples.push({ at: now, ms });
    this.prune(now);
  }

  private prune(now: number): void {
    const cutoff = now - this.windowMs;
    let i = 0;
    while (i < this.samples.length && (this.samples[i]?.at ?? 0) < cutoff) i++;
    if (i) this.samples.splice(0, i);
  }

  summary(now: number): LatencySummary {
    this.prune(now);
    const values = this.samples.map((s) => s.ms).sort((a, b) => a - b);
    return { p50: percentile(values, 0.5), p95: percentile(values, 0.95), n: values.length };
  }
}

/** Linear interpolation between closest ranks, like numpy's default. */
export function percentile(sorted: number[], q: number): number | null {
  if (sorted.length === 0) return null;
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  const a = sorted[lo] ?? 0;
  const b = sorted[hi] ?? 0;
  return a + (b - a) * (pos - lo);
}

/**
 * Tracks when each segment first became visible, JA and EN separately, and
 * turns it into latency samples. Only counted while the video plays at 1×.
 */
export class LatencyTracker {
  readonly ja = new LatencyWindow();
  readonly en = new LatencyWindow();
  private seenJa = new Set<number>();
  private seenEn = new Set<number>();

  onFinal(segId: number, t1: number, mediaTime: number, now: number): void {
    if (this.seenJa.has(segId)) return;
    this.seenJa.add(segId);
    this.ja.add((mediaTime - t1) * 1000, now);
  }

  onTranslation(segId: number, t1: number, mediaTime: number, now: number): void {
    if (this.seenEn.has(segId)) return;
    this.seenEn.add(segId);
    this.en.add((mediaTime - t1) * 1000, now);
  }

  summary(now: number): { ja: LatencySummary; en: LatencySummary } {
    return { ja: this.ja.summary(now), en: this.en.summary(now) };
  }
}
