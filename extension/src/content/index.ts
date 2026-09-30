// Content script on youtube.com: capture (WP07), overlay (WP08), sync & latency
// (WP09), transcript & export (WP14). The WebSocket lives in the background (ADR-004).
import type { Lang, ServerMessage, Stats } from "../protocol";
import { PORT_NAME, type BgToContent, type ConnState, type ContentToBg, type TabCommand, type TabInfo } from "../shared/messages";
import { DISPLAY_CYCLE, loadSettings, onSettingsChanged, saveSettings, type Settings } from "../shared/settings";
import { Capture } from "./capture";
import { Transcript, saveSession, toSrt, toVtt } from "./history";
import { LatencyTracker } from "./latency";
import { Overlay } from "./overlay";
import { initialState, reduce, view, type OverlayState } from "./overlay-state";
import { TranscriptPanel } from "./panel";
import { controlsVisible, findPlayer, findVideo, isWatchPage, pageInfo, watchPlayer, type PageInfo } from "./player";

const log = (...args: unknown[]) => console.debug("[live-subs]", ...args);

let settings: Settings;
let overlay: Overlay;
let panel: TranscriptPanel;
let state: OverlayState = initialState;

// Session (one per video while capturing)
let port: browser.runtime.Port | null = null;
let capture: Capture | null = null;
let info: PageInfo | null = null;
let tracker = new LatencyTracker();
let transcript: Transcript | null = null;
let conn: ConnState = "idle";
let stats: Stats | null = null;
let hudOn = false;
let inAd = false;
let starting = false;
const segTimes = new Map<number, number>(); // seg_id → t1, for EN latency

function targets(s: Settings): Lang[] {
  return s.display === "ja" ? ["ja"] : ["ja", "en"];
}

function send(msg: ContentToBg): void {
  try {
    port?.postMessage(msg);
  } catch {
    /* background restarting */
  }
}

// ------------------------------------------------------------------ session

async function start(): Promise<void> {
  if (capture || starting) return;
  const video = findVideo();
  info = pageInfo();
  if (!video || !info.videoId) return;
  starting = true;
  try {
    port = browser.runtime.connect({ name: PORT_NAME });
    port.onMessage.addListener((m) => onBackground(m as BgToContent));
    port.onDisconnect.addListener(() => {
      port = null;
      void stop(false);
    });
    send({
      kind: "start",
      hello: {
        video_id: info.videoId,
        channel_id: info.channelId,
        title: info.title,
        targets: targets(settings),
        mode: settings.aheadMode ? "ahead" : "capture",
      },
    });
    if (settings.aheadMode) keepBehindLive(video, settings.aheadDelayS);
    tracker = new LatencyTracker();
    transcript = new Transcript({ videoId: info.videoId, title: info.title, startedAt: Date.now(), entries: [] });
    panel.setTranscript(transcript);
    state = initialState;
    await startCapture(video, 0);
    bindVideo(video);
    log("capturing", info);
  } catch (e) {
    log("start failed", e);
    await stop();
  } finally {
    starting = false;
  }
}

/**
 * Ahead mode (WP13): the server hears the live edge; the player stays `delay` s
 * behind it (YouTube's DVR), so subtitles are ready before their sentence.
 */
function keepBehindLive(video: HTMLVideoElement, delay: number): void {
  const n = video.seekable.length;
  if (!n) return;
  const edge = video.seekable.end(n - 1);
  if (edge - video.currentTime < delay) video.currentTime = Math.max(0, edge - delay);
}

async function startCapture(video: HTMLVideoElement, sampleIdx: number): Promise<void> {
  capture = new Capture(video, {
    method: settings.captureMethod,
    worklet: settings.workletMode,
    reroute: settings.captureReroute,
    onFrame: (buf) => send({ kind: "frame", buf }),
    log,
  });
  await capture.start(sampleIdx);
  if (sampleIdx > 0) capture.markDiscontinuity();
  capture.setSending(!video.paused && !inAd);
}

