import { describe, expect, it } from 'vitest';
import { mvToPercent, formatBatteryLabel } from '../utils/batteryDisplay';

describe('mvToPercent', () => {
  it('clamps to 100 above table ceiling', () => {
    expect(mvToPercent(4500)).toBe(100);
    expect(mvToPercent(4190)).toBe(100);
  });

  it('clamps to 0 below table floor', () => {
    expect(mvToPercent(3100)).toBe(0);
    expect(mvToPercent(2800)).toBe(0);
  });

  it('returns exact table values at boundaries', () => {
    expect(mvToPercent(4050)).toBe(90);
    expect(mvToPercent(3630)).toBe(40);
  });

  it('interpolates between table entries', () => {
    // Midpoint between 3630 (40%) and 3720 (50%) = 3675 → ~45%
    const mid = mvToPercent(3675);
    expect(mid).toBeGreaterThan(40);
    expect(mid).toBeLessThan(50);
  });
});

describe('formatBatteryLabel', () => {
  it('returns null when both toggles are off', () => {
    expect(formatBatteryLabel(4050, false, false)).toBeNull();
  });

  it('returns percentage only', () => {
    expect(formatBatteryLabel(4050, true, false)).toBe('90%');
  });

  it('returns voltage only', () => {
    expect(formatBatteryLabel(4050, false, true)).toBe('4050mV');
  });

  it('returns combined when both enabled', () => {
    expect(formatBatteryLabel(4050, true, true)).toBe('90% (4050mV)');
  });
});
