// WP02 capture spike. Adds a small panel on YouTube pages; every observation is
// logged with a timestamp in the panel, the console ("[spike]") and in a log file
// downloaded with the WAV at the end of the run.
"use strict";

const RECORD_SECONDS = 60;
const OUT_RATE = 16000;
const log = [];
let panelLog;

function note(q, msg) {
  const line = `${new Date().toISOString()} ${q} ${msg}`;
  log.push(line);
  console.log("[spike]", line);
  if (panelLog) {
    panelLog.textContent = line + "\n" + panelLog.textContent.slice(0, 4000);
  }
}

function buildPanel() {
  const panel = document.createElement("div");
  panel.style.cssText =
    "position:fixed;right:8px;bottom:8px;z-index:99999;background:#111;color:#eee;" +
    "font:12px monospace;padding:8px;width:420px;border:1px solid #555;border-radius:6px";
  panel.innerHTML = `
    <b>live-subs spike</b> — Firefox ${navigator.userAgent.match(/Firefox\/([\d.]+)/)?.[1] ?? "?"}<br>
    capture <select id="ls-method">
      <option value="mozCaptureStream">mozCaptureStream</option>
      <option value="captureStream">captureStream</option>
      <option value="mediaElementSource">createMediaElementSource</option>
    </select>
    <label><input type="checkbox" id="ls-route" checked> route to destination</label><br>
    worklet <select id="ls-worklet">
      <option value="auto">auto (ext URL → blob → ScriptProcessor)</option>
      <option value="ext">extension URL only</option>
      <option value="blob">blob URL only</option>
      <option value="script">ScriptProcessor only</option>
    </select>
    <label><input type="checkbox" id="ls-directws"> also WS from content</label><br>
    <button id="ls-start">record ${RECORD_SECONDS} s</button>
    <span id="ls-rms"></span>
    <pre id="ls-log" style="max-height:180px;overflow:auto;white-space:pre-wrap;margin:4px 0 0"></pre>`;
  document.body.appendChild(panel);
  panelLog = panel.querySelector("#ls-log");
  panel.querySelector("#ls-start").addEventListener("click", () => {
    run({
      method: panel.querySelector("#ls-method").value,
      route: panel.querySelector("#ls-route").checked,
      worklet: panel.querySelector("#ls-worklet").value,
      directWs: panel.querySelector("#ls-directws").checked,
      rmsEl: panel.querySelector("#ls-rms"),
    }).catch((e) => note("ERR", String(e?.stack ?? e)));
  });
}

function findVideo() {
  return document.querySelector("#movie_player video.html5-main-video") ?? document.querySelector("video");
}

// --- Q7: what happens to the element around ads, SPA navigation, quality, DVR
function watchVideo(video) {
  for (const ev of ["emptied", "loadedmetadata", "loadstart", "seeking", "seeked", "pause", "play",
    "waiting", "ratechange", "volumechange", "resize", "ended"]) {
    video.addEventListener(ev, () =>
      note("Q7", `video ${ev} t=${video.currentTime.toFixed(2)} src=${video.src.slice(0, 60)} ` +
        `muted=${video.muted} vol=${video.volume.toFixed(2)} ${video.videoWidth}x${video.videoHeight}`));
  }
  const player = document.querySelector("#movie_player");
  if (player) {
    let wasAd = player.classList.contains("ad-showing");
    new MutationObserver(() => {
      const isAd = player.classList.contains("ad-showing");
      if (isAd !== wasAd) note("Q7", `ad-showing ${wasAd} → ${isAd}`);
      wasAd = isAd;
    }).observe(player, { attributes: true, attributeFilter: ["class"] });
  }
  document.addEventListener("yt-navigate-finish", () =>
    note("Q7", `yt-navigate-finish url=${location.href} sameElement=${findVideo() === video}`));
}

function captureSource(ctx, video, method, route) {
  let node;
  if (method === "mediaElementSource") {
    node = ctx.createMediaElementSource(video);
    note("Q1", "createMediaElementSource OK");
  } else {
    const fn = video[method];
    if (typeof fn !== "function") throw new Error(`${method} not available`);
    const stream = fn.call(video);
    note("Q1", `${method} OK, audio tracks=${stream.getAudioTracks().length}`);
    stream.addEventListener("addtrack", (e) => note("Q7", `stream addtrack ${e.track.kind}`));
    stream.addEventListener("removetrack", (e) => note("Q7", `stream removetrack ${e.track.kind}`));
    node = ctx.createMediaStreamSource(stream);
  }
  if (route) {
    node.connect(ctx.destination);
    note("Q2", "source routed to destination — check the stream is audible exactly once");
  } else {
    note("Q2", "source NOT routed — is the stream still audible?");
  }
  return node;
}

