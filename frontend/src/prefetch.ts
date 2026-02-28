/**
 * Consume prefetched API promises started in index.html before React loaded.
 *
 * Each key is consumed at most once — the first caller gets the promise,
 * subsequent callers get undefined and should fall back to a normal fetch.
 */

import type { AppSettings, Channel, Contact, RadioConfig, UnreadCounts } from './types';

interface PrefetchMap {
  config?: Promise<RadioConfig>;
  settings?: Promise<AppSettings>;
  channels?: Promise<Channel[]>;
  contacts?: Promise<Contact[]>;
  unreads?: Promise<UnreadCounts>;
  undecryptedCount?: Promise<{ count: number }>;
}

const store: PrefetchMap = (window as unknown as { __prefetch?: PrefetchMap }).__prefetch ?? {};

type PrefetchResolved<K extends keyof PrefetchMap> =
  PrefetchMap[K] extends Promise<infer T> ? T : never;

/** Take a prefetched promise (consumed once, then gone). */
export function takePrefetch<K extends keyof PrefetchMap>(key: K): PrefetchMap[K] {
  const p = store[key];
  delete store[key];
  return p;
}

/**
 * Use prefetched data when available. If prefetch failed or was absent, run
 * the provided fallback fetcher.
 */
export async function takePrefetchOrFetch<K extends keyof PrefetchMap>(
  key: K,
  fallback: () => Promise<PrefetchResolved<K>>
): Promise<PrefetchResolved<K>> {
  const prefetched = takePrefetch(key);
  if (!prefetched) {
    return fallback();
  }

  try {
    return await prefetched;
  } catch (err) {
    console.warn(`Prefetch for "${String(key)}" failed, falling back to live fetch.`, err);
    return fallback();
  }
}
