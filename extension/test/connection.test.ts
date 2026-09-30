import { afterEach, describe, expect, test } from "vitest";
import { WebSocket as NodeWebSocket, WebSocketServer } from "ws";
import { Connection, backoffMs } from "../src/background/connection";
import { PROTOCOL_VERSION, encodeFrame, type ServerMessage } from "../src/protocol";
import type { ConnState } from "../src/shared/messages";
import { startFakeServer } from "./fake-server";

const WS = NodeWebSocket as unknown as new (url: string) => WebSocket;
const fast = (fn: () => void, ms: number) => setTimeout(fn, ms / 50);
const servers: WebSocketServer[] = [];
afterEach(() => {
  for (const s of servers.splice(0)) s.close();
});

function until(cond: () => boolean, ms = 3000): Promise<void> {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const iv = setInterval(() => {
      if (cond()) {
        clearInterval(iv);
        resolve();
      } else if (Date.now() - start > ms) {
        clearInterval(iv);
        reject(new Error("timeout"));
      }
    }, 5);
  });
}

function recorder() {
  const states: [ConnState, boolean][] = [];
  const msgs: ServerMessage[] = [];
  return {
    states,
    msgs,
    events: {
      onServer: (m: ServerMessage) => msgs.push(m),
      onState: (s: ConnState, _d?: string, r?: boolean) => states.push([s, !!r]),
    },
  };
}

test("backoff doubles from 0.5 s and caps at 8 s", () => {
  expect([0, 1, 2, 3, 4, 5, 9].map(backoffMs)).toEqual([500, 1000, 2000, 4000, 8000, 8000, 8000]);
});

describe("connection", () => {
  test("hello with version and token, ready opens, frames flow", async () => {
    const wss = new WebSocketServer({ port: 0 });
    servers.push(wss);
    const got: unknown[] = [];
    let frames = 0;
    wss.on("connection", (ws) => {
      ws.on("message", (data, isBinary) => {
        if (isBinary) frames++;
        else {
          got.push(JSON.parse(String(data)));
          ws.send(JSON.stringify({ type: "ready", session_id: "s", asr_model: "a", mt_model: null, sample_rate: 16000 }));
        }
      });
    });
    const port = (wss.address() as { port: number }).port;
    const rec = recorder();
    const c = new Connection(`ws://127.0.0.1:${port}`, { video_id: "vid", token: "tok" }, rec.events, WS, fast);
    const frame = encodeFrame({ sampleIdx: 0, mediaTime: 0, pcm: new Int16Array(1600), discontinuity: false });
    c.start();
    c.sendFrame(frame); // not open yet: dropped
    await until(() => c.state === "open");
    c.sendFrame(frame);
    await until(() => frames === 1);
    expect(got[0]).toMatchObject({ type: "hello", protocol_version: PROTOCOL_VERSION, video_id: "vid", token: "tok" });
    expect(c.framesDropped).toBe(1);
    expect(rec.states.map((s) => s[0])).toEqual(["connecting", "open"]);
    c.stop();
  });

  test("reconnects by itself after the server restarts, flagged as reconnected", async () => {
    let wss = startFakeServer(0);
    servers.push(wss);
    await new Promise((r) => wss.on("listening", r));
    const port = (wss.address() as { port: number }).port;
    const rec = recorder();
    const c = new Connection(`ws://127.0.0.1:${port}/ws`, { video_id: "v" }, rec.events, WS, fast);
    c.start();
    await until(() => c.state === "open");
    for (const client of wss.clients) client.terminate();
    wss.close();
    await until(() => c.state === "reconnecting");
    wss = startFakeServer(port);
    servers.push(wss);
    await until(() => c.state === "open", 5000);
    expect(rec.states.at(-1)).toEqual(["open", true]);
    c.stop();
    expect(c.state).toBe("idle");
  });

  test("protocol mismatch is not retried", async () => {
    const wss = new WebSocketServer({ port: 0 });
    servers.push(wss);
    wss.on("connection", (ws) => {
      ws.on("message", () => {
        ws.send(JSON.stringify({ type: "error", code: "protocol_mismatch", message: "update", fatal: true }));
        ws.close();
      });
    });
    const port = (wss.address() as { port: number }).port;
    const rec = recorder();
    const c = new Connection(`ws://127.0.0.1:${port}`, { video_id: "v" }, rec.events, WS, fast);
    c.start();
    await until(() => c.state === "error");
    await new Promise((r) => setTimeout(r, 100));
    expect(c.state).toBe("error");
    expect(rec.msgs.some((m) => m.type === "error")).toBe(true);
  });

  test("fake server scripts partial → final → translation from frames", async () => {
    const wss = startFakeServer(0, { segmentSeconds: 1 });
    servers.push(wss);
    await new Promise((r) => wss.on("listening", r));
    const port = (wss.address() as { port: number }).port;
    const rec = recorder();
    const c = new Connection(`ws://127.0.0.1:${port}/ws`, { video_id: "v" }, rec.events, WS, fast);
    c.start();
    await until(() => c.state === "open");
    for (let i = 0; i < 12; i++) {
      c.sendFrame(encodeFrame({ sampleIdx: i * 1600, mediaTime: 50 + i * 0.1, pcm: new Int16Array(1600), discontinuity: false }));
    }
    await until(() => rec.msgs.some((m) => m.type === "translation"));
    const types = rec.msgs.map((m) => m.type);
    expect(types.indexOf("partial")).toBeLessThan(types.indexOf("final"));
    const fin = rec.msgs.find((m) => m.type === "final");
    expect(fin).toMatchObject({ seg_id: 1, t0: 50 });
    c.stop();
  });
});
