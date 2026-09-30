// Scripted WebSocket server for developing the extension without GPU (WP07 §4):
//   just fake-server [port]   then point the extension at ws://127.0.0.1:<port>/ws
// Replies `ready`, then for every ~3 s of audio received emits partials, a final
// and a streamed translation, with t0/t1 taken from the frames' media_time.
import { WebSocketServer, type WebSocket } from "ws";
import { PROTOCOL_VERSION, decodeFrame, type ServerMessage } from "../src/protocol";

const LINES: [string, string][] = [
  ["みなさんこんばんは、今日も配信に来てくれてありがとう", "Good evening everyone, thanks for coming to the stream again today"],
  ["今日は新しいゲームをやっていきたいと思います", "Today I'd like to play a new game"],
  ["えっ、ちょっと待って、今の見た？", "Wait, hold on, did you see that?"],
  ["コメント読みますね", "I'll read some comments"],
];

export function startFakeServer(port: number, opts: { segmentSeconds?: number } = {}): WebSocketServer {
  const segSeconds = opts.segmentSeconds ?? 3;
  const wss = new WebSocketServer({ port, path: "/ws" });
  wss.on("connection", (ws: WebSocket) => {
    let seg = 0;
    let segStart: number | null = null;
    let received = 0;
    const send = (m: ServerMessage) => ws.send(JSON.stringify(m));
    ws.on("message", (data, isBinary) => {
      if (!isBinary) {
        const msg = JSON.parse(String(data)) as { type: string; protocol_version?: number; ts?: number };
        if (msg.type === "hello") {
          if (msg.protocol_version !== PROTOCOL_VERSION) {
            send({ type: "error", code: "protocol_mismatch", message: "fake server", fatal: true });
            ws.close();
            return;
          }
          send({ type: "ready", session_id: "fake", asr_model: "fake-asr", mt_model: "fake-mt", sample_rate: 16000 });
        } else if (msg.type === "ping") {
          send({ type: "pong", ts: msg.ts ?? 0 });
        }
        return;
      }
      const buf = data as Buffer;
      const frame = decodeFrame(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer);
      segStart ??= frame.mediaTime;
      received += frame.pcm.length;
      const elapsed = received / 16000;
      const [ja, en] = LINES[seg % LINES.length] ?? ["", ""];
      const shown = Math.floor((ja.length * elapsed) / segSeconds);
      const t0 = segStart;
      const t1 = frame.mediaTime + 0.1;
      if (elapsed < segSeconds) {
        if (received % 16000 < frame.pcm.length) {
          send({ type: "partial", seg_id: seg + 1, ja_stable: ja.slice(0, Math.max(0, shown - 3)), ja_unstable: ja.slice(Math.max(0, shown - 3), shown), t0, t1 });
        }
        return;
      }
      const id = ++seg;
      send({ type: "final", seg_id: id, ja, t0, t1, asr_ms: 120 });
      segStart = null;
      received = 0;
      const words = en.split(" ");
      words.forEach((w, i) => {
        setTimeout(() => send({ type: "translation_delta", seg_id: id, en_delta: (i ? " " : "") + w }), 300 + 60 * i);
      });
      setTimeout(() => send({ type: "translation", seg_id: id, en, mt_ms: 300 + 60 * words.length, merged: [] }), 350 + 60 * words.length);
    });
  });
  return wss;
}

if (process.argv[1]?.endsWith("fake-server.ts")) {
  const port = Number(process.argv[2] ?? 8765);
  startFakeServer(port);
  console.log(`fake live-subs server on ws://127.0.0.1:${port}/ws`);
}
