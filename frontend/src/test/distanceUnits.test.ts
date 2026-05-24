import { beforeEach, describe, expect, it } from 'vitest';

import {
  DISTANCE_UNIT_KEY,
  getSavedDistanceUnit,
  setSavedDistanceUnit,
} from '../utils/distanceUnits';

describe('distanceUnits utilities', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('defaults to metric when unset', () => {
    expect(getSavedDistanceUnit()).toBe('metric');
  });

  it('returns the stored unit when valid', () => {
    localStorage.setItem(DISTANCE_UNIT_KEY, 'metric');
    expect(getSavedDistanceUnit()).toBe('metric');
  });

  it('falls back to metric for invalid stored values', () => {
    localStorage.setItem(DISTANCE_UNIT_KEY, 'parsecs');
    expect(getSavedDistanceUnit()).toBe('metric');
  });

  it('stores the selected distance unit', () => {
    setSavedDistanceUnit('smoots');
    expect(localStorage.getItem(DISTANCE_UNIT_KEY)).toBe('smoots');
  });
});
