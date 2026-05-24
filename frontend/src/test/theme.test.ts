import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  FOLLOW_OS_THEME_ID,
  THEMES,
  applyTheme,
  getEffectiveTheme,
  getSavedTheme,
} from '../utils/theme';

const originalMatchMedia = globalThis.matchMedia;

function stubPrefersLight(isLight: boolean) {
  Object.defineProperty(globalThis, 'matchMedia', {
    configurable: true,
    value: (query: string) => ({
      matches: query.includes('light') ? isLight : !isLight,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

describe('theme module', () => {
  beforeEach(() => {
    localStorage.clear();
    delete document.documentElement.dataset.theme;
  });

  afterEach(() => {
    globalThis.matchMedia = originalMatchMedia;
  });

  it('exposes an OS-following theme in the selectable list', () => {
    const followOS = THEMES.find((t) => t.id === FOLLOW_OS_THEME_ID);
    expect(followOS).toBeDefined();
    expect(followOS?.name).toBeTruthy();
  });

  it('applyTheme("follow-os") resolves to light when OS prefers light', () => {
    stubPrefersLight(true);

    applyTheme(FOLLOW_OS_THEME_ID);

    // Saved value is the follow-os preference, but the DOM reflects the resolved theme.
    expect(localStorage.getItem('remoteterm-theme')).toBe(FOLLOW_OS_THEME_ID);
    expect(getSavedTheme()).toBe(FOLLOW_OS_THEME_ID);
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(getEffectiveTheme()).toBe('light');
  });

  it('applyTheme("follow-os") resolves to original (dark) when OS prefers dark', () => {
    stubPrefersLight(false);

    applyTheme(FOLLOW_OS_THEME_ID);

    expect(localStorage.getItem('remoteterm-theme')).toBe(FOLLOW_OS_THEME_ID);
    // Original has no data-theme attribute, it's the default.
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(getEffectiveTheme()).toBe('original');
  });

  it('applyTheme updates the PWA meta theme-color to match the effective theme', () => {
    // Seed the meta tag (jsdom base template has none).
    const meta = document.createElement('meta');
    meta.setAttribute('name', 'theme-color');
    meta.setAttribute('content', '#000000');
    document.head.appendChild(meta);

    stubPrefersLight(true);
    applyTheme(FOLLOW_OS_THEME_ID);
    // Light theme's metaThemeColor
    expect(meta.getAttribute('content')).toBe('#F8F7F4');

    stubPrefersLight(false);
    applyTheme(FOLLOW_OS_THEME_ID);
    // Original theme's metaThemeColor
    expect(meta.getAttribute('content')).toBe('#111419');

    meta.remove();
  });
});
