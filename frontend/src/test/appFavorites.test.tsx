import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  api: {
    getRadioConfig: vi.fn(),
    getSettings: vi.fn(),
    getUndecryptedPacketCount: vi.fn(),
    getChannels: vi.fn(),
    getContacts: vi.fn(),
    toggleFavorite: vi.fn(),
    updateSettings: vi.fn(),
    getHealth: vi.fn(),
    sendAdvertisement: vi.fn(),
    rebootRadio: vi.fn(),
    createChannel: vi.fn(),
    decryptHistoricalPackets: vi.fn(),
    createContact: vi.fn(),
    deleteChannel: vi.fn(),
    deleteContact: vi.fn(),
    sendChannelMessage: vi.fn(),
    sendDirectMessage: vi.fn(),
    requestTrace: vi.fn(),
    updateRadioConfig: vi.fn(),
    setPrivateKey: vi.fn(),
  },
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
  push: {
    isSupported: false,
    isSubscribed: false,
    subscribe: vi.fn<() => Promise<string | null>>(async () => null),
    toggleConversation: vi.fn(async () => {}),
    isConversationPushEnabled: vi.fn(() => false),
  },
  hookFns: {
    fetchOlderMessages: vi.fn(async () => {}),
    observeMessage: vi.fn(() => ({ added: false, activeConversation: false })),
    receiveMessageAck: vi.fn(),
    reconcileOnReconnect: vi.fn(),
    renameConversationMessages: vi.fn(),
    removeConversationMessages: vi.fn(),
    clearConversationMessages: vi.fn(),
    recordMessageEvent: vi.fn(),
    markAllRead: vi.fn(),
    refreshUnreads: vi.fn(async () => {}),
  },
}));

vi.mock('../api', () => ({
  api: mocks.api,
}));

vi.mock('../useWebSocket', () => ({
  useWebSocket: vi.fn(),
}));

vi.mock('../contexts/PushSubscriptionContext', () => ({
  usePush: () => ({
    isSupported: mocks.push.isSupported,
    isSubscribed: mocks.push.isSubscribed,
    currentSubscriptionId: mocks.push.isSubscribed ? 'sub-1' : null,
    allSubscriptions: [],
    pushConversations: [],
    loading: false,
    subscribe: mocks.push.subscribe,
    unsubscribe: vi.fn(async () => {}),
    toggleConversation: mocks.push.toggleConversation,
    isConversationPushEnabled: mocks.push.isConversationPushEnabled,
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
      fetchOlderMessages: mocks.hookFns.fetchOlderMessages,
      fetchNewerMessages: vi.fn(async () => {}),
      jumpToBottom: vi.fn(),
      reloadCurrentConversation: vi.fn(),
      observeMessage: mocks.hookFns.observeMessage,
      receiveMessageAck: mocks.hookFns.receiveMessageAck,
      reconcileOnReconnect: mocks.hookFns.reconcileOnReconnect,
      renameConversationMessages: mocks.hookFns.renameConversationMessages,
      removeConversationMessages: mocks.hookFns.removeConversationMessages,
      clearConversationMessages: mocks.hookFns.clearConversationMessages,
    }),
    useUnreadCounts: () => ({
      unreadCounts: {},
      mentions: {},
      lastMessageTimes: {},
      unreadLastReadAts: {},
      recordMessageEvent: mocks.hookFns.recordMessageEvent,
      renameConversationState: vi.fn(),
      markAllRead: mocks.hookFns.markAllRead,
      refreshUnreads: mocks.hookFns.refreshUnreads,
    }),
  };
});

vi.mock('../components/StatusBar', () => ({
  StatusBar: ({
    settingsMode,
    onSettingsClick,
  }: {
    settingsMode?: boolean;
    onSettingsClick: () => void;
  }) => (
    <button type="button" onClick={onSettingsClick} data-testid="status-bar-settings-toggle">
      {settingsMode ? 'Back to Chat' : 'Radio & Config'}
    </button>
  ),
}));

vi.mock('../components/Sidebar', () => ({
  Sidebar: () => <div data-testid="sidebar" />,
}));

vi.mock('../components/MessageList', () => ({
  MessageList: () => <div data-testid="message-list" />,
}));

vi.mock('../components/MessageInput', () => ({
  MessageInput: React.forwardRef((_props, ref) => {
    React.useImperativeHandle(ref, () => ({ appendText: vi.fn() }));
    return <div data-testid="message-input" />;
  }),
}));

vi.mock('../components/NewMessageModal', () => ({
  NewMessageModal: () => null,
}));

vi.mock('../components/SettingsModal', () => ({
  SettingsModal: ({ desktopSection }: { desktopSection?: string }) => (
    <div data-testid="settings-modal-section">{desktopSection ?? 'none'}</div>
  ),
  SETTINGS_SECTION_ORDER: ['radio', 'local', 'radio-app', 'database', 'bot'],
  SETTINGS_SECTION_LABELS: {
    radio: '📻 Radio',
    local: '🖥️ Local Configuration',
    'radio-app': '🗄️ Radio-App Management',
    database: '🗄️ Database',
    bot: '🤖 Bot',
  },
}));

vi.mock('../components/RawPacketList', () => ({
  RawPacketList: () => null,
}));

vi.mock('../components/MapView', () => ({
  MapView: () => null,
}));

vi.mock('../components/VisualizerView', () => ({
  VisualizerView: () => null,
}));

vi.mock('../components/CrackerPanel', () => ({
  CrackerPanel: () => null,
}));

