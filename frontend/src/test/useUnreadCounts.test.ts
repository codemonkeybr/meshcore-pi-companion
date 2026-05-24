/**
 * Tests for useUnreadCounts hook.
 *
 * Focuses on the fix for stale server-side unreads overwriting local state
 * when the user is viewing a conversation (e.g. after WS reconnect or
 * contact/channel count change triggers a server re-fetch).
 */

import { act, renderHook } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { useUnreadCounts } from '../hooks/useUnreadCounts';
import type { Channel, Contact, Conversation, Message } from '../types';
import { getStateKey } from '../utils/conversationState';

// Mock api module
vi.mock('../api', () => ({
  api: {
    getUnreads: vi.fn(),
    markChannelRead: vi.fn().mockResolvedValue({ status: 'ok', key: '' }),
    markContactRead: vi.fn().mockResolvedValue({ status: 'ok', public_key: '' }),
    markAllRead: vi.fn().mockResolvedValue({ status: 'ok' }),
  },
}));

// Mock prefetch — takePrefetchOrFetch calls the fetcher directly
vi.mock('../prefetch', () => ({
  takePrefetchOrFetch: vi.fn((_key: string, fetcher: () => Promise<unknown>) => fetcher()),
}));

function makeChannel(key: string, name: string): Channel {
  return {
    key,
    name,
    is_hashtag: false,
    on_radio: false,
    last_read_at: null,
    favorite: false,
    muted: false,
  };
}

function makeContact(pubkey: string): Contact {
  return {
    public_key: pubkey,
    name: `Contact-${pubkey.slice(0, 6)}`,
    type: 1,
    flags: 0,
    direct_path: null,
    direct_path_len: -1,
    direct_path_hash_mode: 0,
    last_advert: null,
    lat: null,
    lon: null,
    last_seen: null,
    on_radio: false,
    favorite: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: null,
  };
}

function makeMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 1,
    type: 'PRIV',
    conversation_key: CONTACT_KEY,
    text: 'hello',
    sender_timestamp: 1700000000,
    received_at: 1700000001,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: false,
    acked: 0,
    sender_name: null,
    ...overrides,
  };
}

const CHANNEL_KEY = 'AABB00112233445566778899AABBCCDD';
const CONTACT_KEY = '00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff';

// Get typed references to the mocked api functions
async function getMockedApi() {
  const { api } = await import('../api');
  return {
    getUnreads: vi.mocked(api.getUnreads),
    markChannelRead: vi.mocked(api.markChannelRead),
    markContactRead: vi.mocked(api.markContactRead),
    markAllRead: vi.mocked(api.markAllRead),
  };
}

