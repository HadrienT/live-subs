// Pure state of the subtitle overlay. The DOM layer (overlay.ts) only renders `view()`.
import type { ServerMessage } from "../protocol";

export interface Line {
  segId: number;
  t0: number; // media time (s)
  t1: number;
  jaStable: string;
  jaUnstable: string;
  final: boolean;
  en: string;
  enDone: boolean;
  enFailed: boolean;
  updatedAt: number; // ms, performance.now()
  arrivedAt: number; // video time when the Japanese text arrived
  enAt: number | null; // video time when the translation was complete
}

export interface OverlayState {
  lines: Line[]; // ascending segId, a few at most
  banner: string | null;
  lastActivity: number;
}

export type Action =
  // mediaTime: where the video was when the message arrived (reading-time start)
  | { type: "server"; msg: ServerMessage; now: number; mediaTime?: number }
  | { type: "seek"; mediaTime: number }
  | { type: "reset" };

// A line disappears 6 s of video after its speech ended (t1), without a newer one.
// Subtitling rules (anime fansub / Netflix style), in seconds of video.
export const TIMING = {
  enCps: 15, // English reading speed, characters per second
  jaCps: 6, // Japanese reading speed
  minS: 1.2, // shortest time a line stays readable
  maxS: 7, // longest reading time granted to one line
  lingerS: 1.0, // stays this long after the speech ends
  bridgeS: 1.5, // a shorter gap before the next line is not blanked out
};
// Ahead mode (WP13) receives sentences before the player reaches them: keep enough.
export const KEEP_LINES = 40;
export const MODE_CODE_BANNER = "LLM en mode code — passer en mode traduction";

export const initialState: OverlayState = { lines: [], banner: null, lastActivity: 0 };

function upsert(lines: Line[], segId: number, make: () => Line, patch: (l: Line) => Line): Line[] {
  const i = lines.findIndex((l) => l.segId === segId);
  if (i === -1) {
    const next = [...lines, make()].sort((a, b) => a.segId - b.segId);
    return next.slice(-KEEP_LINES);
  }
  const copy = [...lines];
  copy[i] = patch(copy[i] as Line);
  return copy;
}

function blank(segId: number, t0: number, t1: number, now: number, mediaTime: number): Line {
  return {
    segId, t0, t1, jaStable: "", jaUnstable: "", final: false, en: "", enDone: false, enFailed: false,
    updatedAt: now, arrivedAt: mediaTime, enAt: null,
  };
}

export function reduce(state: OverlayState, action: Action): OverlayState {
  switch (action.type) {
    case "reset":
      return initialState;
    case "seek":
      // Seek back: forget what belongs to a future we have not heard again yet.
      return { ...state, lines: state.lines.filter((l) => l.t0 <= action.mediaTime) };
    case "server":
      return onServer(state, action.msg, action.now, action.mediaTime ?? -Infinity);
  }
}

