// One WebSocket per capturing tab (ADR-004: opened by the background, not the page).
import { PROTOCOL_VERSION, parseServerMessage, type Hello, type ServerMessage } from "../protocol";
import type { ConnState } from "../shared/messages";

/** 0.5 s, 1, 2, 4, 8, 8, … (WP07 §3). */
export function backoffMs(attempt: number): number {
  return Math.min(500 * 2 ** attempt, 8000);
}

export interface ConnectionEvents {
  onServer(msg: ServerMessage): void;
  onState(state: ConnState, detail?: string, reconnected?: boolean): void;
}

type WsCtor = new (url: string) => WebSocket;

// Errors after which reconnecting cannot help.
const FATAL_NO_RETRY = new Set(["protocol_mismatch", "unauthorized"]);

export class Connection {
  state: ConnState = "idle";
  framesDropped = 0;
  private ws: WebSocket | null = null;
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private everOpened = false;
  private noRetry = false;

  constructor(
    private readonly url: string,
    private readonly hello: Omit<Hello, "type" | "protocol_version">,
    private readonly events: ConnectionEvents,
    private readonly WS: WsCtor = WebSocket,
    private readonly schedule: (fn: () => void, ms: number) => ReturnType<typeof setTimeout> = setTimeout,
  ) {}

  start(): void {
    this.stopped = false;
    this.connect();
  }

  private setState(state: ConnState, detail?: string, reconnected?: boolean): void {
    this.state = state;
    this.events.onState(state, detail, reconnected);
  }

  private connect(): void {
    this.setState(this.everOpened || this.attempt > 0 ? "reconnecting" : "connecting");
    let ws: WebSocket;
    try {
      ws = new this.WS(this.url);
    } catch (e) {
      this.setState("error", `bad server URL: ${String(e)}`);
      return;
    }
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      const hello: Hello = { type: "hello", protocol_version: PROTOCOL_VERSION, ...this.hello };
      ws.send(JSON.stringify(hello));
    };
    ws.onmessage = (e: MessageEvent) => {
      if (typeof e.data !== "string") return;
      const msg = parseServerMessage(e.data);
      if (!msg) return;
      if (msg.type === "ready") {
        const reconnected = this.everOpened;
        this.everOpened = true;
        this.attempt = 0;
        this.setState("open", undefined, reconnected);
      } else if (msg.type === "error" && msg.fatal && FATAL_NO_RETRY.has(msg.code)) {
        this.noRetry = true;
        this.setState("error", msg.message);
      }
      this.events.onServer(msg);
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (this.stopped) {
        this.setState("idle");
      } else if (this.noRetry) {
        // state already "error" with the server's reason
      } else {
        const delay = backoffMs(this.attempt++);
        this.setState("reconnecting", `retry in ${(delay / 1000).toFixed(1)} s`);
        this.timer = this.schedule(() => {
          this.timer = null;
          if (!this.stopped) this.connect();
        }, delay);
      }
    };
    ws.onerror = () => {
      /* onclose follows */
    };
  }

  /** Live audio: while disconnected, frames are dropped, never buffered. */
  sendFrame(buf: ArrayBuffer): void {
    if (this.state === "open" && this.ws?.readyState === 1) {
      this.ws.send(buf);
    } else {
      this.framesDropped++;
    }
  }

  sendControl(msg: object): void {
    if (this.state === "open" && this.ws?.readyState === 1) this.ws.send(JSON.stringify(msg));
  }

  stop(): void {
    this.stopped = true;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    const ws = this.ws;
    this.ws = null;
    ws?.close();
    this.setState("idle");
  }
}