describe('useUnreadCounts', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    const mocks = await getMockedApi();
    // Re-establish default resolvers (clearAllMocks wipes them)
    mocks.getUnreads.mockResolvedValue({
      counts: {},
      mentions: {},
      last_message_times: {},
      last_read_ats: {},
    });
    mocks.markChannelRead.mockResolvedValue({ status: 'ok', key: '' });
    mocks.markContactRead.mockResolvedValue({ status: 'ok', public_key: '' });
    mocks.markAllRead.mockResolvedValue({ status: 'ok', timestamp: 0 });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function renderWith({
    channels = [] as Channel[],
    contacts = [] as Contact[],
    activeConversation = null as Conversation | null,
  } = {}) {
    return renderHook(
      ({ channels: ch, contacts: ct, activeConversation: ac }) => useUnreadCounts(ch, ct, ac),
      { initialProps: { channels, contacts, activeConversation } }
    );
  }

  it('filters out active channel conversation from server unreads', async () => {
    const mocks = await getMockedApi();
    const channels = [makeChannel(CHANNEL_KEY, 'Test')];

    // Server reports 5 unreads for the channel we're viewing
    mocks.getUnreads.mockResolvedValue({
      counts: { [`channel-${CHANNEL_KEY}`]: 5 },
      mentions: { [`channel-${CHANNEL_KEY}`]: true },
      last_message_times: {},
      last_read_ats: { [`channel-${CHANNEL_KEY}`]: 1234 },
    });

    const activeConv: Conversation = { type: 'channel', id: CHANNEL_KEY, name: 'Test' };
    const { result } = renderWith({ channels, activeConversation: activeConv });

    // Wait for the initial fetch + apply
    await act(async () => {
      await vi.waitFor(() => expect(mocks.getUnreads).toHaveBeenCalled());
    });

    // The active conversation should NOT have unreads
    expect(result.current.unreadCounts[`channel-${CHANNEL_KEY}`]).toBeUndefined();
    expect(result.current.mentions[`channel-${CHANNEL_KEY}`]).toBeUndefined();
    expect(result.current.unreadLastReadAts[`channel-${CHANNEL_KEY}`]).toBe(1234);
  });

  it('filters out active contact conversation from server unreads', async () => {
    const mocks = await getMockedApi();
    const contacts = [makeContact(CONTACT_KEY)];

    mocks.getUnreads.mockResolvedValue({
      counts: { [`contact-${CONTACT_KEY}`]: 3 },
      mentions: {},
      last_message_times: {},
      last_read_ats: { [`contact-${CONTACT_KEY}`]: 2345 },
    });

    const activeConv: Conversation = { type: 'contact', id: CONTACT_KEY, name: 'Test' };
    const { result } = renderWith({ contacts, activeConversation: activeConv });

    await act(async () => {
      await vi.waitFor(() => expect(mocks.getUnreads).toHaveBeenCalled());
    });

    expect(result.current.unreadCounts[`contact-${CONTACT_KEY}`]).toBeUndefined();
    expect(result.current.unreadLastReadAts[`contact-${CONTACT_KEY}`]).toBe(2345);
  });

  it('preserves unreads for non-active conversations', async () => {
    const mocks = await getMockedApi();
    const otherKey = 'FFEEDDCCBBAA99887766554433221100';
    const channels = [makeChannel(CHANNEL_KEY, 'Active'), makeChannel(otherKey, 'Other')];

    mocks.getUnreads.mockResolvedValue({
      counts: {
        [`channel-${CHANNEL_KEY}`]: 5,
        [`channel-${otherKey}`]: 2,
      },
      mentions: {},
      last_message_times: {},
      last_read_ats: {},
    });

    const activeConv: Conversation = { type: 'channel', id: CHANNEL_KEY, name: 'Active' };
    const { result } = renderWith({ channels, activeConversation: activeConv });

    await act(async () => {
      await vi.waitFor(() => expect(mocks.getUnreads).toHaveBeenCalled());
    });

    // Active channel filtered out, other channel preserved
    expect(result.current.unreadCounts[`channel-${CHANNEL_KEY}`]).toBeUndefined();
    expect(result.current.unreadCounts[`channel-${otherKey}`]).toBe(2);
  });

  it('calls mark-read API for active channel after fetching unreads', async () => {
    const mocks = await getMockedApi();
    const channels = [makeChannel(CHANNEL_KEY, 'Test')];
    const activeConv: Conversation = { type: 'channel', id: CHANNEL_KEY, name: 'Test' };

    renderWith({ channels, activeConversation: activeConv });

    await act(async () => {
      await vi.waitFor(() => expect(mocks.markChannelRead).toHaveBeenCalledWith(CHANNEL_KEY));
    });
  });

  it('calls mark-read API for active contact after fetching unreads', async () => {
    const mocks = await getMockedApi();
    const contacts = [makeContact(CONTACT_KEY)];
    const activeConv: Conversation = { type: 'contact', id: CONTACT_KEY, name: 'Test' };

    renderWith({ contacts, activeConversation: activeConv });

    await act(async () => {
      await vi.waitFor(() => expect(mocks.markContactRead).toHaveBeenCalledWith(CONTACT_KEY));
    });
  });

  it('does not treat search or trace views as readable conversations', async () => {
    const mocks = await getMockedApi();
    mocks.getUnreads.mockResolvedValue({
      counts: {
        [getStateKey('channel', CHANNEL_KEY)]: 4,
        [getStateKey('contact', CONTACT_KEY)]: 2,
      },
      mentions: {
        [getStateKey('channel', CHANNEL_KEY)]: true,
      },
      last_message_times: {},
      last_read_ats: {},
    });

    const { result, rerender } = renderWith({
      channels: [makeChannel(CHANNEL_KEY, 'Test')],
      contacts: [makeContact(CONTACT_KEY)],
      activeConversation: { type: 'search', id: 'search', name: 'Message Search' },
    });

    await act(async () => {
      await vi.waitFor(() => expect(mocks.getUnreads).toHaveBeenCalled());
    });

    expect(result.current.unreadCounts[getStateKey('channel', CHANNEL_KEY)]).toBe(4);
    expect(result.current.unreadCounts[getStateKey('contact', CONTACT_KEY)]).toBe(2);
    expect(mocks.markChannelRead).not.toHaveBeenCalled();
    expect(mocks.markContactRead).not.toHaveBeenCalled();

    rerender({
      channels: [makeChannel(CHANNEL_KEY, 'Test')],
      contacts: [makeContact(CONTACT_KEY)],
      activeConversation: { type: 'trace', id: 'trace', name: 'Trace' },
    });

    await act(async () => {
      await Promise.resolve();
    });

    expect(mocks.markChannelRead).not.toHaveBeenCalled();
    expect(mocks.markContactRead).not.toHaveBeenCalled();
  });

  it('re-fetches and filters when refreshUnreads is called (simulating WS reconnect)', async () => {
    const mocks = await getMockedApi();
    const channels = [makeChannel(CHANNEL_KEY, 'Test')];
    const activeConv: Conversation = { type: 'channel', id: CHANNEL_KEY, name: 'Test' };

    // Initial fetch: no unreads
    mocks.getUnreads.mockResolvedValueOnce({
      counts: {},
      mentions: {},
      last_message_times: {},
      last_read_ats: {},
    });

    const { result } = renderWith({ channels, activeConversation: activeConv });

    await act(async () => {
      await vi.waitFor(() => expect(mocks.getUnreads).toHaveBeenCalledTimes(1));
    });

    // Simulate reconnect: server now reports unreads for the active conversation
    mocks.getUnreads.mockResolvedValueOnce({
      counts: { [`channel-${CHANNEL_KEY}`]: 7 },
      mentions: {},
      last_message_times: {},
      last_read_ats: { [`channel-${CHANNEL_KEY}`]: 3456 },
    });

    await act(async () => {
      await result.current.refreshUnreads();
    });

    // Should still be filtered out
    expect(result.current.unreadCounts[`channel-${CHANNEL_KEY}`]).toBeUndefined();
    expect(result.current.unreadLastReadAts[`channel-${CHANNEL_KEY}`]).toBe(3456);
  });

  it('re-fetches when channels change while contacts remain empty', async () => {
    const mocks = await getMockedApi();
    const initialChannels = [makeChannel(CHANNEL_KEY, 'Test')];
    const addedChannelKey = '11223344556677889900AABBCCDDEEFF';

    const { rerender } = renderWith({ channels: initialChannels, contacts: [] });

    await act(async () => {
      await vi.waitFor(() => expect(mocks.getUnreads).toHaveBeenCalledTimes(1));
    });

    mocks.getUnreads.mockResolvedValueOnce({
      counts: { [`channel-${addedChannelKey}`]: 2 },
      mentions: {},
      last_message_times: {},
      last_read_ats: {},
    });

    rerender({
      channels: [...initialChannels, makeChannel(addedChannelKey, 'Added')],
      contacts: [],
      activeConversation: null,
    });

    await act(async () => {
      await vi.waitFor(() => expect(mocks.getUnreads).toHaveBeenCalledTimes(2));
    });
  });

  it('re-fetches when contacts change while channels remain empty', async () => {
    const mocks = await getMockedApi();
    const initialContact = makeContact(CONTACT_KEY);
    const addedContactKey = 'ffeeddccbbaa99887766554433221100ffeeddccbbaa99887766554433221100';

    const { rerender } = renderWith({ channels: [], contacts: [initialContact] });

    await act(async () => {
      await vi.waitFor(() => expect(mocks.getUnreads).toHaveBeenCalledTimes(1));
    });

    mocks.getUnreads.mockResolvedValueOnce({
      counts: { [`contact-${addedContactKey}`]: 1 },
      mentions: {},
      last_message_times: {},
      last_read_ats: {},
    });

    rerender({
      channels: [],
      contacts: [initialContact, makeContact(addedContactKey)],
      activeConversation: null,
    });

    await act(async () => {
      await vi.waitFor(() => expect(mocks.getUnreads).toHaveBeenCalledTimes(2));
    });
  });

  it('does not filter when no active conversation', async () => {
    const mocks = await getMockedApi();
    mocks.getUnreads.mockResolvedValue({
      counts: { [`channel-${CHANNEL_KEY}`]: 5 },
      mentions: {},
      last_message_times: {},
      last_read_ats: {},
    });

    const { result } = renderWith({});

    await act(async () => {
      await vi.waitFor(() => expect(mocks.getUnreads).toHaveBeenCalled());
    });

    expect(result.current.unreadCounts[`channel-${CHANNEL_KEY}`]).toBe(5);
  });

  it('does not filter for non-conversation views (raw, map, visualizer)', async () => {
    const mocks = await getMockedApi();
    mocks.getUnreads.mockResolvedValue({
      counts: { [`channel-${CHANNEL_KEY}`]: 5 },
      mentions: {},
      last_message_times: {},
      last_read_ats: {},
    });

    const activeConv: Conversation = { type: 'raw', id: 'raw', name: 'Raw Packet Feed' };
    const { result } = renderWith({ activeConversation: activeConv });

    await act(async () => {
      await vi.waitFor(() => expect(mocks.getUnreads).toHaveBeenCalled());
    });

    // Raw view doesn't filter any conversation's unreads
    expect(result.current.unreadCounts[`channel-${CHANNEL_KEY}`]).toBe(5);
  });

  it('recordMessageEvent updates last-message time and unread count for new inactive incoming messages', async () => {
    const mocks = await getMockedApi();
    const { result } = renderWith({});

    await act(async () => {
      await vi.waitFor(() => expect(mocks.getUnreads).toHaveBeenCalled());
    });

    const msg = makeMessage({
      id: 5,
      type: 'CHAN',
      conversation_key: CHANNEL_KEY,
      received_at: 1700001234,
    });

    await act(async () => {
      result.current.recordMessageEvent({
        msg,
        activeConversation: false,
        isNewMessage: true,
        hasMention: true,
      });
    });

    expect(result.current.unreadCounts[getStateKey('channel', CHANNEL_KEY)]).toBe(1);
    expect(result.current.mentions[getStateKey('channel', CHANNEL_KEY)]).toBe(true);
    expect(result.current.lastMessageTimes[getStateKey('channel', CHANNEL_KEY)]).toBe(1700001234);
  });

  it('recordMessageEvent skips unread increment for active or non-new messages but still tracks time', async () => {
    const mocks = await getMockedApi();
    const { result } = renderWith({});

    await act(async () => {
      await vi.waitFor(() => expect(mocks.getUnreads).toHaveBeenCalled());
    });

    const activeMsg = makeMessage({
      id: 6,
      type: 'PRIV',
      conversation_key: CONTACT_KEY,
      received_at: 1700002000,
    });

    await act(async () => {
      result.current.recordMessageEvent({
        msg: activeMsg,
        activeConversation: true,
        isNewMessage: true,
        hasMention: true,
      });
      result.current.recordMessageEvent({
        msg: makeMessage({
          id: 7,
          type: 'CHAN',
          conversation_key: CHANNEL_KEY,
          received_at: 1700002001,
        }),
        activeConversation: false,
        isNewMessage: false,
        hasMention: true,
      });
    });

    expect(result.current.unreadCounts[getStateKey('contact', CONTACT_KEY)]).toBeUndefined();
    expect(result.current.unreadCounts[getStateKey('channel', CHANNEL_KEY)]).toBeUndefined();
    expect(result.current.lastMessageTimes[getStateKey('contact', CONTACT_KEY)]).toBe(1700002000);
    expect(result.current.lastMessageTimes[getStateKey('channel', CHANNEL_KEY)]).toBe(1700002001);
  });
});
