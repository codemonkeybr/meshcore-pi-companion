/**
 * The invariant this store exists to protect: nothing on the chat render path may
 * subscribe to the raw packet stream.
 *
 * The original bug was not that the chat view read packets — it never did. It was that
 * the stream lived in `App` state, and `App` is an *ancestor* of `MessageList`. Nothing
 * on that path is memoized, so every overheard packet re-rendered the whole message list
 * regardless of which props it actually received.
 *
 * That means the regression can only be caught by mounting the real ancestor chain
 * (`App` → `AppShell` → `ConversationPane` → `MessageList`). A test that renders
 * `ConversationPane` on its own cannot see it: the offending subscription lives above.
 */
import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  messageList: vi.fn(() => <div data-testid="message-list" />),
  api: {
    getRadioConfig: vi.fn(),
    getSettings: vi.fn(),
    getUndecryptedPacketCount: vi.fn(),
    getChannels: vi.fn(),
    getContacts: vi.fn(),
    getHealth: vi.fn(),
  },
  hookFns: {
    observeMessage: vi.fn(() => ({ added: false, activeConversation: false })),
    refreshUnreads: vi.fn(async () => {}),
  },
}));

vi.mock('../api', () => ({ api: mocks.api }));

vi.mock('../useWebSocket', () => ({ useWebSocket: vi.fn() }));

vi.mock('../contexts/PushSubscriptionContext', () => ({
  usePush: () => ({
    isSupported: false,
    isSubscribed: false,
    currentSubscriptionId: null,
    allSubscriptions: [],
    pushConversations: [],
    loading: false,
    subscribe: vi.fn(async () => null),
    unsubscribe: vi.fn(async () => {}),
    toggleConversation: vi.fn(async () => {}),
    isConversationPushEnabled: () => false,
    deleteSubscription: vi.fn(async () => {}),
    testPush: vi.fn(async () => {}),
    refreshSubscriptions: vi.fn(async () => []),
    refreshConversations: vi.fn(async () => {}),
  }),
}));

vi.mock('../hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../hooks')>();
  return {
    ...actual,
    useConversationMessages: () => ({
      messages: [],
      messagesLoading: false,
      loadingOlder: false,
      hasOlderMessages: false,
      hasNewerMessages: false,
      loadingNewer: false,
      fetchOlderMessages: vi.fn(async () => {}),
      fetchNewerMessages: vi.fn(async () => {}),
      jumpToBottom: vi.fn(),
      reloadCurrentConversation: vi.fn(),
      observeMessage: mocks.hookFns.observeMessage,
      receiveMessageAck: vi.fn(),
      reconcileOnReconnect: vi.fn(),
      renameConversationMessages: vi.fn(),
      removeConversationMessages: vi.fn(),
      clearConversationMessages: vi.fn(),
    }),
    useUnreadCounts: () => ({
      unreadCounts: {},
      mentions: {},
      lastMessageTimes: {},
      unreadLastReadAts: {},
      firstUnreadIds: {},
      recordMessageEvent: vi.fn(),
      renameConversationState: vi.fn(),
      removeConversationState: vi.fn(),
      markAllRead: vi.fn(),
      refreshUnreads: mocks.hookFns.refreshUnreads,
    }),
  };
});

// Mocked to keep the tree small and deterministic. None of them are mounted while a
// chat conversation is active, so removing them cannot mask a chat-path subscription.
vi.mock('../components/StatusBar', () => ({ StatusBar: () => <div data-testid="status-bar" /> }));
vi.mock('../components/Sidebar', () => ({ Sidebar: () => <div data-testid="sidebar" /> }));
vi.mock('../components/MessageList', () => ({ MessageList: mocks.messageList }));
vi.mock('../components/MessageInput', () => ({
  MessageInput: React.forwardRef((_props, ref) => {
    React.useImperativeHandle(ref, () => ({ appendText: vi.fn(), focus: vi.fn() }));
    return <div data-testid="message-input" />;
  }),
}));
vi.mock('../components/NewMessageModal', () => ({ NewMessageModal: () => null }));
vi.mock('../components/SettingsModal', () => ({
  SettingsModal: () => null,
  SETTINGS_SECTION_ORDER: ['radio'],
  SETTINGS_SECTION_LABELS: { radio: 'Radio' },
}));
vi.mock('../components/MapView', () => ({ MapView: () => null }));
vi.mock('../components/VisualizerView', () => ({ VisualizerView: () => null }));
vi.mock('../components/CrackerPanel', () => ({ CrackerPanel: () => null }));
vi.mock('../components/ui/sonner', () => ({
  Toaster: () => null,
  toast: { success: vi.fn(), error: vi.fn() },
}));
vi.mock('../utils/urlHash', () => ({
  parseHashConversation: () => null,
  parseHashSettingsSection: () => null,
  updateUrlHash: vi.fn(),
  pushUrlHash: vi.fn(),
  updateSettingsHash: vi.fn(),
  pushSettingsHash: vi.fn(),
  getSettingsHash: (section: string) => `#settings/${section}`,
  getMapFocusHash: () => '#map',
}));

