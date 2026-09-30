// Everything that depends on YouTube's DOM lives here (selectors change: one place to fix).

export const PLAYER_SELECTOR = "#movie_player";
export const VIDEO_SELECTOR = "#movie_player video.html5-main-video";

export interface PageInfo {
  videoId: string | null;
  channelId: string | null;
  title: string | null;
}

/** youtube.com/watch?v=ID and youtube.com/live/ID. */
export function videoIdFromUrl(href: string): string | null {
  try {
    const url = new URL(href);
    if (url.pathname === "/watch") return url.searchParams.get("v");
    const live = /^\/live\/([\w-]{6,})/.exec(url.pathname);
    return live?.[1] ?? null;
  } catch {
    return null;
  }
}

export function isWatchPage(href: string): boolean {
  return videoIdFromUrl(href) !== null;
}

function channelId(): string | null {
  const meta = document.querySelector<HTMLMetaElement>('meta[itemprop="channelId"], meta[itemprop="identifier"]');
  if (meta?.content?.startsWith("UC")) return meta.content;
  const link = document.querySelector<HTMLAnchorElement>(
    'ytd-watch-metadata ytd-channel-name a[href*="/channel/UC"], #owner a[href*="/channel/UC"]',
  );
  const m = link ? /\/channel\/(UC[\w-]+)/.exec(link.href) : null;
  return m?.[1] ?? null;
}

export function pageInfo(): PageInfo {
  const title =
    document.querySelector("ytd-watch-metadata h1")?.textContent?.trim() ||
    document.title.replace(/ - YouTube$/, "") ||
    null;
  return { videoId: videoIdFromUrl(location.href), channelId: channelId(), title };
}

export function findPlayer(): HTMLElement | null {
  return document.querySelector<HTMLElement>(PLAYER_SELECTOR);
}

export function findVideo(): HTMLVideoElement | null {
  return document.querySelector<HTMLVideoElement>(VIDEO_SELECTOR);
}

export function isAdShowing(): boolean {
  return findPlayer()?.classList.contains("ad-showing") ?? false;
}

/** Controls visible (not auto-hidden): subtitles move up above them. */
export function controlsVisible(): boolean {
  const p = findPlayer();
  return !!p && !p.classList.contains("ytp-autohide");
}

/**
 * Calls `cb` whenever YouTube swaps the <video> element or the ad state flips.
 * YouTube recreates nodes freely, hence a MutationObserver on the whole player.
 */
export function watchPlayer(cb: (video: HTMLVideoElement | null, ad: boolean) => void): () => void {
  let lastVideo: HTMLVideoElement | null = null;
  let lastAd = false;
  const check = () => {
    const video = findVideo();
    const ad = isAdShowing();
    if (video !== lastVideo || ad !== lastAd) {
      lastVideo = video;
      lastAd = ad;
      cb(video, ad);
    }
  };
  const obs = new MutationObserver(check);
  obs.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
  check();
  return () => obs.disconnect();
}
