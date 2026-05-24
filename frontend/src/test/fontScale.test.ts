import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  applyFontScale,
  DEFAULT_FONT_SCALE,
  FONT_SCALE_KEY,
  MAX_FONT_SCALE,
  MIN_FONT_SCALE,
  getSavedFontScale,
  setSavedFontScale,
} from '../utils/fontScale';

describe('fontScale utilities', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.style.fontSize = '';
  });

  afterEach(() => {
    document.documentElement.style.fontSize = '';
  });

  it('defaults to 100% when nothing is saved', () => {
    expect(getSavedFontScale()).toBe(DEFAULT_FONT_SCALE);
  });

  it('reads a saved scale from localStorage', () => {
    localStorage.setItem(FONT_SCALE_KEY, '135');

    expect(getSavedFontScale()).toBe(135);
  });

  it('falls back to the default when the saved value is invalid', () => {
    localStorage.setItem(FONT_SCALE_KEY, 'giant');

    expect(getSavedFontScale()).toBe(DEFAULT_FONT_SCALE);
  });

  it('applies the scale to the document root', () => {
    expect(applyFontScale(150)).toBe(150);
    expect(document.documentElement.style.fontSize).toBe('150%');
  });

  it('stores non-default values and applies them immediately', () => {
    expect(setSavedFontScale(137.5)).toBe(137.5);
    expect(localStorage.getItem(FONT_SCALE_KEY)).toBe('137.5');
    expect(document.documentElement.style.fontSize).toBe('137.5%');
  });

  it('removes the saved value when returning to the default scale', () => {
    localStorage.setItem(FONT_SCALE_KEY, '150');

    expect(setSavedFontScale(DEFAULT_FONT_SCALE)).toBe(DEFAULT_FONT_SCALE);
    expect(localStorage.getItem(FONT_SCALE_KEY)).toBeNull();
    expect(document.documentElement.style.fontSize).toBe('100%');
  });

  it('clamps saved and applied values to the supported range', () => {
    localStorage.setItem(FONT_SCALE_KEY, '900');
    expect(getSavedFontScale()).toBe(MAX_FONT_SCALE);

    expect(setSavedFontScale(5)).toBe(MIN_FONT_SCALE);
    expect(localStorage.getItem(FONT_SCALE_KEY)).toBe(String(MIN_FONT_SCALE));
    expect(document.documentElement.style.fontSize).toBe(`${MIN_FONT_SCALE}%`);
  });
});
