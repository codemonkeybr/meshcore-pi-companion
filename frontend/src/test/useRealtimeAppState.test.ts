import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useRealtimeAppState } from '../hooks/useRealtimeAppState';
import type { Channel, Contact, Conversation, HealthStatus, Message, RawPacket } from '../types';

const mocks = vi.hoisted(() => ({
  api: {
    getChannels: vi.fn(),
  },
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock('../api', () => ({
  api: mocks.api,
}));

vi.mock('../components/ui/sonner', () => ({
  toast: mocks.toast,
}));

const publicChannel: Channel = {
  key: '8B3387E9C5CDEA6AC9E5EDBAA115CD72',
  name: 'Public',
  is_hashtag: false,
  on_radio: false,
  last_read_at: null,
  favorite: false,
  muted: false,
};

const incomingDm: Message = {
  id: 7,
  type: 'PRIV',
  conversation_key: 'aa'.repeat(32),
  text: 'hello',
  sender_timestamp: 1700000000,
  received_at: 1700000001,
  paths: null,
  txt_type: 0,
  signature: null,
  sender_key: 'aa'.repeat(32),
  outgoing: false,
  acked: 0,
  sender_name: 'Alice',
};

function createRealtimeArgs(overrides: Partial<Parameters<typeof useRealtimeAppState>[0]> = {}) {
  const setHealth = vi.fn();
  const setRawPackets = vi.fn();
  const setChannels = vi.fn();
  const setContacts = vi.fn();

  return {
    args: {
      prevHealthRef: { current: null as HealthStatus | null },
      setHealth,
      fetchConfig: vi.fn(),
      setRawPackets,
      reconcileOnReconnect: vi.fn(),
      refreshUnreads: vi.fn(async () => {}),
      setChannels,
      fetchAllContacts: vi.fn(async () => [] as Contact[]),
      setContacts,
      blockedKeysRef: { current: [] as string[] },
      channelsRef: { current: [publicChannel] },
      blockedNamesRef: { current: [] as string[] },
      activeConversationRef: { current: null as Conversation | null },
      observeMessage: vi.fn(() => ({ added: false, activeConversation: false })),
      recordMessageEvent: vi.fn(),
      renameConversationState: vi.fn(),
      removeConversationState: vi.fn(),
      checkMention: vi.fn(() => false),
      pendingDeleteFallbackRef: { current: false },
      setActiveConversation: vi.fn(),
      renameConversationMessages: vi.fn(),
      removeConversationMessages: vi.fn(),
      receiveMessageAck: vi.fn(),
      notifyIncomingMessage: vi.fn(),
      ...overrides,
    },
    fns: {
      setHealth,
      setRawPackets,
      setChannels,
      setContacts,
    },
  };
}

describe('useRealtimeAppState', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.api.getChannels.mockResolvedValue([publicChannel]);
  });

  it('reconnect clears raw packets and refetches channels/contacts/unreads', async () => {
    const contacts: Contact[] = [
      {
        public_key: 'bb'.repeat(32),
        name: 'Bob',
        type: 1,
        flags: 0,
        direct_path: null,
        direct_path_len: 0,
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
      },
    ];

    const { args, fns } = createRealtimeArgs({
      fetchAllContacts: vi.fn(async () => contacts),
    });

    const { result } = renderHook(() => useRealtimeAppState(args));

    act(() => {
      result.current.onReconnect?.();
    });

    await waitFor(() => {
      expect(args.reconcileOnReconnect).toHaveBeenCalledTimes(1);
      expect(args.refreshUnreads).toHaveBeenCalledTimes(1);
      expect(mocks.api.getChannels).toHaveBeenCalledTimes(1);
      expect(args.fetchAllContacts).toHaveBeenCalledTimes(1);
      expect(fns.setRawPackets).toHaveBeenCalledWith([]);
      expect(fns.setChannels).toHaveBeenCalledWith([publicChannel]);
      expect(fns.setContacts).toHaveBeenCalledWith(contacts);
    });
  });

  it('reconnect skips active-conversation reconcile while browsing mid-history', async () => {
    const contacts: Contact[] = [
      {
        public_key: 'bb'.repeat(32),
        name: 'Bob',
        type: 1,
        flags: 0,
        direct_path: null,
        direct_path_len: 0,
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
      },
    ];

    const { args, fns } = createRealtimeArgs({
      fetchAllContacts: vi.fn(async () => contacts),
    });

    const { result } = renderHook(() => useRealtimeAppState(args));

    act(() => {
      result.current.onReconnect?.();
    });

    await waitFor(() => {
      expect(args.reconcileOnReconnect).toHaveBeenCalledTimes(1);
      expect(args.refreshUnreads).toHaveBeenCalledTimes(1);
      expect(mocks.api.getChannels).toHaveBeenCalledTimes(1);
      expect(args.fetchAllContacts).toHaveBeenCalledTimes(1);
      expect(fns.setRawPackets).toHaveBeenCalledWith([]);
      expect(fns.setChannels).toHaveBeenCalledWith([publicChannel]);
      expect(fns.setContacts).toHaveBeenCalledWith(contacts);
    });
  });

  it('tracks unread state for a new non-active incoming message', () => {
    const { args } = createRealtimeArgs({
      checkMention: vi.fn(() => true),
      observeMessage: vi.fn(() => ({ added: true, activeConversation: false })),
    });

    const { result } = renderHook(() => useRealtimeAppState(args));

    act(() => {
      result.current.onMessage?.(incomingDm);
    });

    expect(args.observeMessage).toHaveBeenCalledWith(incomingDm);
    expect(args.recordMessageEvent).toHaveBeenCalledWith({
      msg: incomingDm,
      activeConversation: false,
      isNewMessage: true,
      hasMention: true,
    });
    expect(args.notifyIncomingMessage).toHaveBeenCalledWith(incomingDm);
  });

  it('deleting the active contact clears it and marks fallback recovery pending', () => {
    const pendingDeleteFallbackRef = { current: false };
    const activeConversationRef = {
      current: {
        type: 'contact',
        id: incomingDm.conversation_key,
        name: 'Alice',
      } satisfies Conversation,
    };
    const { args, fns } = createRealtimeArgs({
      activeConversationRef,
      pendingDeleteFallbackRef,
    });

    const { result } = renderHook(() => useRealtimeAppState(args));

    act(() => {
      result.current.onContactDeleted?.(incomingDm.conversation_key);
    });

    expect(fns.setContacts).toHaveBeenCalledWith(expect.any(Function));
    expect(args.removeConversationMessages).toHaveBeenCalledWith(incomingDm.conversation_key);
    expect(args.setActiveConversation).toHaveBeenCalledWith(null);
    expect(pendingDeleteFallbackRef.current).toBe(true);
  });

  it('resolves a prefix-only contact into a full key and updates active conversation state', () => {
    const previousPublicKey = 'abc123def456';
    const resolvedContact: Contact = {
      public_key: 'aa'.repeat(32),
      name: null,
      type: 0,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      last_advert: null,
      lat: null,
      lon: null,
      last_seen: null,
      on_radio: false,
      favorite: false,
      last_contacted: 1700000000,
      last_read_at: null,
      first_seen: 1700000000,
    };
    const activeConversationRef = {
      current: {
        type: 'contact',
        id: previousPublicKey,
        name: 'abc123def456',
      } satisfies Conversation,
    };
    const { args, fns } = createRealtimeArgs({
      activeConversationRef,
    });

    const { result } = renderHook(() => useRealtimeAppState(args));

    act(() => {
      result.current.onContactResolved?.(previousPublicKey, resolvedContact);
    });

    expect(fns.setContacts).toHaveBeenCalledWith(expect.any(Function));
    expect(args.renameConversationMessages).toHaveBeenCalledWith(
      previousPublicKey,
      resolvedContact.public_key
    );
    expect(args.renameConversationState).toHaveBeenCalledWith(
      `contact-${previousPublicKey}`,
      `contact-${resolvedContact.public_key}`
    );
    expect(args.setActiveConversation).toHaveBeenCalledWith({
      type: 'contact',
      id: resolvedContact.public_key,
      name: '[unknown sender]',
    });
  });

  it('appends raw packets using observation identity dedup', () => {
    const { args, fns } = createRealtimeArgs();
    const packet: RawPacket = {
      id: 1,
      observation_id: 2,
      timestamp: 1700000000,
      data: 'aabb',
      payload_type: 'GROUP_TEXT',
      snr: 7.5,
      rssi: -80,
      decrypted: false,
      decrypted_info: null,
    };

    const { result } = renderHook(() => useRealtimeAppState(args));

    act(() => {
      result.current.onRawPacket?.(packet);
    });

    expect(fns.setRawPackets).toHaveBeenCalledWith(expect.any(Function));
  });
});
