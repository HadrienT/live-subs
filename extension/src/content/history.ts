// Transcript of the session (WP14): JA/EN pairs in media time, search, SRT/VTT export.

export interface Entry {
  segId: number;
  t0: number;
  t1: number;
  ja: string;
  en: string;
}

export interface SessionRecord {
  videoId: string;
  title: string | null;
  startedAt: number; // Date.now()
  entries: Entry[];
}

export class Transcript {
  private readonly bySeg = new Map<number, Entry>();

  constructor(readonly record: SessionRecord) {
    for (const e of record.entries) this.bySeg.set(e.segId, e);
  }

  get entries(): Entry[] {
    return [...this.bySeg.values()].sort((a, b) => a.t0 - b.t0);
  }

  onFinal(segId: number, ja: string, t0: number, t1: number): void {
    if (!ja) {
      this.bySeg.delete(segId);
      return;
    }
    const prev = this.bySeg.get(segId);
    this.bySeg.set(segId, { segId, t0, t1, ja, en: prev?.en ?? "" });
  }

  onTranslation(segId: number, en: string): void {
    const e = this.bySeg.get(segId);
    if (e) e.en = en;
  }

  search(query: string): Entry[] {
    const q = query.trim().toLowerCase();
    if (!q) return this.entries;
    return this.entries.filter((e) => e.ja.toLowerCase().includes(q) || e.en.toLowerCase().includes(q));
  }

  snapshot(): SessionRecord {
    return { ...this.record, entries: this.entries };
  }
}

// ------------------------------------------------------------------ export

export type ExportLang = "ja" | "en" | "both";

function stamp(seconds: number, sep: "," | "."): string {
  const ms = Math.max(0, Math.round(seconds * 1000));
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)}${sep}${pad(ms % 1000, 3)}`;
}

function text(e: Entry, lang: ExportLang): string {
  if (lang === "ja") return e.ja;
  if (lang === "en") return e.en;
  return e.en ? `${e.ja}\n${e.en}` : e.ja;
}

/**
 * Cues keep the timing of the live (media time of the capture). On the VOD of
 * the same stream the offset is constant: shift it in the player if needed.
 * A cue ends at the next cue's start at the latest, so cues never overlap.
 */
function cues(entries: Entry[], lang: ExportLang): { start: number; end: number; body: string }[] {
  const kept = entries.filter((e) => text(e, lang).trim());
  return kept.map((e, i) => {
    const next = kept[i + 1];
    const end = Math.max(e.t1 + 1.0, e.t0 + 1.5);
    return { start: e.t0, end: next ? Math.min(end, next.t0) : end, body: text(e, lang) };
  });
}

export function toSrt(entries: Entry[], lang: ExportLang): string {
  return cues(entries, lang)
    .map((c, i) => `${i + 1}\n${stamp(c.start, ",")} --> ${stamp(c.end, ",")}\n${c.body}\n`)
    .join("\n");
}

export function toVtt(entries: Entry[], lang: ExportLang): string {
  const body = cues(entries, lang)
    .map((c) => `${stamp(c.start, ".")} --> ${stamp(c.end, ".")}\n${c.body}\n`)
    .join("\n");
  return `WEBVTT\n\n${body}`;
}

// ------------------------------------------------------------------ storage

const KEY = "history";

export async function loadHistory(): Promise<SessionRecord[]> {
  const stored = (await browser.storage.local.get(KEY)) as { history?: SessionRecord[] };
  return stored.history ?? [];
}

/** Keeps the `keep` most recent sessions (storage.local quota). */
export async function saveSession(rec: SessionRecord, keep: number): Promise<void> {
  const all = (await loadHistory()).filter((r) => !(r.videoId === rec.videoId && r.startedAt === rec.startedAt));
  all.push(rec);
  all.sort((a, b) => a.startedAt - b.startedAt);
  await browser.storage.local.set({ [KEY]: all.slice(-keep) });
}