vi.mock('../components/ui/sheet', () => ({
  Sheet: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('../components/ui/sonner', () => ({
  Toaster: () => null,
  toast: mocks.toast,
}));

vi.mock('../utils/urlHash', () => ({
  parseHashConversation: () => null,
  parseHashSettingsSection: () => null,
  updateUrlHash: vi.fn(),
  updateSettingsHash: vi.fn(),
  getSettingsHash: (section: string) => `#settings/${section}`,
  getMapFocusHash: () => '#map',
}));

import { App } from '../App';
import { useWebSocket } from '../useWebSocket';

const baseConfig = {
  public_key: 'aa'.repeat(32),
  name: 'TestNode',
  lat: 0,
  lon: 0,
  tx_power: 17,
  max_tx_power: 22,
  radio: { freq: 910.525, bw: 62.5, sf: 7, cr: 5 },
  path_hash_mode: 0,
  path_hash_mode_supported: false,
};

const baseSettings = {
  max_radio_contacts: 200,
  auto_decrypt_dm_on_advert: false,
  last_message_times: {},

  advert_interval: 0,
  last_advert_time: 0,
  flood_scope: '',
  blocked_keys: [],
  blocked_names: [],
};

const publicChannel = {
  key: '8B3387E9C5CDEA6AC9E5EDBAA115CD72',
  name: 'Public',
  is_hashtag: false,
  on_radio: false,
  last_read_at: null,
  favorite: false,
};

describe('App favorite toggle flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.push.isSupported = false;
    mocks.push.isSubscribed = false;
    mocks.push.subscribe.mockResolvedValue(null);
    mocks.push.isConversationPushEnabled.mockReturnValue(false);

    mocks.api.getRadioConfig.mockResolvedValue(baseConfig);
    mocks.api.getSettings.mockResolvedValue({ ...baseSettings });
    mocks.api.getUndecryptedPacketCount.mockResolvedValue({ count: 0 });
    mocks.api.getChannels.mockResolvedValue([publicChannel]);
    mocks.api.getContacts.mockResolvedValue([]);
    mocks.api.toggleFavorite.mockResolvedValue({
      type: 'channel',
      id: publicChannel.key,
      favorite: true,
    });
  });

  it('optimistically toggles favorite and persists on success', async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByTitle('Add to favorites')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTitle('Add to favorites'));

    await waitFor(() => {
      expect(mocks.api.toggleFavorite).toHaveBeenCalledWith('channel', publicChannel.key);
    });

    await waitFor(() => {
      expect(screen.getByTitle('Remove from favorites')).toBeInTheDocument();
    });
  });

  it('rolls back favorite state on toggle failure', async () => {
    mocks.api.toggleFavorite.mockRejectedValue(new Error('toggle failed'));

    render(<App />);

    await waitFor(() => {
      expect(screen.getByTitle('Add to favorites')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTitle('Add to favorites'));

    await waitFor(() => {
      expect(mocks.api.toggleFavorite).toHaveBeenCalledWith('channel', publicChannel.key);
    });

    await waitFor(() => {
      expect(mocks.toast.error).toHaveBeenCalledWith('Failed to update favorite');
    });

    await waitFor(() => {
      expect(screen.getByTitle('Add to favorites')).toBeInTheDocument();
    });
  });

  it('re-fetches channels after WebSocket reconnect', async () => {
    render(<App />);

    await waitFor(() => {
      expect(mocks.api.getChannels).toHaveBeenCalledTimes(1);
    });

    const wsHandlers = vi.mocked(useWebSocket).mock.calls[0]?.[0];
    expect(wsHandlers?.onReconnect).toBeTypeOf('function');

    await act(async () => {
      wsHandlers?.onReconnect?.();
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(mocks.api.getChannels).toHaveBeenCalledTimes(2);
    });
    expect(mocks.hookFns.reconcileOnReconnect).toHaveBeenCalledTimes(1);
    expect(mocks.hookFns.refreshUnreads).toHaveBeenCalledTimes(1);
  });

  it('toggles settings page mode and syncs selected section into SettingsModal', async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Radio & Config' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Radio & Config' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Back to Chat' })).toBeInTheDocument();
      expect(screen.getByTestId('settings-modal-section')).toHaveTextContent('radio');
    });

    fireEvent.click(screen.getAllByRole('button', { name: /Local Configuration/i })[0]);

    await waitFor(() => {
      expect(screen.getByTestId('settings-modal-section')).toHaveTextContent('local');
    });

    fireEvent.click(screen.getByRole('button', { name: 'Back to Chat' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Radio & Config' })).toBeInTheDocument();
      expect(screen.queryByTestId('settings-modal-section')).not.toBeInTheDocument();
    });
  });

  it('subscribes this browser before enabling web push for a conversation', async () => {
    mocks.push.isSupported = true;
    mocks.push.isSubscribed = false;
    mocks.push.subscribe.mockResolvedValue('sub-1');

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Notification settings' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Notification settings' }));
    fireEvent.click(screen.getByRole('checkbox', { name: /web push/i }));

    await waitFor(() => {
      expect(mocks.push.subscribe).toHaveBeenCalledTimes(1);
      expect(mocks.push.toggleConversation).toHaveBeenCalledWith(`channel-${publicChannel.key}`);
    });
  });

  it('does not enable web push when subscription setup fails', async () => {
    mocks.push.isSupported = true;
    mocks.push.isSubscribed = false;
    mocks.push.subscribe.mockResolvedValue(null);

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Notification settings' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Notification settings' }));
    fireEvent.click(screen.getByRole('checkbox', { name: /web push/i }));

    await waitFor(() => {
      expect(mocks.push.subscribe).toHaveBeenCalledTimes(1);
    });
    expect(mocks.push.toggleConversation).not.toHaveBeenCalled();
  });
});
