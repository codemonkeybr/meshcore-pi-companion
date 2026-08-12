// Browser-local preference for rendering MeshCore Open rich-chat payloads
// (Giphy GIFs and emoji reactions) instead of their raw encoded text. This is
// a pure display tweak, stored per-browser in localStorage. GIF rendering
// fetches images from media.giphy.com, so it is off by default.

export const RENDER_RICH_PAYLOADS_KEY = 'remoteterm-render-rich-payloads';

export function getSavedRenderRichPayloads(): boolean {
  try {
    return localStorage.getItem(RENDER_RICH_PAYLOADS_KEY) === 'true';
  } catch {
    return false;
  }
}

export function setSavedRenderRichPayloads(enabled: boolean): void {
  try {
    if (enabled) {
      localStorage.setItem(RENDER_RICH_PAYLOADS_KEY, 'true');
    } else {
      localStorage.removeItem(RENDER_RICH_PAYLOADS_KEY);
    }
  } catch {
    // localStorage may be unavailable
  }
}