async function stop(notify = true): Promise<void> {
  const c = capture;
  capture = null;
  if (c) unbindVideo(c.video);
  await c?.stop();
  if (notify) send({ kind: "stop" });
  port?.disconnect();
  port = null;
  if (transcript && transcript.entries.length) {
    await saveSession(transcript.snapshot(), settings.historySessions).catch(() => undefined);
  }
  state = initialState;
  conn = "idle";
  segTimes.clear();
}

async function toggle(): Promise<void> {
  if (capture) await stop();
  else await start();
}

// ------------------------------------------------------------------ video events

const onPause = () => {
  send({ kind: "control", msg: { type: "pause" } });
  capture?.setSending(false);
};
const onPlay = () => {
  if (inAd) return;
  send({ kind: "control", msg: { type: "resume" } });
  capture?.setSending(true);
};
const onSeeking = () => capture?.markDiscontinuity();
const onSeeked = () => {
  const v = capture?.video;
  if (v) state = reduce(state, { type: "seek", mediaTime: v.currentTime });
};

function bindVideo(v: HTMLVideoElement): void {
  v.addEventListener("pause", onPause);
  v.addEventListener("play", onPlay);
  v.addEventListener("seeking", onSeeking);
  v.addEventListener("seeked", onSeeked);
}

function unbindVideo(v: HTMLVideoElement): void {
  v.removeEventListener("pause", onPause);
  v.removeEventListener("play", onPlay);
  v.removeEventListener("seeking", onSeeking);
  v.removeEventListener("seeked", onSeeked);
}

async function onPlayerChange(video: HTMLVideoElement | null, ad: boolean): Promise<void> {
  const player = findPlayer();
  if (player) overlay.attach(player);
  if (ad !== inAd) {
    inAd = ad;
    // Ads: the server closes the open segment; the next frame is a discontinuity.
    if (ad) onPause();
    else if (video && !video.paused) onPlay();
  }
  if (capture && video && video !== capture.video) {
    // YouTube recreated the element: same session, new source.
    const next = capture.nextSampleIdx;
    unbindVideo(capture.video);
    await capture.stop();
    await startCapture(video, next);
    bindVideo(video);
  }
}

async function onNavigate(): Promise<void> {
  panel.attach();
  const id = isWatchPage(location.href) ? pageInfo().videoId : null;
  if (capture && id !== info?.videoId) {
    await stop();
    if (id) await start(); // new video in the same tab → new session
    return;
  }
  if (!capture && id) setTimeout(() => void maybeAutoStart(), 1500); // channel id needs the page
}

async function maybeAutoStart(): Promise<void> {
  const p = pageInfo();
  if (!capture && p.channelId && settings.alwaysChannels.includes(p.channelId)) await start();
}

// ------------------------------------------------------------------ server messages

function onBackground(m: BgToContent): void {
  if (m.kind === "conn") {
    conn = m.state;
    if (m.state === "open") {
      if (m.reconnected) capture?.markDiscontinuity();
      send({ kind: "control", msg: { type: "config", show_partials: settings.showPartials, targets: targets(settings) } });
    }
    return;
  }
  onServer(m.msg);
}

function onServer(msg: ServerMessage): void {
  const now = performance.now();
  const v = capture?.video;
  state = reduce(state, { type: "server", msg, now, ...(v ? { mediaTime: v.currentTime } : {}) });
  const measurable = !!v && !v.paused && !v.seeking && v.playbackRate === 1;
  switch (msg.type) {
    case "final":
      transcript?.onFinal(msg.seg_id, msg.ja, msg.t0, msg.t1);
      segTimes.set(msg.seg_id, msg.t1);
      if (msg.ja && measurable) tracker.onFinal(msg.seg_id, msg.t1, v.currentTime, now);
      panel.update();
      break;
    case "translation": {
      transcript?.onTranslation(msg.seg_id, msg.en);
      const t1 = segTimes.get(msg.seg_id);
      if (t1 !== undefined && msg.en && measurable) tracker.onTranslation(msg.seg_id, t1, v.currentTime, now);
      panel.update();
      break;
    }
    case "stats":
      stats = msg;
      break;
    case "ahead_status":
      if (msg.state === "failed") state = initialState; // the capture takes over, new seg ids
      break;
    default:
      break;
  }
}

