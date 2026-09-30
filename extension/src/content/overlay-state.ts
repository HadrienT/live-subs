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
}

export interface OverlayState {
  lines: Line[]; // ascending segId, a few at most
  banner: string | null;
  lastActivity: number;
}

export type Action =
  | { type: "server"; msg: ServerMessage; now: number }
  | { type: "seek"; mediaTime: number }
  | { type: "reset" };

// A line disappears 6 s of video after its speech ended (t1), without a newer one.
export const EXPIRE_S = 6;
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

function blank(segId: number, t0: number, t1: number, now: number): Line {
  return { segId, t0, t1, jaStable: "", jaUnstable: "", final: false, en: "", enDone: false, enFailed: false, updatedAt: now };
}

export function reduce(state: OverlayState, action: Action): OverlayState {
  switch (action.type) {
    case "reset":
      return initialState;
    case "seek":
      // Seek back: forget what belongs to a future we have not heard again yet.
      return { ...state, lines: state.lines.filter((l) => l.t0 <= action.mediaTime) };
    case "server":
      return onServer(state, action.msg, action.now);
  }
}

function onServer(state: OverlayState, msg: ServerMessage, now: number): OverlayState {
  switch (msg.type) {
    case "partial": {
      const existing = state.lines.find((l) => l.segId === msg.seg_id);
      if (existing?.final) return state; // a final replaces every partial, late ones included
      const lines = upsert(
        state.lines,
        msg.seg_id,
        () => ({ ...blank(msg.seg_id, msg.t0, msg.t1, now), jaStable: msg.ja_stable, jaUnstable: msg.ja_unstable }),
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
        () => ({ ...blank(msg.seg_id, msg.t0, msg.t1, now), jaStable: msg.ja, final: true }),
        (l) => ({ ...l, jaStable: msg.ja, jaUnstable: "", final: true, t0: msg.t0, t1: msg.t1, updatedAt: now }),
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
        if (l.segId === msg.seg_id) return { ...l, en: msg.en, enDone: true, updatedAt: now };
        if (merged.has(l.segId)) return { ...l, enDone: true };
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
  previous: Line | null;
  banner: string | null;
}

/**
 * What to show at `mediaTime`: only segments whose speech has started in the
 * video (t0 ≤ mediaTime), and nothing EXPIRE_S of video after the last one ended.
 * Video time, not wall time: in ahead mode (WP13) lines arrive before they play.
 */
export function view(state: OverlayState, mediaTime: number): View {
  const started = state.lines.filter((l) => l.t0 <= mediaTime + 0.25);
  const last = started.at(-1) ?? null;
  const current = last && (!last.final || mediaTime - last.t1 <= EXPIRE_S) ? last : null;
  const previous = started.length > 1 ? (started.at(-2) ?? null) : null;
  return { current, previous: current && previous?.final ? previous : null, banner: state.banner };
}
