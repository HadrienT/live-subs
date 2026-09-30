// Options page: server URL, token, look, capture fallbacks. Saved in storage.local.
import { loadSettings, saveSettings, type Settings } from "../shared/settings";

const form = document.getElementById("form") as HTMLFormElement;
const saved = document.getElementById("saved") as HTMLElement;

type Field = HTMLInputElement | HTMLSelectElement;

async function fill(): Promise<void> {
  const s = await loadSettings();
  for (const el of Array.from(form.elements) as Field[]) {
    if (!el.name) continue;
    const value = s[el.name as keyof Settings];
    if (el instanceof HTMLInputElement && el.type === "checkbox") el.checked = Boolean(value);
    else el.value = String(value ?? "");
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const patch: Record<string, unknown> = {};
  for (const el of Array.from(form.elements) as Field[]) {
    if (!el.name) continue;
    if (el instanceof HTMLInputElement && el.type === "checkbox") patch[el.name] = el.checked;
    else if (el instanceof HTMLInputElement && (el.type === "range" || el.type === "number")) patch[el.name] = Number(el.value);
    else patch[el.name] = el.value.trim();
  }
  const url = String(patch["serverUrl"] ?? "");
  if (!/^wss?:\/\/[^/]+/.test(url)) {
    alert("L'URL du serveur doit commencer par ws:// ou wss://");
    return;
  }
  void saveSettings(patch as Partial<Settings>).then(() => {
    saved.hidden = false;
    setTimeout(() => (saved.hidden = true), 1500);
  });
});

void fill();
