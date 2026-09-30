// Subtitle overlay: a shadow DOM host inside #movie_player, so it follows
// fullscreen / theatre mode and YouTube's CSS cannot reach it (WP08 §1).
import type { Settings, SubtitlePosition } from "../shared/settings";
import type { View } from "./overlay-state";

const CSS = `
:host { all: initial; }
.wrap {
  position: absolute; left: var(--center, 50%); bottom: var(--bottom, 8%);
  transform: translateX(-50%); width: 88%;
  display: flex; flex-direction: column; align-items: center; gap: 0.2em;
  pointer-events: none; /* clicks go to the player, except on the subtitles themselves */
  font-family: "Noto Sans JP", "Hiragino Sans", "Yu Gothic UI", system-ui, sans-serif;
  font-size: var(--size, 24px); line-height: 1.35; z-index: 60;
  transition: bottom 0.15s ease-out;
}
.box {
  pointer-events: auto; cursor: grab; user-select: none; touch-action: none;
  position: relative; max-width: 100%; padding: 0.2em 0.6em; border-radius: 0.3em;
  background: rgba(8, 8, 8, var(--opacity, 0.6)); color: #fff; text-align: center;
  text-shadow: 0 0 2px #000, 0 0 4px #000;
}
.box:empty, .line:empty { display: none; }
.prev { font-size: 0.72em; opacity: 0.75; }
.ja .unstable { color: #b8b8b8; }
.en { font-size: 0.92em; }
.en.streaming { font-style: italic; color: #e6e6e6; }
.banner { font-size: 0.7em; color: #ffd166; }
.box.dragging { cursor: grabbing; outline: 1px dashed rgba(255, 255, 255, 0.6); }
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
  private pos: SubtitlePosition | null;
  private dragging = false;

  constructor(
    settings: Settings,
    private readonly onMoved: (pos: SubtitlePosition | null) => void,
    private readonly onClickThrough: () => void,
  ) {
    this.settings = settings;
    this.pos = settings.position;
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
    this.box.title = "live-subs : glisser pour déplacer, double-clic pour replacer";
    this.box.append(this.ja, this.en, this.banner);
    this.wrap.append(this.prev, this.box);
    this.hud = el("div", "hud");
    this.root.append(style, this.wrap, this.hud);
    this.enableDrag();
  }

  attach(player: HTMLElement): void {
    if (this.host.parentElement !== player) player.appendChild(this.host);
  }

  detach(): void {
    this.host.remove();
  }

  setSettings(s: Settings): void {
    this.settings = s;
    if (!this.dragging) this.pos = s.position;
  }

  render(v: View, controlsVisible: boolean): void {
    const s = this.settings;
    const height = this.host.parentElement?.clientHeight ?? 720;
    // ~3.4 % of the player height: 24 px at 720p, 37 px in 1080p fullscreen, 73 px in 4K.
    this.wrap.style.setProperty("--size", `${Math.max(14, height * 0.034 * s.fontScale)}px`);
    this.wrap.style.setProperty("--opacity", String(s.opacity));
    if (!this.dragging) this.applyPosition(controlsVisible);

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

  private applyPosition(controlsVisible: boolean): void {
    const bottom = this.pos ? this.pos.bottomPct : controlsVisible ? 14 : 8;
    this.wrap.style.setProperty("--bottom", `${bottom}%`);
    this.wrap.style.setProperty("--center", `${this.pos?.centerPct ?? 50}%`);
  }

  /**
   * Drag the subtitles anywhere on the player. A click that does not move is
   * passed to the player (play / pause), so the subtitles never steal it.
   */
  private enableDrag(): void {
    const box = this.box;
    box.addEventListener("pointerdown", (e: PointerEvent) => {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      const player = this.host.getBoundingClientRect();
      const boxRect = box.getBoundingClientRect();
      const grab = { dx: e.clientX - (boxRect.left + boxRect.width / 2), dy: boxRect.bottom - e.clientY };
      const start = { x: e.clientX, y: e.clientY };
      let moved = false;
      box.setPointerCapture(e.pointerId);
      const move = (ev: PointerEvent) => {
        if (!moved && Math.hypot(ev.clientX - start.x, ev.clientY - start.y) < 4) return;
        moved = true;
        this.dragging = true;
        box.classList.add("dragging");
        this.pos = positionFromPointer(player, ev.clientX, ev.clientY, grab);
        this.wrap.style.setProperty("--bottom", `${this.pos.bottomPct}%`);
        this.wrap.style.setProperty("--center", `${this.pos.centerPct}%`);
      };
      const up = () => {
        box.removeEventListener("pointermove", move);
        box.removeEventListener("pointerup", up);
        box.removeEventListener("pointercancel", up);
        box.classList.remove("dragging");
        this.dragging = false;
        if (moved) this.onMoved(this.pos);
        else this.onClickThrough();
      };
      box.addEventListener("pointermove", move);
      box.addEventListener("pointerup", up);
      box.addEventListener("pointercancel", up);
    });
    box.addEventListener("click", (e) => e.stopPropagation());
    box.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      this.pos = null;
      this.onMoved(null);
    });
  }
}

/**
 * Where the subtitles go when the pointer is at (x, y), keeping the point of the
 * box that was grabbed under the pointer. Percent of the player, clamped inside it.
 */
export function positionFromPointer(
  player: { left: number; bottom: number; width: number; height: number },
  x: number,
  y: number,
  grab: { dx: number; dy: number },
): SubtitlePosition {
  const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
  const centerPct = ((x - grab.dx - player.left) / player.width) * 100;
  const bottomPct = ((player.bottom - (y + grab.dy)) / player.height) * 100;
  return {
    centerPct: Math.round(clamp(centerPct, 10, 90) * 10) / 10,
    bottomPct: Math.round(clamp(bottomPct, 0, 90) * 10) / 10,
  };
}

function el(tag: string, cls: string, text?: string): HTMLElement {
  const node = document.createElement(tag);
  node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
