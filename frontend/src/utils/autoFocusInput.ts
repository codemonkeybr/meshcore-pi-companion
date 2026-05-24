const KEY = 'remoteterm-auto-focus-input';

export function getAutoFocusInputEnabled(): boolean {
  try {
    const raw = localStorage.getItem(KEY);
    return raw === null || raw !== 'false';
  } catch {
    return true;
  }
}

export function setAutoFocusInputEnabled(enabled: boolean): void {
  try {
    if (enabled) {
      localStorage.removeItem(KEY);
    } else {
      localStorage.setItem(KEY, 'false');
    }
  } catch {
    // localStorage may be unavailable
  }
}

/**
 * Returns true when auto-focus should fire: the setting is enabled
 * AND the viewport is wide enough that focusing won't summon a
 * mobile keyboard (matches the md: Tailwind breakpoint).
 */
export function shouldAutoFocusInput(): boolean {
  return getAutoFocusInputEnabled() && window.innerWidth >= 768;
}
