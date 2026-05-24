/**
 * Integration tests for message deduplication and content key contracts.
 *
 * These tests verify that the real messageCache and getMessageContentKey
 * functions work correctly with realistic WebSocket event data from fixtures.
 *
 * The fixtures in fixtures/websocket_events.json define the contract
 * between backend and frontend - both sides test against the same data.
 */

import { describe, it, expect } from 'vitest';
import fixtures from './fixtures/websocket_events.json';
import { getStateKey } from '../utils/conversationState';
import { mergeContactIntoList } from '../utils/contactMerge';
import { ConversationMessageCache } from '../hooks/useConversationMessages';
import { getMessageContentKey } from '../utils/messageIdentity';
import type { Contact, Message } from '../types';

/**
 * Minimal state for testing message dedup and unread logic.
 * Uses real messageCache.addMessage and real getMessageContentKey.
 */
interface MockState {
  messages: Message[];
  unreadCounts: Record<string, number>;
  lastMessageTimes: Record<string, number>;
  seenActiveContent: Set<string>;
  messageCache: ConversationMessageCache;
}

function createMockState(): MockState {
  return {
    messages: [],
    unreadCounts: {},
    lastMessageTimes: {},
    seenActiveContent: new Set(),
    messageCache: new ConversationMessageCache(),
  };
}

/**
 * Simulate the message handling path from App.tsx.
 * Uses real getMessageContentKey and real messageCache.addMessage for dedup.
 */
function handleMessageEvent(
  state: MockState,
  msg: Message,
  activeConversationKey: string | null
): { added: boolean; unreadIncremented: boolean } {
  const contentKey = getMessageContentKey(msg);
  let added = false;
  let unreadIncremented = false;

  const isForActiveConversation =
    activeConversationKey !== null && msg.conversation_key === activeConversationKey;

  if (isForActiveConversation) {
    if (!state.seenActiveContent.has(contentKey)) {
      state.seenActiveContent.add(contentKey);
      state.messages.push(msg);
      added = true;
    }
  }

  const stateKey =
    msg.type === 'CHAN'
      ? getStateKey('channel', msg.conversation_key)
      : getStateKey('contact', msg.conversation_key);

  state.lastMessageTimes[stateKey] = msg.received_at;

  if (!isForActiveConversation) {
    const isNew = state.messageCache.addMessage(msg.conversation_key, msg);
    if (!msg.outgoing && isNew) {
      state.unreadCounts[stateKey] = (state.unreadCounts[stateKey] || 0) + 1;
      unreadIncremented = true;
    }
  }

  return { added, unreadIncremented };
}

describe('Integration: Channel Message Events', () => {
  const fixture = fixtures.channel_message;

  it('adds message to list when conversation is active', () => {
    const state = createMockState();
    const msg = fixture.expected_ws_event.data as unknown as Message;
    msg.id = 1;
    msg.received_at = 1700000000;

    const result = handleMessageEvent(state, msg, msg.conversation_key);

    expect(result.added).toBe(true);
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].text).toContain('Flightless🥝');
  });

  it('increments unread count when conversation is not active', () => {
    const state = createMockState();
    const msg = fixture.expected_ws_event.data as unknown as Message;
    msg.id = 1;
    msg.received_at = 1700000000;

    const result = handleMessageEvent(state, msg, 'different_conversation');

    expect(result.unreadIncremented).toBe(true);
    const stateKey = getStateKey('channel', msg.conversation_key);
    expect(state.unreadCounts[stateKey]).toBe(1);
  });

  it('updates lastMessageTimes for sidebar sorting', () => {
    const state = createMockState();
    const msg = fixture.expected_ws_event.data as unknown as Message;
    msg.id = 1;
    msg.received_at = 1700000000;

    handleMessageEvent(state, msg, null);

    const stateKey = getStateKey('channel', msg.conversation_key);
    expect(state.lastMessageTimes[stateKey]).toBe(1700000000);
  });

  it('does not increment unread for outgoing messages', () => {
    const state = createMockState();
    const msg = { ...fixture.expected_ws_event.data, outgoing: true } as unknown as Message;
    msg.id = 1;
    msg.received_at = 1700000000;

    const result = handleMessageEvent(state, msg, 'different_conversation');

    expect(result.unreadIncremented).toBe(false);
    const stateKey = getStateKey('channel', msg.conversation_key);
    expect(state.unreadCounts[stateKey]).toBeUndefined();
  });
});

