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

const SORT_ORDER_KEY = 'remoteterm-sortOrder';
const SIDEBAR_SECTION_SORT_ORDERS_KEY = 'remoteterm-sidebar-section-sort-orders';

export type ConversationTimes = Record<string, number>;
export type SortOrder = 'recent' | 'alpha';
export type SidebarSortableSection = 'favorites' | 'channels' | 'contacts' | 'rooms' | 'repeaters';
export type SidebarSectionSortOrders = Record<SidebarSortableSection, SortOrder>;

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
 * Load the legacy single sidebar sort order from localStorage, if present.
 */
export function loadLegacyLocalStorageSortOrder(): SortOrder | null {
  try {
    const stored = localStorage.getItem(SORT_ORDER_KEY);
    if (!stored) return null;
    return stored === 'alpha' ? 'alpha' : 'recent';
  } catch {
    return null;
  }
}

export function buildSidebarSectionSortOrders(
  defaultOrder: SortOrder = 'recent'
): SidebarSectionSortOrders {
  return {
    favorites: defaultOrder,
    channels: defaultOrder,
    contacts: defaultOrder,
    rooms: defaultOrder,
    repeaters: defaultOrder,
  };
}

/**
 * Load per-section sidebar sort orders from localStorage.
 */
export function loadLocalStorageSidebarSectionSortOrders(): SidebarSectionSortOrders | null {
  try {
    const stored = localStorage.getItem(SIDEBAR_SECTION_SORT_ORDERS_KEY);
    if (!stored) return null;

    const parsed = JSON.parse(stored) as Partial<SidebarSectionSortOrders>;
    return {
      favorites: parsed.favorites === 'alpha' ? 'alpha' : 'recent',
      channels: parsed.channels === 'alpha' ? 'alpha' : 'recent',
      contacts: parsed.contacts === 'alpha' ? 'alpha' : 'recent',
      rooms: parsed.rooms === 'alpha' ? 'alpha' : 'recent',
      repeaters: parsed.repeaters === 'alpha' ? 'alpha' : 'recent',
    };
  } catch {
    return null;
  }
}

export function saveLocalStorageSidebarSectionSortOrders(orders: SidebarSectionSortOrders): void {
  try {
    localStorage.setItem(SIDEBAR_SECTION_SORT_ORDERS_KEY, JSON.stringify(orders));
  } catch {
    // localStorage might be disabled
  }
}
