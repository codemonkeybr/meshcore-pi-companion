import { describe, it, expect } from 'vitest';
import {
  UNSCOPED_OVERRIDE_MARKER,
  isUnscopedMarker,
  stripRegionScopePrefix,
  floodScopeOverrideLabel,
} from '../utils/regionScope';

describe('regionScope helpers', () => {
  describe('isUnscopedMarker', () => {
    it('recognizes the unscoped marker', () => {
      expect(isUnscopedMarker(UNSCOPED_OVERRIDE_MARKER)).toBe(true);
      expect(isUnscopedMarker('*')).toBe(true);
    });

    it('rejects regions, null, undefined, and empty', () => {
      expect(isUnscopedMarker('#Esperance')).toBe(false);
      expect(isUnscopedMarker('Esperance')).toBe(false);
      expect(isUnscopedMarker(null)).toBe(false);
      expect(isUnscopedMarker(undefined)).toBe(false);
      expect(isUnscopedMarker('')).toBe(false);
    });
  });

  describe('stripRegionScopePrefix', () => {
    it('strips a leading hash', () => {
      expect(stripRegionScopePrefix('#Esperance')).toBe('Esperance');
    });

    it('leaves unprefixed values (incl. the marker) unchanged', () => {
      expect(stripRegionScopePrefix('Esperance')).toBe('Esperance');
      expect(stripRegionScopePrefix('*')).toBe('*');
    });

    it('returns empty string for nullish input', () => {
      expect(stripRegionScopePrefix(null)).toBe('');
      expect(stripRegionScopePrefix(undefined)).toBe('');
      expect(stripRegionScopePrefix('')).toBe('');
    });
  });

  describe('floodScopeOverrideLabel', () => {
    it('returns null when there is no override (inherit)', () => {
      expect(floodScopeOverrideLabel(null)).toBeNull();
      expect(floodScopeOverrideLabel(undefined)).toBeNull();
      expect(floodScopeOverrideLabel('')).toBeNull();
    });

    it('maps the unscoped marker to a friendly label, not a bare "*"', () => {
      expect(floodScopeOverrideLabel(UNSCOPED_OVERRIDE_MARKER)).toBe('unscoped');
      expect(floodScopeOverrideLabel('*')).toBe('unscoped');
    });

    it('passes a region name through unchanged', () => {
      expect(floodScopeOverrideLabel('#Esperance')).toBe('#Esperance');
      expect(floodScopeOverrideLabel('Esperance')).toBe('Esperance');
    });
  });
});
