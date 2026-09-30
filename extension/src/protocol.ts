// Wire protocol — hand-written MIRROR of server/src/livesubs/protocol.py.
//
// The Python file is the source of truth. Any change there lands in the same
// commit as the change here and bumps PROTOCOL_VERSION. test/protocol-drift.test.ts
// compares SCHEMA below with server/src/livesubs/protocol.schema.json, and tsc
// checks that SCHEMA and the interfaces agree.

export const PROTOCOL_VERSION = 1;
export const SAMPLE_RATE = 16_000;
export const FRAME_SAMPLES = 1_600; // nominal frame: 100 ms

// ------------------------------------------------------------------ frames

export const FRAME_KIND_AUDIO = 0x01;
export const FLAG_DISCONTINUITY = 0x01;
export const FRAME_HEADER_SIZE = 20;

export interface AudioFrame {
  sampleIdx: number; // uint64 on the wire; safe as a JS number for ~17 000 years of audio
  mediaTime: number;
  pcm: Int16Array;
  discontinuity: boolean;
}

/** kind u8 | flags u8 | reserved u16 | sample_idx u64 LE | media_time f64 LE | pcm int16 LE */
export function encodeFrame(frame: AudioFrame): ArrayBuffer {
  const buf = new ArrayBuffer(FRAME_HEADER_SIZE + frame.pcm.length * 2);
  const view = new DataView(buf);
  view.setUint8(0, FRAME_KIND_AUDIO);
  view.setUint8(1, frame.discontinuity ? FLAG_DISCONTINUITY : 0);
  view.setUint16(2, 0, true);
  view.setBigUint64(4, BigInt(frame.sampleIdx), true);
  view.setFloat64(12, frame.mediaTime, true);
  for (let i = 0; i < frame.pcm.length; i++) {
    view.setInt16(FRAME_HEADER_SIZE + 2 * i, frame.pcm[i] ?? 0, true);
  }
  return buf;
}

export function decodeFrame(buf: ArrayBuffer): AudioFrame {
  if (buf.byteLength < FRAME_HEADER_SIZE) throw new Error("frame too short");
  const view = new DataView(buf);
  if (view.getUint8(0) !== FRAME_KIND_AUDIO) throw new Error("unknown frame kind");
  const n = (buf.byteLength - FRAME_HEADER_SIZE) / 2;
  if (!Number.isInteger(n)) throw new Error("odd PCM payload length");
  const pcm = new Int16Array(n);
  for (let i = 0; i < n; i++) pcm[i] = view.getInt16(FRAME_HEADER_SIZE + 2 * i, true);
  return {
    sampleIdx: Number(view.getBigUint64(4, true)),
    mediaTime: view.getFloat64(12, true),
    pcm,
    discontinuity: (view.getUint8(1) & FLAG_DISCONTINUITY) !== 0,
  };
}

// ------------------------------------------------------------------ messages

export type Lang = "ja" | "en";

export const ERROR_CODES = [
  "protocol_mismatch",
  "unauthorized",
  "bad_message",
  "bad_frame",
  "not_ready",
  "mt_timeout",
  "mt_unreachable",
  "mt_model_inactive",
  "internal",
] as const;
export type ErrorCode = (typeof ERROR_CODES)[number];

// client → server
export interface Hello {
  type: "hello";
  protocol_version: number;
  token?: string | null;
  video_id: string;
  channel_id?: string | null;
  title?: string | null;
  targets?: Lang[];
}
export interface Pause {
  type: "pause";
}
export interface Resume {
  type: "resume";
}
export interface Config {
  type: "config";
  targets?: Lang[] | null;
  show_partials?: boolean | null;
}
export interface Ping {
  type: "ping";
  ts: number;
}

// server → client
export interface Ready {
  type: "ready";
  session_id: string;
  asr_model: string;
  mt_model: string | null;
  sample_rate?: number;
}
export interface Partial {
  type: "partial";
  seg_id: number;
  ja_stable: string;
  ja_unstable: string;
  t0: number;
  t1: number;
}
export interface Final {
  type: "final";
  seg_id: number;
  ja: string;
  t0: number;
  t1: number;
  asr_ms: number;
}
export interface TranslationDelta {
  type: "translation_delta";
  seg_id: number;
  en_delta: string;
}
export interface Translation {
  type: "translation";
  seg_id: number;
  en: string;
  mt_ms: number;
  merged?: number[];
}
export interface Stats {
  type: "stats";
  queue_depth: number;
  gpu_busy: boolean;
  asr_ms_p50?: number | null;
  asr_ms_p95?: number | null;
  ja_ms_p50?: number | null;
  ja_ms_p95?: number | null;
  en_ms_p50?: number | null;
  en_ms_p95?: number | null;
  mt_first_token_ms_p50?: number | null;
}
export interface ErrorMsg {
  type: "error";
  code: ErrorCode;
  message: string;
  fatal?: boolean;
  seg_id?: number | null;
}
export interface Pong {
  type: "pong";
  ts: number;
}