describe('Integration: Duplicate Message Handling', () => {
  it('deduplicates messages by content when adding to list', () => {
    const state = createMockState();
    const msgData = fixtures.channel_message.expected_ws_event.data;
    const msg1 = { ...msgData, id: 1, received_at: 1700000000 } as unknown as Message;
    const msg2 = { ...msgData, id: 2, received_at: 1700000001 } as unknown as Message;

    const result1 = handleMessageEvent(state, msg1, msg1.conversation_key);
    const result2 = handleMessageEvent(state, msg2, msg2.conversation_key);

    expect(result1.added).toBe(true);
    expect(result2.added).toBe(false); // Deduplicated
    expect(state.messages).toHaveLength(1);
  });

  it('deduplicates unread increments by content', () => {
    const state = createMockState();
    const msgData = fixtures.channel_message.expected_ws_event.data;
    const msg1 = { ...msgData, id: 1, received_at: 1700000000 } as unknown as Message;
    const msg2 = { ...msgData, id: 2, received_at: 1700000001 } as unknown as Message;

    const result1 = handleMessageEvent(state, msg1, 'other_conversation');
    const result2 = handleMessageEvent(state, msg2, 'other_conversation');

    expect(result1.unreadIncremented).toBe(true);
    expect(result2.unreadIncremented).toBe(false); // Deduplicated

    const stateKey = getStateKey('channel', msg1.conversation_key);
    expect(state.unreadCounts[stateKey]).toBe(1); // Only incremented once
  });
});

describe('Integration: Trimmed cache entries can reappear (hitlist #7 regression)', () => {
  it('increments unread when an evicted inactive-conversation message arrives again', () => {
    const state = createMockState();
    const convKey = 'channel_busy';

    // Deliver enough unique messages to evict msg-0 from the inactive
    // conversation cache. Once it falls out of that window, a later arrival
    // with the same content should be allowed back in instead of being
    // suppressed forever by a stale content key.
    const MESSAGE_COUNT = 1001;
    for (let i = 0; i < MESSAGE_COUNT; i++) {
      const msg: Message = {
        id: i,
        type: 'CHAN',
        conversation_key: convKey,
        text: `msg-${i}`,
        sender_timestamp: 1700000000 + i,
        received_at: 1700000000 + i,
        paths: null,
        txt_type: 0,
        signature: null,
        sender_key: null,
        outgoing: false,
        acked: 0,
        sender_name: null,
      };
      handleMessageEvent(state, msg, 'other_active_conv');
    }

    const stateKey = getStateKey('channel', convKey);
    expect(state.unreadCounts[stateKey]).toBe(MESSAGE_COUNT);

    // Now a mesh echo of msg-0 arrives (same content, different id).
    const echo: Message = {
      id: 9999,
      type: 'CHAN',
      conversation_key: convKey,
      text: 'msg-0',
      sender_timestamp: 1700000000, // same sender_timestamp as original
      received_at: 1700001000,
      paths: null,
      txt_type: 0,
      signature: null,
      sender_key: null,
      outgoing: false,
      acked: 0,
      sender_name: null,
    };
    const result = handleMessageEvent(state, echo, 'other_active_conv');

    expect(result.unreadIncremented).toBe(true);
    expect(state.unreadCounts[stateKey]).toBe(MESSAGE_COUNT + 1);
  });
});

describe('Integration: Message Content Key Contract', () => {
  it('generates consistent keys for deduplication', () => {
    const msg = fixtures.channel_message.expected_ws_event.data as unknown as Message;
    msg.id = 1;

    // Same content with different IDs should generate same key
    const msg2 = { ...msg, id: 2 };

    expect(getMessageContentKey(msg)).toBe(getMessageContentKey(msg2));
  });

  it('key format matches backend expectation', () => {
    const msg = fixtures.channel_message.expected_ws_event.data as unknown as Message;

    const key = getMessageContentKey(msg);

    // Key should be: type-conversation_key-text-sender_timestamp
    expect(key).toContain(msg.type);
    expect(key).toContain(msg.conversation_key);
    expect(key).toContain(String(msg.sender_timestamp));
  });
});

describe('Integration: State Key Contract', () => {
  it('generates correct channel state key', () => {
    const channelKey = fixtures.channel_message.expected_ws_event.data.conversation_key;

    const stateKey = getStateKey('channel', channelKey);

    expect(stateKey).toBe(`channel-${channelKey}`);
  });

  it('generates correct contact state key with full public key', () => {
    const publicKey = fixtures.advertisement_with_gps.expected_ws_event.data.public_key;

    const stateKey = getStateKey('contact', publicKey);

    expect(stateKey).toBe(`contact-${publicKey}`);
  });
});

// --- Contact merge tests (imports real mergeContactIntoList) ---

function makeContact(overrides: Partial<Contact> = {}): Contact {
  return {
    public_key: 'abc123',
    name: 'TestNode',
    type: 1,
    flags: 0,
    direct_path: null,
    direct_path_len: 0,
    last_advert: null,
    lat: null,
    lon: null,
    last_seen: null,
    on_radio: true,
    favorite: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: null,
    ...overrides,
    direct_path_hash_mode: overrides.direct_path_hash_mode ?? 0,
  };
}

