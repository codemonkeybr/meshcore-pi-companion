/**
 * Integration tests for WebSocket event handling.
 *
 * These tests verify that WebSocket events (as produced by the backend)
 * are correctly processed by the frontend state handlers.
 *
 * The fixtures in fixtures/websocket_events.json define the contract
 * between backend and frontend - both sides test against the same data.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import fixtures from './fixtures/websocket_events.json';
import { getMessageContentKey } from '../hooks/useConversationMessages';
import { getStateKey } from '../utils/conversationState';
import * as messageCache from '../messageCache';
import type { Message, Contact, Channel } from '../types';

/**
 * Simulate the WebSocket message handler from App.tsx.
 * This is the core logic we're testing.
 */
interface MockState {
  messages: Message[];
  contacts: Contact[];
  channels: Channel[];
  unreadCounts: Record<string, number>;
  lastMessageTimes: Record<string, number>;
  /** Active-conversation dedup (mirrors useConversationMessages internal set) */
  seenActiveContent: Set<string>;
}

function createMockState(): MockState {
  return {
    messages: [],
    contacts: [],
    channels: [],
    unreadCounts: {},
    lastMessageTimes: {},
    seenActiveContent: new Set(),
  };
}

/**
 * Simulate handling a message WebSocket event.
 * Mirrors the logic in App.tsx onMessage handler.
 *
 * Non-active conversation dedup uses messageCache.addMessage (single source of truth).
 * Active conversation dedup uses seenActiveContent (mirrors useConversationMessages).
 */
function handleMessageEvent(
  state: MockState,
  msg: Message,
  activeConversationKey: string | null
): { added: boolean; unreadIncremented: boolean } {
  const contentKey = getMessageContentKey(msg);
  let added = false;
  let unreadIncremented = false;

  // Check if message is for active conversation
  const isForActiveConversation =
    activeConversationKey !== null && msg.conversation_key === activeConversationKey;

  // Add to messages if for active conversation (with deduplication)
  if (isForActiveConversation) {
    if (!state.seenActiveContent.has(contentKey)) {
      state.seenActiveContent.add(contentKey);
      state.messages.push(msg);
      added = true;
    }
  }

  // Update last message time
  const stateKey =
    msg.type === 'CHAN'
      ? getStateKey('channel', msg.conversation_key)
      : getStateKey('contact', msg.conversation_key);

  state.lastMessageTimes[stateKey] = msg.received_at;

  // Increment unread if not for active conversation and not outgoing
  // Uses messageCache.addMessage as single source of truth for dedup
  if (!isForActiveConversation) {
    const isNew = messageCache.addMessage(msg.conversation_key, msg, contentKey);
    if (!msg.outgoing && isNew) {
      state.unreadCounts[stateKey] = (state.unreadCounts[stateKey] || 0) + 1;
      unreadIncremented = true;
    }
  }

  return { added, unreadIncremented };
}

/**
 * Simulate handling a contact WebSocket event.
 */
function handleContactEvent(state: MockState, contact: Contact): void {
  const idx = state.contacts.findIndex((c) => c.public_key === contact.public_key);
  if (idx >= 0) {
    // Update existing contact
    state.contacts[idx] = { ...state.contacts[idx], ...contact };
  } else {
    // Add new contact
    state.contacts.push(contact);
  }
}

/**
 * Simulate handling a message_acked WebSocket event.
 */
function handleMessageAckedEvent(state: MockState, messageId: number, ackCount: number): boolean {
  const idx = state.messages.findIndex((m) => m.id === messageId);
  if (idx >= 0) {
    state.messages[idx] = { ...state.messages[idx], acked: ackCount };
    return true;
  }
  return false;
}

// Clear messageCache between tests to avoid cross-test contamination
beforeEach(() => {
  messageCache.clear();
});

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
  // Note: duplicate_channel_message fixture references the same packet data as channel_message

  it('deduplicates messages by content when adding to list', () => {
    const state = createMockState();
    // Use channel_message fixture data since duplicate_channel_message references same packet
    const msgData = fixtures.channel_message.expected_ws_event.data;
    const msg1 = { ...msgData, id: 1, received_at: 1700000000 } as unknown as Message;
    const msg2 = { ...msgData, id: 2, received_at: 1700000001 } as unknown as Message;

    // Both arrive for active conversation
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

    // Both arrive for non-active conversation
    const result1 = handleMessageEvent(state, msg1, 'other_conversation');
    const result2 = handleMessageEvent(state, msg2, 'other_conversation');

    expect(result1.unreadIncremented).toBe(true);
    expect(result2.unreadIncremented).toBe(false); // Deduplicated

    const stateKey = getStateKey('channel', msg1.conversation_key);
    expect(state.unreadCounts[stateKey]).toBe(1); // Only incremented once
  });
});