// ------------------------------------------------------------------ render loop

function fmt(ms: number | null | undefined): string {
  return ms === null || ms === undefined ? "  –  " : `${(ms / 1000).toFixed(2)}s`;
}

function tick(): void {
  const v = capture?.video ?? findVideo();
  const now = performance.now();
  if (capture && v) overlay.render(view(state, v.currentTime), controlsVisible());
  else overlay.render({ current: null, previous: null, overlap: false, banner: null }, false);
  if (hudOn) {
    const l = tracker.summary(now);
    overlay.setHud(
      `live-subs ${conn}\n` +
        `JA  p50 ${fmt(l.ja.p50)}  p95 ${fmt(l.ja.p95)}  (${l.ja.n})\n` +
        `EN  p50 ${fmt(l.en.p50)}  p95 ${fmt(l.en.p95)}  (${l.en.n})\n` +
        `srv JA ${fmt(stats?.ja_ms_p50)}  EN ${fmt(stats?.en_ms_p50)}  queue ${stats?.queue_depth ?? "–"}`,
    );
  } else {
    overlay.setHud("");
  }
}

// ------------------------------------------------------------------ commands from popup / keyboard

function exportTranscript(format: "srt" | "vtt", lang: "ja" | "en" | "both"): { filename: string; text: string } | null {
  if (!transcript) return null;
  const entries = transcript.entries;
  const base = `${info?.videoId ?? "live"}-${lang}`;
  return format === "srt"
    ? { filename: `${base}.srt`, text: toSrt(entries, lang) }
    : { filename: `${base}.vtt`, text: toVtt(entries, lang) };
}

browser.runtime.onMessage.addListener((raw: unknown) => {
  const cmd = raw as TabCommand;
  switch (cmd.kind) {
    case "toggle":
      return toggle().then(() => ({ capturing: !!capture }));
    case "cycle-display": {
      const i = DISPLAY_CYCLE.indexOf(settings.display);
      const display = DISPLAY_CYCLE[(i + 1) % DISPLAY_CYCLE.length] ?? "both";
      return saveSettings({ display }).then(() => ({ display }));
    }
    case "toggle-hud":
      hudOn = !hudOn;
      return Promise.resolve({ hud: hudOn });
    case "export":
      return Promise.resolve(exportTranscript(cmd.format, cmd.lang));
    case "info": {
      const p = pageInfo();
      const reply: TabInfo = { capturing: !!capture, videoId: p.videoId, channelId: p.channelId, title: p.title };
      return Promise.resolve(reply);
    }
  }
  return undefined;
});

// ------------------------------------------------------------------ boot

async function main(): Promise<void> {
  settings = await loadSettings();
  hudOn = settings.hud;
  overlay = new Overlay(
    settings,
    (position) => void saveSettings({ position }),
    () => {
      // a plain click on the subtitles still plays / pauses, like on the video
      const v = capture?.video ?? findVideo();
      if (v) void (v.paused ? v.play() : v.pause());
    },
  );
  panel = new TranscriptPanel((t) => {
    const v = capture?.video ?? findVideo();
    if (v) v.currentTime = t;
  });
  onSettingsChanged((s) => {
    const targetsChanged = targets(s).join() !== targets(settings).join();
    const partialsChanged = s.showPartials !== settings.showPartials;
    settings = s;
    overlay.setSettings(s);
    if (capture && (targetsChanged || partialsChanged)) {
      send({ kind: "control", msg: { type: "config", targets: targets(s), show_partials: s.showPartials } });
    }
  });
  watchPlayer((video, ad) => void onPlayerChange(video, ad));
  document.addEventListener("yt-navigate-finish", () => void onNavigate());
  setInterval(() => {
    tick();
  }, 100);
  setInterval(() => {
    if (capture) send({ kind: "latency", ...tracker.summary(performance.now()) });
  }, 5000);
  window.addEventListener("pagehide", () => void stop());
  await onNavigate();
}

void main();
