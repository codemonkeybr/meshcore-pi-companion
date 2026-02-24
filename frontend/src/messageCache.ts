/**
 * LRU message cache for recently-visited conversations.
 *
 * Uses Map insertion-order semantics: the most recently used entry
 * is always at the end. Eviction removes the first (least-recently-used) entry.
 *
 * Cache size: 20 conversations, 200 messages each (~2.4MB worst case).
 */

import type { Message, MessagePath } from './types';

export const MAX_CACHED_CONVERSATIONS = 20;
export const MAX_MESSAGES_PER_ENTRY = 200;

export interface CacheEntry {
  messages: Message[];
  seenContent: Set<string>;
  hasOlderMessages: boolean;
}

const cache = new Map<string, CacheEntry>();

/** Get a cached entry and promote it to most-recently-used. */
export function get(id: string): CacheEntry | undefined {
  const entry = cache.get(id);
  if (!entry) return undefined;
  // Promote to MRU: delete and re-insert
  cache.delete(id);
  cache.set(id, entry);
  return entry;
}

/** Insert or update an entry at MRU position, evicting LRU if over capacity. */
export function set(id: string, entry: CacheEntry): void {
  // Trim to most recent messages to bound memory
  if (entry.messages.length > MAX_MESSAGES_PER_ENTRY) {
    const trimmed = [...entry.messages]
      .sort((a, b) => b.received_at - a.received_at)
      .slice(0, MAX_MESSAGES_PER_ENTRY);
    entry = { ...entry, messages: trimmed, hasOlderMessages: true };
  }
  // Remove first so re-insert moves to end
  cache.delete(id);
  cache.set(id, entry);
  // Evict LRU (first entry) if over capacity
  if (cache.size > MAX_CACHED_CONVERSATIONS) {
    const lruKey = cache.keys().next().value as string;
    cache.delete(lruKey);
  }
}

/** Add a message to a cached conversation with dedup. Returns true if new, false if duplicate. */
export function addMessage(id: string, msg: Message, contentKey: string): boolean {
  const entry = cache.get(id);
  if (!entry) {
    // Auto-create a minimal entry for never-visited conversations
    cache.set(id, {
      messages: [msg],
      seenContent: new Set([contentKey]),
      hasOlderMessages: true,
    });
    // Evict LRU if over capacity
    if (cache.size > MAX_CACHED_CONVERSATIONS) {
      const lruKey = cache.keys().next().value as string;
      cache.delete(lruKey);
    }
    return true;
  }
  if (entry.seenContent.has(contentKey)) return false;
  if (entry.messages.some((m) => m.id === msg.id)) return false;
  entry.seenContent.add(contentKey);
  entry.messages = [...entry.messages, msg];
  // Trim if over limit (drop oldest by received_at)
  if (entry.messages.length > MAX_MESSAGES_PER_ENTRY) {
    entry.messages = [...entry.messages]
      .sort((a, b) => b.received_at - a.received_at)
      .slice(0, MAX_MESSAGES_PER_ENTRY);
  }
  return true;
}

/** Scan all cached entries for a message ID and update its ack/paths. */
export function updateAck(messageId: number, ackCount: number, paths?: MessagePath[]): void {
  for (const entry of cache.values()) {
    const idx = entry.messages.findIndex((m) => m.id === messageId);
    if (idx >= 0) {
      const current = entry.messages[idx];
      const updated = [...entry.messages];
      updated[idx] = {
        ...current,
        acked: Math.max(current.acked, ackCount),
        ...(paths !== undefined && paths.length >= (current.paths?.length ?? 0) && { paths }),
      };
      entry.messages = updated;
      return; // Message IDs are unique, stop after first match
    }
  }
}

/**
 * Compare fetched messages against current state.
 * Returns merged array if there are differences (new messages or ack changes),
 * or null if the cache is already consistent (happy path — no rerender needed).
 * Preserves any older paginated messages not present in the fetched page.
 */
export function reconcile(current: Message[], fetched: Message[]): Message[] | null {
  const currentById = new Map<number, { acked: number; pathsLen: number; text: string }>();
  for (const m of current) {
    currentById.set(m.id, { acked: m.acked, pathsLen: m.paths?.length ?? 0, text: m.text });
  }

  let needsUpdate = false;
  for (const m of fetched) {
    const cur = currentById.get(m.id);
    if (
      !cur ||
      cur.acked !== m.acked ||
      cur.pathsLen !== (m.paths?.length ?? 0) ||
      cur.text !== m.text
    ) {
      needsUpdate = true;
      break;
    }
  }
  if (!needsUpdate) return null;

  // Merge: fresh recent page + any older paginated messages not in the fetch
  const fetchedIds = new Set(fetched.map((m) => m.id));
  const olderMessages = current.filter((m) => !fetchedIds.has(m.id));
  return [...fetched, ...olderMessages];
}

/** Evict a specific conversation from the cache. */
export function remove(id: string): void {
  cache.delete(id);
}

/** Clear the entire cache. */
export function clear(): void {
  cache.clear();
}

/** Get current cache size (for testing). */
export function size(): number {
  return cache.size;
}