async function makeResampler(ctx, mode, onFrame) {
  const url = browser.runtime.getURL("worklet.js");
  const tryWorklet = async (moduleUrl, label) => {
    try {
      await ctx.audioWorklet.addModule(moduleUrl);
      const node = new AudioWorkletNode(ctx, "spike-downsampler");
      node.port.onmessage = (e) => onFrame(new Int16Array(e.data));
      note("Q4", `AudioWorklet via ${label}: OK`);
      return node;
    } catch (e) {
      note("Q4", `AudioWorklet via ${label}: FAILED ${e}`);
      return null;
    }
  };
  if (mode === "auto" || mode === "ext") {
    const node = await tryWorklet(url, "extension URL");
    if (node || mode === "ext") return node;
  }
  if (mode === "auto" || mode === "blob") {
    const src = await (await fetch(url)).text();
    const blobUrl = URL.createObjectURL(new Blob([src], { type: "application/javascript" }));
    const node = await tryWorklet(blobUrl, "blob URL");
    if (node || mode === "blob") return node;
  }
  // ScriptProcessor fallback: naive decimation (for the comparison only)
  const sp = ctx.createScriptProcessor(4096, 2, 1);
  const step = ctx.sampleRate / OUT_RATE;
  let phase = 0;
  let buf = [];
  sp.onaudioprocess = (e) => {
    const l = e.inputBuffer.getChannelData(0);
    const r = e.inputBuffer.numberOfChannels > 1 ? e.inputBuffer.getChannelData(1) : l;
    for (; phase < l.length; phase += step) {
      const i = Math.floor(phase);
      buf.push(Math.max(-32768, Math.min(32767, Math.round(((l[i] + r[i]) / 2) * 32767))));
      if (buf.length === 1600) {
        onFrame(Int16Array.from(buf));
        buf = [];
      }
    }
    phase -= l.length;
  };
  note("Q4", "using ScriptProcessorNode fallback");
  return sp;
}

function wavBlob(chunks) {
  const n = chunks.reduce((a, c) => a + c.length, 0);
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const str = (o, s) => [...s].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
  str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE"); str(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, OUT_RATE, true); v.setUint32(28, OUT_RATE * 2, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data"); v.setUint32(40, n * 2, true);
  let o = 44;
  for (const c of chunks) for (const s of c) { v.setInt16(o, s, true); o += 2; }
  return new Blob([buf], { type: "audio/wav" });
}

function download(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function run(opts) {
  const video = findVideo();
  if (!video) throw new Error("no <video> found");
  const player = document.querySelector("#movie_player");
  note("INFO", `video found, live=${!!document.querySelector(".ytp-live")} src=${video.src.slice(0, 60)}`);
  watchVideo(video);

  const ctx = new AudioContext();
  note("INFO", `AudioContext sampleRate=${ctx.sampleRate} state=${ctx.state}`);
  if (ctx.state !== "running") await ctx.resume();

  // --- Q6/Q8: the background opens the WebSocket; we measure the port
  const port = browser.runtime.connect({ name: "spike" });
  let sent = 0;
  const sentAt = new Map();
  const portDelays = [];
  port.onMessage.addListener((m) => {
    if (m.type === "ack") {
      const t = sentAt.get(m.seq);
      if (t !== undefined) { portDelays.push(performance.now() - t); sentAt.delete(m.seq); }
    } else if (m.type === "log") {
      note(m.q, m.msg);
    }
  });
  let directWs = null;
  if (opts.directWs) {
    try {
      directWs = new WebSocket("ws://192.168.1.200:8766/spike");
      directWs.onopen = () => note("Q6", "WebSocket from content script: OPEN");
      directWs.onerror = () => note("Q6", "WebSocket from content script: ERROR");
    } catch (e) {
      note("Q6", `WebSocket from content script threw: ${e}`);
    }
  }

  const chunks = [];
  let sumSq = 0;
  let count = 0;
  const target = RECORD_SECONDS * OUT_RATE;
  let total = 0;
  let done;
  const finished = new Promise((r) => (done = r));
  const onFrame = (pcm) => {
    if (total >= target) return;
    chunks.push(pcm);
    total += pcm.length;
    for (const s of pcm) { sumSq += s * s; count++; }
    const seq = sent++;
    sentAt.set(seq, performance.now());
    port.postMessage({ type: "frame", seq, mediaTime: video.currentTime, ad: player?.classList.contains("ad-showing") ?? false, pcm: pcm.buffer });
    if (directWs?.readyState === WebSocket.OPEN) directWs.send(pcm.buffer.slice(0));
    if (total >= target) done();
  };

  const src = captureSource(ctx, video, opts.method, opts.route);
  const resampler = await makeResampler(ctx, opts.worklet, onFrame);
  if (!resampler) throw new Error("no resampler");
  src.connect(resampler);
  if (resampler instanceof ScriptProcessorNode) resampler.connect(ctx.destination); // must be pulled

  // --- Q3: RMS every second; mute YouTube / change its volume during the run
  const rmsTimer = setInterval(() => {
    const rms = count ? Math.sqrt(sumSq / count) / 32768 : 0;
    const db = rms > 0 ? (20 * Math.log10(rms)).toFixed(1) : "-inf";
    opts.rmsEl.textContent = `RMS ${db} dBFS  ${(total / OUT_RATE).toFixed(0)}/${RECORD_SECONDS}s`;
    note("Q3", `rms=${db}dBFS muted=${video.muted} volume=${video.volume.toFixed(2)}`);
    sumSq = 0; count = 0;
  }, 1000);

  await finished;
  clearInterval(rmsTimer);
  src.disconnect();
  resampler.disconnect();
  await new Promise((r) => setTimeout(r, 1000));
  portDelays.sort((a, b) => a - b);
  const pct = (q) => portDelays.length ? portDelays[Math.floor(q * (portDelays.length - 1))].toFixed(2) : "n/a";
  note("Q8", `port frames sent=${sent} acked=${portDelays.length} lost=${sent - portDelays.length} ` +
    `round-trip p50=${pct(0.5)}ms p95=${pct(0.95)}ms max=${pct(1)}ms`);
  port.postMessage({ type: "end" });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  download(wavBlob(chunks), `spike-${stamp}.wav`);
  download(new Blob([log.join("\n")], { type: "text/plain" }), `spike-${stamp}.log`);
  note("INFO", "done: WAV and log downloaded (move them to benchmarks/data/)");
  directWs?.close();
  await ctx.close();
}

buildPanel();
