import { describe, it, expect, vi, afterEach } from 'vitest';
import { formatDuration, formatClockDrift } from '../components/RepeaterDashboard';

describe('formatDuration', () => {
  it('formats seconds only', () => {
    expect(formatDuration(0)).toBe('0s');
    expect(formatDuration(30)).toBe('30s');
    expect(formatDuration(59)).toBe('59s');
  });

  it('formats minutes only', () => {
    expect(formatDuration(60)).toBe('1m');
    expect(formatDuration(300)).toBe('5m');
    expect(formatDuration(3540)).toBe('59m');
  });

  it('formats hours and minutes', () => {
    expect(formatDuration(3600)).toBe('1h');
    expect(formatDuration(3660)).toBe('1h1m');
    expect(formatDuration(7200)).toBe('2h');
    expect(formatDuration(7260)).toBe('2h1m');
  });

  it('formats days', () => {
    expect(formatDuration(86400)).toBe('1d');
    expect(formatDuration(86400 + 3600)).toBe('1d1h');
    expect(formatDuration(86400 + 60)).toBe('1d1m');
    expect(formatDuration(86400 + 3600 + 60)).toBe('1d1h1m');
    expect(formatDuration(172800)).toBe('2d');
  });

  it('formats multi-day durations', () => {
    expect(formatDuration(3 * 86400 + 12 * 3600 + 30 * 60)).toBe('3d12h30m');
  });
});

describe('formatClockDrift', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('parses firmware format HH:MM - D/M/YYYY UTC', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-01-09T12:30:00Z'));

    const result = formatClockDrift('12:30 - 9/1/2025 UTC');
    expect(result.isLarge).toBe(false);
    expect(result.text).toBe('0s');
  });

  it('parses firmware format with seconds HH:MM:SS - D/M/YYYY', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-06-15T08:00:00Z'));

    const result = formatClockDrift('08:00:00 - 15/6/2025 UTC');
    expect(result.isLarge).toBe(false);
    expect(result.text).toBe('0s');
  });

  it('reports large drift (>24h)', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-01-11T12:30:00Z'));

    const result = formatClockDrift('12:30 - 9/1/2025 UTC');
    expect(result.isLarge).toBe(true);
    expect(result.text).toBe('>24 hours!');
  });

  it('handles invalid date strings', () => {
    const result = formatClockDrift('not a date');
    expect(result.text).toBe('(invalid)');
    expect(result.isLarge).toBe(false);
  });

  it('formats multi-unit drift', () => {
    vi.useFakeTimers();
    // 1h30m5s drift
    vi.setSystemTime(new Date('2025-01-09T14:00:05Z'));

    const result = formatClockDrift('12:30 - 9/1/2025 UTC');
    expect(result.isLarge).toBe(false);
    expect(result.text).toBe('1h30m5s');
  });

  it('formats minutes and seconds drift', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-01-09T12:35:10Z'));

    const result = formatClockDrift('12:30 - 9/1/2025 UTC');
    expect(result.isLarge).toBe(false);
    expect(result.text).toBe('5m10s');
  });
});