describe('Integration: Contact/Advertisement Events', () => {
  const fixture = fixtures.advertisement_with_gps;

  it('creates new contact from advertisement', () => {
    const state = createMockState();
    const contact = fixture.expected_ws_event.data as unknown as Contact;

    handleContactEvent(state, contact);

    expect(state.contacts).toHaveLength(1);
    expect(state.contacts[0].public_key).toBe(contact.public_key);
    expect(state.contacts[0].name).toBe('Can O Mesh 2 🥫');
    expect(state.contacts[0].type).toBe(2); // Repeater
    expect(state.contacts[0].lat).toBeCloseTo(49.02056, 4);
    expect(state.contacts[0].lon).toBeCloseTo(-123.82935, 4);
  });

  it('updates existing contact from advertisement', () => {
    const state = createMockState();

    // Add existing contact
    state.contacts.push({
      public_key: fixture.expected_ws_event.data.public_key,
      name: 'Old Name',
      type: 0,
      on_radio: false,
      last_read_at: null,
    } as Contact);

    // Process new advertisement
    const contact = fixture.expected_ws_event.data as unknown as Contact;
    handleContactEvent(state, contact);

    expect(state.contacts).toHaveLength(1);
    expect(state.contacts[0].name).toBe('Can O Mesh 2 🥫'); // Updated
    expect(state.contacts[0].type).toBe(2); // Updated
  });

  it('preserves contact GPS from chat node advertisement', () => {
    const state = createMockState();
    const chatFixture = fixtures.advertisement_chat_node;
    const contact = chatFixture.expected_ws_event.data as unknown as Contact;

    handleContactEvent(state, contact);

    expect(state.contacts[0].lat).toBeCloseTo(47.786038, 4);
    expect(state.contacts[0].lon).toBeCloseTo(-122.344096, 4);
    expect(state.contacts[0].type).toBe(1); // Chat node
  });
});

describe('Integration: ACK Events', () => {
  const fixture = fixtures.message_acked;

  it('updates message ack count', () => {
    const state = createMockState();

    // Add a message that's waiting for ACK
    state.messages.push({
      id: 42,
      type: 'PRIV',
      conversation_key: 'abc123',
      text: 'Hello',
      sender_timestamp: 1700000000,
      received_at: 1700000000,
      paths: null,
      txt_type: 0,
      signature: null,
      outgoing: true,
      acked: 0,
    });

    const ackData = fixture.expected_ws_event.data;
    const updated = handleMessageAckedEvent(state, ackData.message_id, ackData.ack_count);

    expect(updated).toBe(true);
    expect(state.messages[0].acked).toBe(1);
  });

  it('returns false for unknown message id', () => {
    const state = createMockState();

    const ackData = fixture.expected_ws_event.data;
    const updated = handleMessageAckedEvent(state, ackData.message_id, ackData.ack_count);

    expect(updated).toBe(false);
  });

  it('updates to multiple ack count for flood echoes', () => {
    const state = createMockState();

    state.messages.push({
      id: 42,
      type: 'CHAN',
      conversation_key: 'channel123',
      text: 'Hello',
      sender_timestamp: 1700000000,
      received_at: 1700000000,
      paths: null,
      txt_type: 0,
      signature: null,
      outgoing: true,
      acked: 0,
    });

    // Multiple flood echoes
    handleMessageAckedEvent(state, 42, 1);
    handleMessageAckedEvent(state, 42, 2);
    handleMessageAckedEvent(state, 42, 3);

    expect(state.messages[0].acked).toBe(3);
  });
});

describe('Integration: No phantom unreads from mesh echoes (hitlist #8 regression)', () => {
  it('does not increment unread when a mesh echo arrives after many unique messages', () => {
    const state = createMockState();
    const convKey = 'channel_busy';

    // Deliver 1001 unique messages — exceeding the old global
    // seenMessageContentRef prune threshold (1000→500). Under the old
    // dual-set design the global set would drop msg-0's key during pruning,
    // so a later mesh echo of msg-0 would pass the global check and
    // phantom-increment unread. With the fix, messageCache's per-conversation
    // seenContent is the single source of truth and is never pruned.
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
        outgoing: false,
        acked: 0,
      };
      handleMessageEvent(state, msg, 'other_active_conv');
    }

    const stateKey = getStateKey('channel', convKey);
    expect(state.unreadCounts[stateKey]).toBe(MESSAGE_COUNT);

    // Now a mesh echo of msg-0 arrives (same content, different id).
    // msg-0's key would have been evicted by the old 1000→500 prune.
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
      outgoing: false,
      acked: 0,
    };
    const result = handleMessageEvent(state, echo, 'other_active_conv');

    // Must NOT increment unread — the echo is a duplicate
    expect(result.unreadIncremented).toBe(false);
    expect(state.unreadCounts[stateKey]).toBe(MESSAGE_COUNT);
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

    // Contact state key uses full public key
    expect(stateKey).toBe(`contact-${publicKey}`);
  });
});
