// Subtitle overlay: a shadow DOM host inside #movie_player, so it follows
// fullscreen / theatre mode and YouTube's CSS cannot reach it (WP08 §1).
import type { Settings } from "../shared/settings";
import type { View } from "./overlay-state";

const CSS = `
:host { all: initial; }
.wrap {
  position: absolute; left: 0; right: 0; bottom: var(--bottom, 8%);
  display: flex; flex-direction: column; align-items: center; gap: 0.2em;
  pointer-events: none; /* clicks go to the player, except on the handle */
  font-family: "Noto Sans JP", "Hiragino Sans", "Yu Gothic UI", system-ui, sans-serif;
  font-size: var(--size, 24px); line-height: 1.35; z-index: 60;
  transition: bottom 0.15s ease-out;
}
.box {
  position: relative; max-width: 88%; padding: 0.2em 0.6em; border-radius: 0.3em;
  background: rgba(8, 8, 8, var(--opacity, 0.6)); color: #fff; text-align: center;
  text-shadow: 0 0 2px #000, 0 0 4px #000;
}
.box:empty, .line:empty { display: none; }
.prev { font-size: 0.72em; opacity: 0.75; }
.ja .unstable { color: #b8b8b8; }
.en { font-size: 0.92em; }
.en.streaming { font-style: italic; color: #e6e6e6; }
.banner { font-size: 0.7em; color: #ffd166; }
.handle {
  pointer-events: auto; cursor: grab; position: absolute; left: -1.1em; top: 50%;
  transform: translateY(-50%); width: 0.7em; height: 1.4em; border-radius: 0.2em;
  background: rgba(255, 255, 255, 0.25); opacity: 0; transition: opacity 0.2s;
}
.wrap:hover .handle, .handle.dragging { opacity: 1; }
.hud {
  position: absolute; top: 8px; right: 8px; font: 12px/1.4 ui-monospace, monospace;
  background: rgba(0, 0, 0, 0.7); color: #9f9; padding: 4px 8px; border-radius: 4px;
  pointer-events: none; white-space: pre; z-index: 61;
}
.hud:empty { display: none; }
`;

export class Overlay {
  readonly host: HTMLElement;
  private readonly root: ShadowRoot;
  private readonly wrap: HTMLElement;
  private readonly prev: HTMLElement;
  private readonly box: HTMLElement;
  private readonly ja: HTMLElement;
  private readonly en: HTMLElement;
  private readonly banner: HTMLElement;
  private readonly hud: HTMLElement;
  private settings: Settings;
  private dragBottomPct: number | null;

  constructor(settings: Settings, private readonly onMoved: (bottomPct: number) => void) {
    this.settings = settings;
    this.dragBottomPct = settings.position?.bottomPct ?? null;
    this.host = document.createElement("div");
    this.host.id = "live-subs-overlay";
    this.host.style.cssText = "position:absolute;inset:0;pointer-events:none;z-index:60";
    this.root = this.host.attachShadow({ mode: "closed" });
    const style = document.createElement("style");
    style.textContent = CSS;
    this.wrap = el("div", "wrap");
    this.prev = el("div", "box prev");
    this.box = el("div", "box");
    this.ja = el("div", "line ja");
    this.en = el("div", "line en");
    this.banner = el("div", "line banner");
    const handle = el("div", "handle");
    handle.title = "live-subs: drag to move";
    this.box.append(handle, this.ja, this.en, this.banner);
    this.wrap.append(this.prev, this.box);
    this.hud = el("div", "hud");
    this.root.append(style, this.wrap, this.hud);
    this.enableDrag(handle);
  }

  attach(player: HTMLElement): void {
    if (this.host.parentElement !== player) player.appendChild(this.host);
  }

  detach(): void {
    this.host.remove();
  }

  setSettings(s: Settings): void {
    this.settings = s;
    this.dragBottomPct = s.position?.bottomPct ?? this.dragBottomPct;
  }

  render(v: View, controlsVisible: boolean): void {
    const s = this.settings;
    const height = this.host.parentElement?.clientHeight ?? 720;
    // ~3.4 % of the player height: 24 px at 720p, 37 px in 1080p fullscreen, 73 px in 4K.
    this.wrap.style.setProperty("--size", `${Math.max(14, height * 0.034 * s.fontScale)}px`);
    this.wrap.style.setProperty("--opacity", String(s.opacity));
    const base = this.dragBottomPct ?? 8;
    this.wrap.style.setProperty("--bottom", `${controlsVisible && this.dragBottomPct === null ? 14 : base}%`);

    const cur = v.current;
    const showJa = s.display !== "en";
    const showEn = s.display !== "ja";
    this.ja.replaceChildren();
    if (cur && showJa) {
      this.ja.append(document.createTextNode(cur.jaStable));
      if (cur.jaUnstable) this.ja.append(el("span", "unstable", cur.jaUnstable));
    }
    this.en.textContent = cur && showEn ? cur.en : "";
    this.en.classList.toggle("streaming", !!cur && !cur.enDone);
    this.banner.textContent = showEn && v.banner && (!cur || !cur.en) ? v.banner : "";
    const prev = s.twoLines ? v.previous : null;
    this.prev.textContent = prev ? [showJa ? prev.jaStable : "", showEn ? prev.en : ""].filter(Boolean).join("\n") : "";
    this.prev.style.whiteSpace = "pre-line";
    this.box.style.display = this.ja.textContent || this.en.textContent || this.banner.textContent ? "" : "none";
  }

  setHud(text: string): void {
    this.hud.textContent = text;
  }

  private enableDrag(handle: HTMLElement): void {
    handle.addEventListener("pointerdown", (e: PointerEvent) => {
      e.preventDefault();
      e.stopPropagation();
      handle.setPointerCapture(e.pointerId);
      handle.classList.add("dragging");
      const rect = this.host.getBoundingClientRect();
      const move = (ev: PointerEvent) => {
        const pct = ((rect.bottom - ev.clientY) / rect.height) * 100;
        this.dragBottomPct = Math.min(90, Math.max(0, pct - 3));
        this.wrap.style.setProperty("--bottom", `${this.dragBottomPct}%`);
      };
      const up = () => {
        handle.classList.remove("dragging");
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
        if (this.dragBottomPct !== null) this.onMoved(this.dragBottomPct);
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
    });
    // Double-click resets to the default, control-bar-aware position.
    handle.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      this.dragBottomPct = null;
      this.onMoved(-1);
    });
  }
}

function el(tag: string, cls: string, text?: string): HTMLElement {
  const node = document.createElement(tag);
  node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
