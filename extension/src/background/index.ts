// Background event page: owns the WebSockets, relays server messages to the
// content scripts, keeps per-tab state for the popup, handles keyboard commands.
import { PORT_NAME, type BgToContent, type ContentToBg, type PopupRequest, type TabCommand, type TabState } from "../shared/messages";
import { loadSettings } from "../shared/settings";
import { Connection } from "./connection";

interface Tab {
  state: TabState;
  port: browser.runtime.Port;
  conn: Connection | null;
}

const tabs = new Map<number, Tab>();

function emptyState(tabId: number): TabState {
  return {
    tabId,
    capturing: false,
    conn: "idle",
    detail: null,
    videoId: null,
    channelId: null,
    title: null,
    asrModel: null,
    mtModel: null,
    mtInactive: null,
    stats: null,
    latency: null,
    protocolMismatch: false,
  };
}

function post(tab: Tab, msg: BgToContent): void {
  try {
    tab.port.postMessage(msg);
  } catch {
    /* tab closing */
  }
}

async function start(tab: Tab, hello: Extract<ContentToBg, { kind: "start" }>["hello"]): Promise<void> {
  tab.conn?.stop();
  const settings = await loadSettings();
  const s = tab.state;
  Object.assign(s, {
    capturing: true,
    videoId: hello.video_id,
    channelId: hello.channel_id ?? null,
    title: hello.title ?? null,
    mtInactive: null,
    protocolMismatch: false,
  });
  tab.conn = new Connection(
    settings.serverUrl,
    { ...hello, token: settings.token || null },
    {
      onServer(msg) {
        if (msg.type === "ready") {
          s.asrModel = msg.asr_model;
          s.mtModel = msg.mt_model;
        } else if (msg.type === "stats") {
          s.stats = msg;
        } else if (msg.type === "translation") {
          s.mtInactive = null;
        } else if (msg.type === "error") {
          if (msg.code === "mt_model_inactive") s.mtInactive = msg.message;
          if (msg.code === "protocol_mismatch") s.protocolMismatch = true;
        }
        post(tab, { kind: "server", msg });
      },
      onState(state, detail, reconnected) {
        s.conn = state;
        s.detail = detail ?? null;
        post(tab, { kind: "conn", state, ...(detail ? { detail } : {}), ...(reconnected ? { reconnected } : {}) });
      },
    },
  );
  tab.conn.start();
}

function stop(tab: Tab): void {
  tab.conn?.stop();
  tab.conn = null;
  tab.state.capturing = false;
  tab.state.conn = "idle";
}

browser.runtime.onConnect.addListener((port) => {
  const tabId = port.sender?.tab?.id;
  if (port.name !== PORT_NAME || tabId === undefined) return;
  const tab: Tab = { state: emptyState(tabId), port, conn: null };
  tabs.get(tabId)?.conn?.stop();
  tabs.set(tabId, tab);
  port.onMessage.addListener((raw) => {
    const msg = raw as ContentToBg;
    switch (msg.kind) {
      case "frame":
        tab.conn?.sendFrame(msg.buf);
        break;
      case "start":
        void start(tab, msg.hello);
        break;
      case "control":
        tab.conn?.sendControl(msg.msg);
        break;
      case "latency":
        tab.state.latency = { ja: msg.ja, en: msg.en };
        break;
      case "stop":
        stop(tab);
        break;
    }
  });
  port.onDisconnect.addListener(() => {
    if (tabs.get(tabId) === tab) {
      stop(tab);
      tabs.delete(tabId);
    }
  });
});

browser.runtime.onMessage.addListener((raw: unknown) => {
  const req = raw as PopupRequest;
  if (req.kind === "get-state") {
    return Promise.resolve(tabs.get(req.tabId)?.state ?? null);
  }
  if (req.kind === "toggle") {
    const cmd: TabCommand = { kind: "toggle" };
    return browser.tabs.sendMessage(req.tabId, cmd).catch(() => null);
  }
  return undefined;
});

browser.commands.onCommand.addListener(async (command) => {
  const [active] = await browser.tabs.query({ active: true, currentWindow: true });
  if (active?.id === undefined) return;
  const map: Record<string, TabCommand> = {
    "toggle-capture": { kind: "toggle" },
    "cycle-display": { kind: "cycle-display" },
    "toggle-hud": { kind: "toggle-hud" },
  };
  const cmd = map[command];
  if (cmd) await browser.tabs.sendMessage(active.id, cmd).catch(() => null);
});
