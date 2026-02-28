import { beforeEach, describe, expect, it, vi } from 'vitest';

interface PrefetchWindow extends Window {
  __prefetch?: unknown;
}

function setPrefetchStore(store: unknown) {
  (window as PrefetchWindow).__prefetch = store;
}

describe('takePrefetchOrFetch', () => {
  beforeEach(() => {
    vi.resetModules();
    delete (window as PrefetchWindow).__prefetch;
    vi.restoreAllMocks();
  });

  it('uses prefetched data once, then falls back', async () => {
    setPrefetchStore({
      undecryptedCount: Promise.resolve({ count: 7 }),
    });

    const { takePrefetchOrFetch } = await import('../prefetch');
    const fallback = vi.fn().mockResolvedValue({ count: 9 });

    await expect(takePrefetchOrFetch('undecryptedCount', fallback)).resolves.toEqual({ count: 7 });
    expect(fallback).not.toHaveBeenCalled();

    await expect(takePrefetchOrFetch('undecryptedCount', fallback)).resolves.toEqual({ count: 9 });
    expect(fallback).toHaveBeenCalledTimes(1);
  });

  it('falls back when prefetched promise rejects', async () => {
    const prefetchedFailure = Promise.reject(new Error('prefetch failed'));
    // Avoid unhandled rejection noise while the helper awaits the same promise.
    prefetchedFailure.catch(() => undefined);
    setPrefetchStore({
      undecryptedCount: prefetchedFailure,
    });

    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const { takePrefetchOrFetch } = await import('../prefetch');
    const fallback = vi.fn().mockResolvedValue({ count: 11 });

    await expect(takePrefetchOrFetch('undecryptedCount', fallback)).resolves.toEqual({ count: 11 });
    expect(fallback).toHaveBeenCalledTimes(1);
    expect(warnSpy).toHaveBeenCalledTimes(1);
  });
});
