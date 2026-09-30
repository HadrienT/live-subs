// Transcript panel (WP14): scrolling JA/EN list next to the live chat, search,
// click → seek. Rendered off the overlay's path (idle callback), so it adds no
// latency to the subtitles.
import type { Entry, Transcript } from "./history";

const CSS = `
:host { all: initial; }
details { font: 13px/1.45 "Noto Sans JP", system-ui, sans-serif; color: var(--fg, #0f0f0f);
  background: var(--bg, #fff); border: 1px solid rgba(128,128,128,.35); border-radius: 12px;
  margin: 0 0 12px; overflow: hidden; }
summary { cursor: pointer; padding: 8px 12px; font-weight: 600; user-select: none; }
.bar { display: flex; gap: 6px; padding: 0 12px 8px; }
input { flex: 1; font: inherit; padding: 4px 8px; border-radius: 6px;
  border: 1px solid rgba(128,128,128,.5); background: transparent; color: inherit; }
ol { list-style: none; margin: 0; padding: 0 0 8px; max-height: 360px; overflow-y: auto; }
li { padding: 4px 12px; cursor: pointer; }
li:hover { background: rgba(128,128,128,.12); }
.t { font: 11px ui-monospace, monospace; opacity: .6; margin-right: 6px; }
.en { opacity: .8; font-size: 12px; }
@media (prefers-color-scheme: dark) { details { --fg: #f1f1f1; --bg: #0f0f0f; } }
`;

function clock(t: number): string {
  const s = Math.max(0, Math.floor(t));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
}

export class TranscriptPanel {
  readonly host: HTMLElement;
  private readonly list: HTMLOListElement;
  private readonly search: HTMLInputElement;
  private readonly details: HTMLDetailsElement;
  private scheduled = false;
  private transcript: Transcript | null = null;

  constructor(private readonly seek: (t: number) => void) {
    this.host = document.createElement("div");
    this.host.id = "live-subs-panel";
    const root = this.host.attachShadow({ mode: "closed" });
    const style = document.createElement("style");
    style.textContent = CSS;
    this.details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "live-subs · transcript";
    const bar = document.createElement("div");
    bar.className = "bar";
    this.search = document.createElement("input");
    this.search.type = "search";
    this.search.placeholder = "Search / 検索";
    this.search.addEventListener("input", () => this.update());
    bar.append(this.search);
    this.list = document.createElement("ol");
    this.list.addEventListener("click", (e) => {
      const li = (e.target as HTMLElement).closest("li");
      const t = Number(li?.dataset["t"]);
      if (Number.isFinite(t)) this.seek(Math.max(0, t - 0.5));
    });
    this.details.addEventListener("toggle", () => this.update());
    this.details.append(summary, bar, this.list);
    root.append(style, this.details);
  }

  /** Next to the chat when there is a right column; nowhere otherwise (fullscreen). */
  attach(): void {
    const column = document.querySelector("#secondary-inner, #secondary");
    if (column && this.host.parentElement !== column) column.prepend(this.host);
  }

  detach(): void {
    this.host.remove();
  }

  setTranscript(t: Transcript | null): void {
    this.transcript = t;
    this.update();
  }

  update(): void {
    if (this.scheduled || !this.details.open) return;
    this.scheduled = true;
    const run = () => {
      this.scheduled = false;
      this.render();
    };
    if ("requestIdleCallback" in window) window.requestIdleCallback(run, { timeout: 500 });
    else setTimeout(run, 50);
  }

  private render(): void {
    const entries: Entry[] = this.transcript?.search(this.search.value) ?? [];
    const atBottom = this.list.scrollTop + this.list.clientHeight >= this.list.scrollHeight - 8;
    this.list.replaceChildren(
      ...entries.map((e) => {
        const li = document.createElement("li");
        li.dataset["t"] = String(e.t0);
        const t = document.createElement("span");
        t.className = "t";
        t.textContent = clock(e.t0);
        const en = document.createElement("div");
        en.className = "en";
        en.textContent = e.en;
        li.append(t, document.createTextNode(e.ja), en);
        return li;
      }),
    );
    if (atBottom) this.list.scrollTop = this.list.scrollHeight;
  }
}
