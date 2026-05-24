export const FONT_SCALE_KEY = 'remoteterm-font-scale';
export const DEFAULT_FONT_SCALE = 100;
export const MIN_FONT_SCALE = 25;
export const MAX_FONT_SCALE = 400;
export const FONT_SCALE_SLIDER_STEP = 5;

function normalizeFontScale(scale: number): number {
  if (!Number.isFinite(scale)) {
    return DEFAULT_FONT_SCALE;
  }

  const clamped = Math.min(MAX_FONT_SCALE, Math.max(MIN_FONT_SCALE, scale));
  return Number.parseFloat(clamped.toFixed(2));
}

export function getSavedFontScale(): number {
  try {
    const raw = localStorage.getItem(FONT_SCALE_KEY);
    if (raw === null) {
      return DEFAULT_FONT_SCALE;
    }

    return normalizeFontScale(Number.parseFloat(raw));
  } catch {
    return DEFAULT_FONT_SCALE;
  }
}

export function applyFontScale(scale: number): number {
  const normalized = normalizeFontScale(scale);

  if (typeof document !== 'undefined') {
    document.documentElement.style.fontSize = `${normalized}%`;
  }

  return normalized;
}

export function setSavedFontScale(scale: number): number {
  const normalized = applyFontScale(scale);

  try {
    if (normalized === DEFAULT_FONT_SCALE) {
      localStorage.removeItem(FONT_SCALE_KEY);
    } else {
      localStorage.setItem(FONT_SCALE_KEY, String(normalized));
    }
  } catch {
    // localStorage may be unavailable
  }

  return normalized;
}
