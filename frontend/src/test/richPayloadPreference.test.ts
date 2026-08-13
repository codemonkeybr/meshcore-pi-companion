import { beforeEach, describe, expect, it } from 'vitest';

import {
  RENDER_RICH_PAYLOADS_KEY,
  getSavedRenderRichPayloads,
  setSavedRenderRichPayloads,
} from '../utils/richPayloadPreference';

describe('richPayloadPreference utilities', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('defaults to off when unset', () => {
    expect(getSavedRenderRichPayloads()).toBe(false);
  });

  it('returns true when enabled', () => {
    localStorage.setItem(RENDER_RICH_PAYLOADS_KEY, 'true');
    expect(getSavedRenderRichPayloads()).toBe(true);
  });

  it('treats any non-"true" value as off', () => {
    localStorage.setItem(RENDER_RICH_PAYLOADS_KEY, 'yes');
    expect(getSavedRenderRichPayloads()).toBe(false);
  });

  it('persists when enabled and clears the key when disabled', () => {
    setSavedRenderRichPayloads(true);
    expect(localStorage.getItem(RENDER_RICH_PAYLOADS_KEY)).toBe('true');

    setSavedRenderRichPayloads(false);
    expect(localStorage.getItem(RENDER_RICH_PAYLOADS_KEY)).toBeNull();
  });
});
