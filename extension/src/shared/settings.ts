// User settings, persisted in storage.local (survive Firefox restarts).

export type DisplayMode = "both" | "en" | "ja";
/** % of the player: bottom edge of the subtitles, horizontal centre. */
export interface SubtitlePosition {
  bottomPct: number;
  centerPct?: number; // absent in settings saved before horizontal dragging
}
export type CaptureMethod = "auto" | "captureStream" | "mediaElementSource";
export type WorkletMode = "auto" | "extension" | "blob" | "scriptProcessor";

export interface Settings {
  serverUrl: string;
  token: string;
  display: DisplayMode;
  showPartials: boolean;
  twoLines: boolean; // previous sentence above, smaller
  fontScale: number; // × the default size (relative to the player height)
  opacity: number; // background opacity 0..1
  position: SubtitlePosition | null; // dragged position (null = default, above the controls)
  alwaysChannels: string[]; // channel ids captured automatically
  // Fallbacks decided by the WP02 spike; "auto" tries them in order.
  captureMethod: CaptureMethod;
  workletMode: WorkletMode;
  captureReroute: boolean; // play the captured sound too (a Firefox that mutes captured videos)
  hud: boolean; // latency HUD (Alt+L)
  historySessions: number; // sessions kept for the transcript panel / export
  aheadMode: boolean; // WP13: the server pulls the live, the player stays behind it
  aheadDelayS: number; // how far behind the live edge the player is kept
}

export const DEFAULT_SETTINGS: Settings = {
  serverUrl: "ws://192.168.1.200:8765/ws",
  token: "",
  display: "both",
  showPartials: true,
  twoLines: false,
  fontScale: 1,
  opacity: 0.6,
  position: null,
  alwaysChannels: [],
  captureMethod: "auto",
  workletMode: "auto",
  captureReroute: false,
  hud: false,
  historySessions: 20,
  aheadMode: false,
  aheadDelayS: 10,
};

export function withDefaults(stored: Partial<Settings> | undefined): Settings {
  return { ...DEFAULT_SETTINGS, ...(stored ?? {}) };
}

export async function loadSettings(): Promise<Settings> {
  const { settings } = (await browser.storage.local.get("settings")) as { settings?: Partial<Settings> };
  return withDefaults(settings);
}

export async function saveSettings(patch: Partial<Settings>): Promise<Settings> {
  const next = { ...(await loadSettings()), ...patch };
  await browser.storage.local.set({ settings: next });
  return next;
}

export function onSettingsChanged(cb: (s: Settings) => void): void {
  browser.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes["settings"]) {
      cb(withDefaults(changes["settings"].newValue as Partial<Settings>));
    }
  });
}

export const DISPLAY_CYCLE: DisplayMode[] = ["both", "en", "ja"];