export type ClientMessage = Hello | Pause | Resume | Config | Ping;
export type ServerMessage =
  | Ready
  | Partial
  | Final
  | TranslationDelta
  | Translation
  | Stats
  | ErrorMsg
  | Pong;
export type Message = ClientMessage | ServerMessage;
export type MessageType = Message["type"];
type MessageOf<T extends MessageType> = Extract<Message, { type: T }>;

// ------------------------------------------------------------------ runtime schema

export type FieldKind = "string" | "integer" | "number" | "boolean" | "array";
export interface FieldSpec {
  type: FieldKind;
  required: boolean;
  nullable: boolean;
}
type Fields<T> = { [K in Exclude<keyof T, "type">]-?: FieldSpec };
export interface MessageSpec<T> {
  direction: "c2s" | "s2c";
  fields: Fields<T>;
}

const req = (type: FieldKind): FieldSpec => ({ type, required: true, nullable: false });
const opt = (type: FieldKind): FieldSpec => ({ type, required: false, nullable: false });
const optNull = (type: FieldKind): FieldSpec => ({ type, required: false, nullable: true });
const reqNull = (type: FieldKind): FieldSpec => ({ type, required: true, nullable: true });

/** Every message type with its fields: tsc forces it to match the interfaces above. */
export const SCHEMA: { [T in MessageType]: MessageSpec<MessageOf<T>> } = {
  hello: {
    direction: "c2s",
    fields: {
      protocol_version: req("integer"),
      token: optNull("string"),
      video_id: req("string"),
      channel_id: optNull("string"),
      title: optNull("string"),
      targets: opt("array"),
    },
  },
  pause: { direction: "c2s", fields: {} },
  resume: { direction: "c2s", fields: {} },
  config: {
    direction: "c2s",
    fields: { targets: optNull("array"), show_partials: optNull("boolean") },
  },
  ping: { direction: "c2s", fields: { ts: req("number") } },
  ready: {
    direction: "s2c",
    fields: {
      session_id: req("string"),
      asr_model: req("string"),
      mt_model: reqNull("string"),
      sample_rate: opt("integer"),
    },
  },
  partial: {
    direction: "s2c",
    fields: {
      seg_id: req("integer"),
      ja_stable: req("string"),
      ja_unstable: req("string"),
      t0: req("number"),
      t1: req("number"),
    },
  },
  final: {
    direction: "s2c",
    fields: {
      seg_id: req("integer"),
      ja: req("string"),
      t0: req("number"),
      t1: req("number"),
      asr_ms: req("number"),
    },
  },
  translation_delta: {
    direction: "s2c",
    fields: { seg_id: req("integer"), en_delta: req("string") },
  },
  translation: {
    direction: "s2c",
    fields: {
      seg_id: req("integer"),
      en: req("string"),
      mt_ms: req("number"),
      merged: opt("array"),
    },
  },
  stats: {
    direction: "s2c",
    fields: {
      queue_depth: req("integer"),
      gpu_busy: req("boolean"),
      asr_ms_p50: optNull("number"),
      asr_ms_p95: optNull("number"),
      ja_ms_p50: optNull("number"),
      ja_ms_p95: optNull("number"),
      en_ms_p50: optNull("number"),
      en_ms_p95: optNull("number"),
      mt_first_token_ms_p50: optNull("number"),
    },
  },
  error: {
    direction: "s2c",
    fields: {
      code: req("string"),
      message: req("string"),
      fatal: opt("boolean"),
      seg_id: optNull("integer"),
    },
  },
  pong: { direction: "s2c", fields: { ts: req("number") } },
};

function matchesKind(value: unknown, kind: FieldKind): boolean {
  switch (kind) {
    case "string":
      return typeof value === "string";
    case "integer":
      return Number.isInteger(value);
    case "number":
      return typeof value === "number" && Number.isFinite(value);
    case "boolean":
      return typeof value === "boolean";
    case "array":
      return Array.isArray(value);
  }
}

function conforms(value: unknown, direction: "c2s" | "s2c"): boolean {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  const type = record["type"];
  if (typeof type !== "string" || !Object.hasOwn(SCHEMA, type)) return false;
  const spec = SCHEMA[type as MessageType];
  if (spec.direction !== direction) return false;
  const fields = spec.fields as Record<string, FieldSpec>;
  for (const [name, field] of Object.entries(fields)) {
    const v = record[name];
    if (v === undefined) {
      if (field.required) return false;
    } else if (v === null) {
      if (!field.nullable) return false;
    } else if (!matchesKind(v, field.type)) {
      return false;
    }
  }
  if (type === "error" && !(ERROR_CODES as readonly unknown[]).includes(record["code"])) {
    return false;
  }
  return true;
}

export function isServerMessage(value: unknown): value is ServerMessage {
  return conforms(value, "s2c");
}

export function isClientMessage(value: unknown): value is ClientMessage {
  return conforms(value, "c2s");
}

/** Parse a JSON text frame from the server; null if it is not a known server message. */
export function parseServerMessage(text: string): ServerMessage | null {
  try {
    const value: unknown = JSON.parse(text);
    return isServerMessage(value) ? value : null;
  } catch {
    return null;
  }
}
