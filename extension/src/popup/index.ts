// Popup: per-tab switch, connection, models, latencies, display mode, export.
import type { PopupRequest, TabCommand, TabInfo, TabState } from "../shared/messages";
import { loadSettings, saveSettings, type DisplayMode } from "../shared/settings";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const CONN_LABEL: Record<string, string> = {
  idle: "inactif",
  connecting: "connexion…",
  open: "connecté",
  reconnecting: "reconnexion…",
  error: "erreur",
};

let tabId: number | null = null;
let channelId: string | null = null;

const sec = (ms: number | null | undefined) => (ms === null || ms === undefined ? "–" : `${(ms / 1000).toFixed(2)} s`);

async function refresh(): Promise<void> {
  if (tabId === null) return;
  const req: PopupRequest = { kind: "get-state", tabId };
  const state = (await browser.runtime.sendMessage(req)) as TabState | null;
  const info = (await browser.tabs.sendMessage(tabId, { kind: "info" } satisfies TabCommand).catch(() => null)) as TabInfo | null;
  channelId = info?.channelId ?? null;
  const capturing = state?.capturing ?? info?.capturing ?? false;
  const toggle = $<HTMLButtonElement>("toggle");
  toggle.textContent = capturing ? "Désactiver sur cet onglet" : "Activer sur cet onglet";
  toggle.classList.toggle("on", capturing);
  toggle.disabled = !info?.videoId;
  const conn = state?.conn ?? "idle";
  const pill = $("conn");
  pill.textContent = CONN_LABEL[conn] ?? conn;
  pill.className = `pill ${conn}`;
  pill.title = state?.detail ?? "";

  const notice = $("notice");
  const msg = !info?.videoId
    ? "Ouvrez un stream YouTube (watch ou live)."
    : state?.protocolMismatch
      ? "Le serveur parle une autre version du protocole : mettez l'extension à jour."
      : state?.mtInactive
        ? "LLM en mode code — passer AgenticEnv en mode traduction (profil translate)."
        : conn === "error"
          ? `Erreur : ${state?.detail ?? "serveur injoignable"}`
          : "";
  notice.hidden = !msg;
  notice.textContent = msg;

  const lat = state?.latency;
  $("ja50").textContent = sec(lat?.ja.p50 ?? state?.stats?.ja_ms_p50);
  $("ja95").textContent = sec(lat?.ja.p95 ?? state?.stats?.ja_ms_p95);
  $("en50").textContent = sec(lat?.en.p50 ?? state?.stats?.en_ms_p50);
  $("en95").textContent = sec(lat?.en.p95 ?? state?.stats?.en_ms_p95);
  $("models").textContent = state?.asrModel ? `ASR ${state.asrModel} · MT ${state.mtModel ?? "aucun"}` : "";
  $("queue").textContent = state?.stats
    ? `File GPU ${state.stats.queue_depth}${state.stats.gpu_busy ? " · GPU occupé" : ""}`
    : "";

  const settings = await loadSettings();
  const always = $<HTMLInputElement>("always");
  always.disabled = !channelId;
  always.checked = !!channelId && settings.alwaysChannels.includes(channelId);
}

function download(filename: string, text: string): void {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
}

async function main(): Promise<void> {
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  tabId = tab?.id ?? null;
  const settings = await loadSettings();
  for (const radio of document.querySelectorAll<HTMLInputElement>('input[name="display"]')) {
    radio.checked = radio.value === settings.display;
    radio.addEventListener("change", () => void saveSettings({ display: radio.value as DisplayMode }));
  }
  $("toggle").addEventListener("click", () => {
    if (tabId === null) return;
    void browser.runtime.sendMessage({ kind: "toggle", tabId } satisfies PopupRequest).then(refresh);
  });
  $<HTMLInputElement>("always").addEventListener("change", async (e) => {
    if (!channelId) return;
    const on = (e.target as HTMLInputElement).checked;
    const s = await loadSettings();
    const set = new Set(s.alwaysChannels);
    if (on) set.add(channelId);
    else set.delete(channelId);
    await saveSettings({ alwaysChannels: [...set] });
  });
  for (const btn of document.querySelectorAll<HTMLButtonElement>("button[data-format]")) {
    btn.addEventListener("click", async () => {
      if (tabId === null) return;
      const format = btn.dataset["format"] as "srt" | "vtt";
      const lang = $<HTMLSelectElement>("lang").value as "ja" | "en" | "both";
      const cmd: TabCommand = { kind: "export", format, lang };
      const file = (await browser.tabs.sendMessage(tabId, cmd).catch(() => null)) as { filename: string; text: string } | null;
      if (file?.text) download(file.filename, file.text);
    });
  }
  $("options").addEventListener("click", (e) => {
    e.preventDefault();
    void browser.runtime.openOptionsPage();
  });
  await refresh();
  setInterval(() => void refresh(), 1000);
}

void main();