function onServer(state: OverlayState, msg: ServerMessage, now: number, mt: number): OverlayState {
  switch (msg.type) {
    case "partial": {
      const existing = state.lines.find((l) => l.segId === msg.seg_id);
      if (existing?.final) return state; // a final replaces every partial, late ones included
      const lines = upsert(
        state.lines,
        msg.seg_id,
        () => ({ ...blank(msg.seg_id, msg.t0, msg.t1, now, mt), jaStable: msg.ja_stable, jaUnstable: msg.ja_unstable }),
        (l) => ({ ...l, jaStable: msg.ja_stable, jaUnstable: msg.ja_unstable, t1: msg.t1, updatedAt: now }),
      );
      return { ...state, lines, lastActivity: now };
    }
    case "final": {
      if (!msg.ja) {
        // Dropped by the server (hallucination): retract the partials.
        return { ...state, lines: state.lines.filter((l) => l.segId !== msg.seg_id) };
      }
      const lines = upsert(
        state.lines,
        msg.seg_id,
        () => ({ ...blank(msg.seg_id, msg.t0, msg.t1, now, mt), jaStable: msg.ja, final: true }),
        (l) => ({ ...l, jaStable: msg.ja, jaUnstable: "", final: true, t0: msg.t0, t1: msg.t1, updatedAt: now, arrivedAt: mt }),
      );
      return { ...state, lines, lastActivity: now };
    }
    case "translation_delta": {
      if (!state.lines.some((l) => l.segId === msg.seg_id)) return state;
      const lines = state.lines.map((l) =>
        l.segId === msg.seg_id ? { ...l, en: l.en + msg.en_delta, updatedAt: now } : l,
      );
      return { ...state, lines, lastActivity: now };
    }
    case "translation": {
      const merged = new Set(msg.merged ?? []);
      const lines = state.lines.map((l) => {
        if (l.segId === msg.seg_id) return { ...l, en: msg.en, enDone: true, updatedAt: now, enAt: mt };
        if (merged.has(l.segId)) return { ...l, enDone: true, enAt: mt };
        return l;
      });
      return { ...state, lines, banner: null, lastActivity: now };
    }
    case "error": {
      if (msg.code === "mt_model_inactive") {
        return { ...state, banner: MODE_CODE_BANNER, lines: markFailed(state.lines, msg.seg_id) };
      }
      if (msg.code === "mt_timeout" || msg.code === "mt_unreachable") {
        return { ...state, lines: markFailed(state.lines, msg.seg_id) };
      }
      return state;
    }
    default:
      return state;
  }
}

function markFailed(lines: Line[], segId: number | null | undefined): Line[] {
  if (segId === null || segId === undefined) return lines;
  return lines.map((l) => (l.segId === segId ? { ...l, enDone: true, enFailed: true } : l));
}

export interface View {
  current: Line | null;
  previous: Line | null; // the line before, still being read (overlap) or for "two lines"
  overlap: boolean; // previous is still within its own reading time
  banner: string | null;
}

interface Cue {
  line: Line;
  start: number;
  end: number;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** When a line leaves the screen, before bridging (video time). */
export function naturalEnd(l: Line): number {
  if (!l.final) return Infinity; // still being spoken
  if (!l.enDone) return Infinity; // English on its way: never cut it off
  const jaStart = Math.max(l.t0, l.arrivedAt);
  const enStart = Math.max(l.t0, l.enAt ?? l.arrivedAt);
  const t = TIMING;
  const ja = jaStart + clamp(l.jaStable.length / t.jaCps, t.minS, t.maxS);
  const en = l.en ? enStart + clamp(l.en.length / t.enCps, t.minS, t.maxS) : -Infinity;
  return Math.max(l.t1 + t.lingerS, ja, en);
}

function cues(lines: Line[]): Cue[] {
  const out = lines.map((line) => ({ line, start: line.t0, end: naturalEnd(line) }));
  for (let i = 0; i + 1 < out.length; i++) {
    const cur = out[i] as Cue;
    const next = out[i + 1] as Cue;
    // Short gap: keep the line until the next one instead of a blink of emptiness.
    if (next.start > cur.end && next.start - cur.end < TIMING.bridgeS) cur.end = next.start;
  }
  return out;
}

/**
 * What to show at `mediaTime` (video time: in ahead mode lines arrive early and
 * wait for their t0). A line appears when its speech starts, stays at least its
 * reading time, bridges short gaps, and when the next line starts too early the
 * two are shown stacked rather than the first being cut off.
 */
export function view(state: OverlayState, mediaTime: number): View {
  const all = cues(state.lines);
  const started = all.filter((c) => c.start <= mediaTime + 0.25);
  const visible = started.filter((c) => mediaTime < c.end);
  const cur = visible.at(-1) ?? null;
  if (!cur) return { current: null, previous: null, overlap: false, banner: state.banner };
  const idx = started.indexOf(cur);
  const before = idx > 0 ? (started[idx - 1] as Cue) : null;
  const overlap = !!before && visible.includes(before);
  return {
    current: cur.line,
    previous: before?.line.final ? before.line : null,
    overlap,
    banner: state.banner,
  };
}
