import { renderHook, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useEntranceSettled } from '../hooks/useEntranceSettled';

describe('useEntranceSettled', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('stays false while closed', () => {
    const { result } = renderHook(() => useEntranceSettled(false, 550));
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current).toBe(false);
  });

  it('flips to true only after the delay once open', () => {
    const { result } = renderHook(() => useEntranceSettled(true, 550));
    expect(result.current).toBe(false);

    act(() => {
      vi.advanceTimersByTime(549);
    });
    expect(result.current).toBe(false);

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toBe(true);
  });

  it('does not settle if closed before the delay elapses', () => {
    const { result, rerender } = renderHook(({ open }) => useEntranceSettled(open, 550), {
      initialProps: { open: true },
    });

    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(result.current).toBe(false);

    // Close before the timer fires — the pending timeout must be cancelled.
    rerender({ open: false });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current).toBe(false);
  });

  it('resets to false when reopened after having settled', () => {
    const { result, rerender } = renderHook(({ open }) => useEntranceSettled(open, 550), {
      initialProps: { open: true },
    });

    act(() => {
      vi.advanceTimersByTime(550);
    });
    expect(result.current).toBe(true);

    rerender({ open: false });
    expect(result.current).toBe(false);

    // Reopening restarts the deferral rather than showing charts immediately.
    rerender({ open: true });
    expect(result.current).toBe(false);
    act(() => {
      vi.advanceTimersByTime(550);
    });
    expect(result.current).toBe(true);
  });
});
