/**
 * Conversation state utilities.
 *
 * Last message times are tracked in-memory and persisted server-side.
 * This file provides helper functions for generating state keys
 * and managing conversation times.
 *
 * Read state (last_read_at) is tracked server-side for consistency
 * across devices - see useUnreadCounts hook.
 */

const LAST_MESSAGE_KEY = 'remoteterm-lastMessageTime';
const SORT_ORDER_KEY = 'remoteterm-sortOrder';

export type ConversationTimes = Record<string, number>;
export type SortOrder = 'recent' | 'alpha';

// In-memory cache of last message times (loaded from server on init)
let lastMessageTimesCache: ConversationTimes = {};

/**
 * Initialize the last message times cache from server data
 */
export function initLastMessageTimes(times: ConversationTimes): void {
  lastMessageTimesCache = { ...times };
}

/**
 * Get all last message times from the cache
 */
export function getLastMessageTimes(): ConversationTimes {
  return { ...lastMessageTimesCache };
}

/**
 * Update a single message time in the cache and return the updated cache.
 * Note: This does NOT persist to server - caller should sync if needed.
 */
export function setLastMessageTime(key: string, timestamp: number): ConversationTimes {
  lastMessageTimesCache[key] = timestamp;
  return { ...lastMessageTimesCache };
}

/**
 * Move conversation timing state to a new key, preserving the most recent timestamp.
 */
export function renameConversationTimeKey(oldKey: string, newKey: string): ConversationTimes {
  if (oldKey === newKey) return { ...lastMessageTimesCache };

  const oldTimestamp = lastMessageTimesCache[oldKey];
  const newTimestamp = lastMessageTimesCache[newKey];
  if (oldTimestamp !== undefined) {
    lastMessageTimesCache[newKey] =
      newTimestamp === undefined ? oldTimestamp : Math.max(newTimestamp, oldTimestamp);
    delete lastMessageTimesCache[oldKey];
  }
  return { ...lastMessageTimesCache };
}

/**
 * Generate a state tracking key for message times.
 *
 * This is NOT the same as Message.conversation_key (the database field).
 * This creates prefixed keys for state tracking:
 * - Channels: "channel-{channelKey}"
 * - Contacts: "contact-{publicKey}"
 */
export function getStateKey(type: 'channel' | 'contact', id: string): string {
  return `${type}-${id}`;
}

/**
 * Load last message times from localStorage (for migration only)
 */
export function loadLocalStorageLastMessageTimes(): ConversationTimes {
  try {
    const stored = localStorage.getItem(LAST_MESSAGE_KEY);
    return stored ? JSON.parse(stored) : {};
  } catch {
    return {};
  }
}

/**
 * Load sort order from localStorage (for migration only)
 */
export function loadLocalStorageSortOrder(): SortOrder {
  try {
    const stored = localStorage.getItem(SORT_ORDER_KEY);
    return stored === 'alpha' ? 'alpha' : 'recent';
  } catch {
    return 'recent';
  }
}

/**
 * Clear conversation state from localStorage (after migration)
 */
export function clearLocalStorageConversationState(): void {
  try {
    localStorage.removeItem(LAST_MESSAGE_KEY);
    localStorage.removeItem(SORT_ORDER_KEY);
  } catch {
    // localStorage might be disabled
  }
}