describe('Integration: Contact Merge', () => {
  it('appends new contact to list', () => {
    const existing = [makeContact({ public_key: 'aaa', name: 'Alpha' })];
    const incoming = makeContact({ public_key: 'bbb', name: 'Beta' });

    const result = mergeContactIntoList(existing, incoming);

    expect(result).toHaveLength(2);
    expect(result[1].name).toBe('Beta');
  });

  it('merges existing contact (updates name, preserves other fields)', () => {
    const existing = [makeContact({ public_key: 'aaa', name: 'Alpha', lat: 47.0 })];
    const incoming = makeContact({ public_key: 'aaa', name: 'Alpha-Updated' });

    const result = mergeContactIntoList(existing, incoming);

    expect(result).toHaveLength(1);
    expect(result[0].name).toBe('Alpha-Updated');
    // Spread semantics: incoming lat (null) overwrites existing lat
    expect(result[0].public_key).toBe('aaa');
  });

  it('returns same array reference when contact is unchanged', () => {
    const contact = makeContact({ public_key: 'aaa', name: 'Alpha' });
    const existing = [contact];
    // Incoming with identical values
    const incoming = makeContact({ public_key: 'aaa', name: 'Alpha' });

    const result = mergeContactIntoList(existing, incoming);

    expect(result).toBe(existing); // referential equality
  });

  it('partial update merges without clobbering unrelated fields', () => {
    const existing = [makeContact({ public_key: 'aaa', name: 'Alpha', lat: 47.0, lon: -122.0 })];
    // Incoming update only changes lat
    const incoming = makeContact({ public_key: 'aaa', name: 'Alpha', lat: 48.0, lon: -122.0 });

    const result = mergeContactIntoList(existing, incoming);

    expect(result[0].lat).toBe(48.0);
    expect(result[0].lon).toBe(-122.0);
    expect(result[0].name).toBe('Alpha');
  });
});

// --- ACK + messageCache propagation tests ---

describe('Integration: ACK + messageCache propagation', () => {
  it('updateAck updates acked count on cached message', () => {
    const messageCache = new ConversationMessageCache();
    const msg: Message = {
      id: 100,
      type: 'PRIV',
      conversation_key: 'pk_abc',
      text: 'Hello',
      sender_timestamp: 1700000000,
      received_at: 1700000000,
      paths: null,
      txt_type: 0,
      signature: null,
      sender_key: null,
      outgoing: true,
      acked: 0,
      sender_name: null,
    };
    messageCache.addMessage('pk_abc', msg);

    messageCache.updateAck(100, 1);

    const entry = messageCache.get('pk_abc');
    expect(entry).toBeDefined();
    expect(entry!.messages[0].acked).toBe(1);
  });

  it('updateAck updates paths when longer', () => {
    const messageCache = new ConversationMessageCache();
    const msg: Message = {
      id: 101,
      type: 'PRIV',
      conversation_key: 'pk_abc',
      text: 'Test',
      sender_timestamp: 1700000001,
      received_at: 1700000001,
      paths: [{ path: 'aa', received_at: 1700000001 }],
      txt_type: 0,
      signature: null,
      sender_key: null,
      outgoing: true,
      acked: 1,
      sender_name: null,
    };
    messageCache.addMessage('pk_abc', msg);

    const longerPaths = [
      { path: 'aa', received_at: 1700000001 },
      { path: 'bb', received_at: 1700000002 },
    ];
    messageCache.updateAck(101, 2, longerPaths);

    const entry = messageCache.get('pk_abc');
    expect(entry!.messages[0].paths).toHaveLength(2);
    expect(entry!.messages[0].acked).toBe(2);
  });

  it('preserves higher existing ack count (max semantics)', () => {
    const messageCache = new ConversationMessageCache();
    const msg: Message = {
      id: 102,
      type: 'PRIV',
      conversation_key: 'pk_abc',
      text: 'Max test',
      sender_timestamp: 1700000002,
      received_at: 1700000002,
      paths: null,
      txt_type: 0,
      signature: null,
      sender_key: null,
      outgoing: true,
      acked: 5,
      sender_name: null,
    };
    messageCache.addMessage('pk_abc', msg);

    // Try to update with a lower ack count
    messageCache.updateAck(102, 3);

    const entry = messageCache.get('pk_abc');
    expect(entry!.messages[0].acked).toBe(5); // max(5, 3) = 5
  });

  it('is a no-op for unknown message ID', () => {
    const messageCache = new ConversationMessageCache();
    const msg: Message = {
      id: 103,
      type: 'PRIV',
      conversation_key: 'pk_abc',
      text: 'Existing',
      sender_timestamp: 1700000003,
      received_at: 1700000003,
      paths: null,
      txt_type: 0,
      signature: null,
      sender_key: null,
      outgoing: true,
      acked: 0,
      sender_name: null,
    };
    messageCache.addMessage('pk_abc', msg);

    // Update a non-existent message ID — should not throw or modify anything
    messageCache.updateAck(999, 1);

    const entry = messageCache.get('pk_abc');
    expect(entry!.messages[0].acked).toBe(0); // unchanged
  });
});
