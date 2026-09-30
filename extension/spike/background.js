// WP02 spike background: opens the WebSocket (Q6) and acks every frame to the
// content script so it can measure the runtime.Port delay (Q8).
"use strict";

const WS_URL = "ws://192.168.1.200:8766/spike";

browser.runtime.onConnect.addListener((port) => {
  const log = (q, msg) => port.postMessage({ type: "log", q, msg });
  let ws;
  let wsSent = 0;
  try {
    ws = new WebSocket(WS_URL);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => log("Q6", `WebSocket from background: OPEN (${WS_URL})`);
    ws.onerror = () => log("Q6", "WebSocket from background: ERROR (is tools/spike_echo.py running?)");
    ws.onmessage = (e) => log("Q6", `echo server says: ${e.data}`);
  } catch (e) {
    log("Q6", `WebSocket from background threw: ${e}`);
  }
  port.onMessage.addListener((m) => {
    if (m.type === "frame") {
      port.postMessage({ type: "ack", seq: m.seq });
      if (ws?.readyState === WebSocket.OPEN) {
        const header = new DataView(new ArrayBuffer(13));
        header.setUint32(0, m.seq, true);
        header.setFloat64(4, m.mediaTime, true);
        header.setUint8(12, m.ad ? 1 : 0);
        ws.send(new Blob([header.buffer, m.pcm]));
        wsSent++;
      }
    } else if (m.type === "end") {
      log("Q6", `frames sent over background WebSocket: ${wsSent}`);
      ws?.close();
    }
  });
});
