// Browser-local preference for showing the per-hop byte width (e.g. "2B")
// next to the hop-count badge on received messages. Pure display tweak, stored
// per-browser in localStorage. Off by default.

export const SHOW_PATH_HOP_WIDTH_KEY = 'remoteterm-show-path-hop-width';

export function getSavedShowPathHopWidth(): boolean {
  try {
    return localStorage.getItem(SHOW_PATH_HOP_WIDTH_KEY) === 'true';
  } catch {
    return false;
  }
}

export function setSavedShowPathHopWidth(enabled: boolean): void {
  try {
    if (enabled) {
      localStorage.setItem(SHOW_PATH_HOP_WIDTH_KEY, 'true');
    } else {
      localStorage.removeItem(SHOW_PATH_HOP_WIDTH_KEY);
    }
  } catch {
    // localStorage may be unavailable
  }
}
