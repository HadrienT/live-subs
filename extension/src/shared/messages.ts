// Messages between extension contexts (not the wire protocol: see protocol.ts).
import type { AheadStatus, ClientMessage, Hello, ServerMessage, Stats } from "../protocol";

export const PORT_NAME = "live-subs-capture";

export type ConnState = "idle" | "connecting" | "open" | "reconnecting" | "error";

/** content → background, over the runtime.Port of the tab. */
export type ContentToBg =
  | { kind: "start"; hello: Omit<Hello, "type" | "protocol_version" | "token"> }
  | { kind: "frame"; buf: ArrayBuffer }
  | { kind: "control"; msg: ClientMessage }
  | { kind: "latency"; ja: LatencySummary; en: LatencySummary }
  | { kind: "stop" };

/** background → content. */
export type BgToContent =
  | { kind: "server"; msg: ServerMessage }
  | { kind: "conn"; state: ConnState; detail?: string; reconnected?: boolean };

export interface LatencySummary {
  p50: number | null;
  p95: number | null;
  n: number;
}

/** State of one tab, shown by the popup. */
export interface TabState {
  tabId: number;
  capturing: boolean;
  conn: ConnState;
  detail: string | null;
  videoId: string | null;
  channelId: string | null;
  title: string | null;
  asrModel: string | null;
  mtModel: string | null;
  mtInactive: string | null; // message of the last mt_model_inactive error
  stats: Stats | null;
  latency: { ja: LatencySummary; en: LatencySummary } | null;
  protocolMismatch: boolean;
  ahead: AheadStatus | null;
}

/** popup / commands → background (runtime.sendMessage). */
export type PopupRequest =
  | { kind: "get-state"; tabId: number }
  | { kind: "toggle"; tabId: number }
  | { kind: "export"; tabId: number; format: "srt" | "vtt"; lang: "ja" | "en" | "both" };

/** background → content (tabs.sendMessage). */
export type TabCommand =
  | { kind: "toggle" }
  | { kind: "cycle-display" }
  | { kind: "toggle-hud" }
  | { kind: "export"; format: "srt" | "vtt"; lang: "ja" | "en" | "both" }
  | { kind: "info" };

export interface TabInfo {
  capturing: boolean;
  videoId: string | null;
  channelId: string | null;
  title: string | null;
}