import { App } from '../App';
import {
  getRawPackets,
  recordRawPacket,
  resetRawPacketStore,
  useRawPackets,
} from '../stores/rawPacketStore';
import type { RawPacket } from '../types';

function createPacket(overrides: Partial<RawPacket> = {}): RawPacket {
  return {
    id: 1,
    observation_id: 1,
    timestamp: 1700000000,
    data: 'aabb',
    payload_type: 'GROUP_TEXT',
    snr: 7.5,
    rssi: -80,
    decrypted: false,
    decrypted_info: null,
    ...overrides,
  };
}

const publicChannel = {
  key: '8B3387E9C5CDEA6AC9E5EDBAA115CD72',
  name: 'Public',
  is_hashtag: false,
  on_radio: false,
  last_read_at: null,
  favorite: false,
  muted: false,
};

describe('overheard packets and the chat render path', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetRawPacketStore();
    mocks.api.getRadioConfig.mockResolvedValue({
      public_key: 'aa'.repeat(32),
      name: 'TestNode',
      lat: 0,
      lon: 0,
      tx_power: 17,
      max_tx_power: 22,
      radio: { freq: 910.525, bw: 62.5, sf: 7, cr: 5 },
      path_hash_mode: 0,
      path_hash_mode_supported: false,
    });
    mocks.api.getSettings.mockResolvedValue({
      max_radio_contacts: 200,
      auto_decrypt_dm_on_advert: false,
      last_message_times: {},
      advert_interval: 0,
      last_advert_time: 0,
      flood_scope: '',
      known_regions: [],
      blocked_keys: [],
      blocked_names: [],
    });
    mocks.api.getUndecryptedPacketCount.mockResolvedValue({ count: 0 });
    mocks.api.getChannels.mockResolvedValue([publicChannel]);
    mocks.api.getContacts.mockResolvedValue([]);
    mocks.api.getHealth.mockResolvedValue(null);
  });

  it('does not re-render the message list when packets arrive', async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByTestId('message-list')).toBeInTheDocument();
    });

    const rendersBefore = mocks.messageList.mock.calls.length;
    act(() => {
      for (let i = 1; i <= 25; i++) {
        recordRawPacket(createPacket({ id: i, observation_id: i }));
      }
    });

    // Guards the assertion below against passing for the wrong reason
    expect(getRawPackets()).toHaveLength(25);
    expect(mocks.messageList.mock.calls.length).toBe(rendersBefore);
  });

  it('re-renders the message list once per batch if an ancestor subscribes', async () => {
    // Negative control. Proves the assertion above can actually fail — without this, a
    // render counter that never increments would look identical to a passing test.
    //
    // The ancestor has to create the <App /> element inside its own render. Taking it as
    // a `children` prop would defeat the point: that element is built once by the caller,
    // so its identity never changes and React bails out of the subtree — the test would
    // pass while proving nothing.
    function SubscribingAncestor() {
      useRawPackets();
      return <App />;
    }

    render(<SubscribingAncestor />);
    await waitFor(() => {
      expect(screen.getByTestId('message-list')).toBeInTheDocument();
    });

    const rendersBefore = mocks.messageList.mock.calls.length;
    act(() => {
      for (let i = 1; i <= 25; i++) {
        recordRawPacket(createPacket({ id: i, observation_id: i }));
      }
    });

    expect(mocks.messageList.mock.calls.length).toBeGreaterThan(rendersBefore);
  });
});
