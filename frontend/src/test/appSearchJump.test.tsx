import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const PUBLIC_CHANNEL_KEY = '8B3387E9C5CDEA6AC9E5EDBAA115CD72';

const mocks = vi.hoisted(() => ({
  api: {
    getRadioConfig: vi.fn(),
    getSettings: vi.fn(),
    getUndecryptedPacketCount: vi.fn(),
    getChannels: vi.fn(),
    getContacts: vi.fn(),
  },
  useConversationMessagesCalls: vi.fn(),
}));

vi.mock('../api', () => ({
  api: mocks.api,
}));

vi.mock('../useWebSocket', () => ({
  useWebSocket: vi.fn(),
}));

vi.mock('../hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../hooks')>();
  return {
    ...actual,
    useConversationMessages: (activeConversation: unknown, targetMessageId: number | null) => {
      mocks.useConversationMessagesCalls(activeConversation, targetMessageId);
      return {
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
        observeMessage: vi.fn(() => ({ added: false, activeConversation: false })),
        receiveMessageAck: vi.fn(),
        reconcileOnReconnect: vi.fn(),
        renameConversationMessages: vi.fn(),
        removeConversationMessages: vi.fn(),
        clearConversationMessages: vi.fn(),
      };
    },
    useUnreadCounts: () => ({
      unreadCounts: {},
      mentions: {},
      lastMessageTimes: {},
      unreadLastReadAts: {},
      recordMessageEvent: vi.fn(),
      renameConversationState: vi.fn(),
      markAllRead: vi.fn(),
      refreshUnreads: vi.fn(),
    }),
  };
});

vi.mock('../components/StatusBar', () => ({
  StatusBar: () => <div data-testid="status-bar" />,
}));

vi.mock('../components/Sidebar', () => ({
  Sidebar: ({
    onSelectConversation,
    activeConversation,
  }: {
    onSelectConversation: (conv: { type: 'search' | 'channel'; id: string; name: string }) => void;
    activeConversation: { type: string; id: string } | null;
  }) => (
    <div>
      <button
        type="button"
        onClick={() =>
          onSelectConversation({
            type: 'search',
            id: 'search',
            name: 'Message Search',
          })
        }
      >
        Open Search
      </button>
      <button
        type="button"
        onClick={() =>
          onSelectConversation({
            type: 'channel',
            id: PUBLIC_CHANNEL_KEY,
            name: 'Public',
          })
        }
      >
        Open Public
      </button>
      <div data-testid="active-conversation">
        {activeConversation ? `${activeConversation.type}:${activeConversation.id}` : 'none'}
      </div>
    </div>
  ),
}));

vi.mock('../components/ChatHeader', () => ({
  ChatHeader: () => <div data-testid="chat-header" />,
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

vi.mock('../components/SearchView', () => ({
  SearchView: ({
    onNavigateToMessage,
    prefillRequest,
  }: {
    onNavigateToMessage: (target: {
      id: number;
      type: 'CHAN' | 'PRIV';
      conversation_key: string;
      conversation_name: string;
    }) => void;
    prefillRequest?: { query: string; nonce: number } | null;
  }) => (
    <div>
      <div data-testid="search-prefill">{prefillRequest?.query ?? ''}</div>
      <button
        type="button"
        onClick={() =>
          onNavigateToMessage({
            id: 321,
            type: 'CHAN',
            conversation_key: PUBLIC_CHANNEL_KEY,
            conversation_name: 'Public',
          })
        }
      >
        Jump Result
      </button>
    </div>
  ),
}));

vi.mock('../components/SettingsModal', () => ({
  SettingsModal: () => null,
}));

vi.mock('../components/RawPacketList', () => ({
  RawPacketList: () => null,
}));

vi.mock('../components/ContactInfoPane', () => ({
  ContactInfoPane: ({
    onSearchMessagesByKey,
  }: {
    onSearchMessagesByKey?: (publicKey: string) => void;
  }) => (
    <button type="button" onClick={() => onSearchMessagesByKey?.('aa'.repeat(32))}>
      Search Contact By Key
    </button>
  ),
}));

vi.mock('../components/ChannelInfoPane', () => ({
  ChannelInfoPane: () => null,
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
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

import { App } from '../App';

describe('App search jump target handling', () => {
  beforeEach(() => {
    vi.clearAllMocks();

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
    });
    mocks.api.getUndecryptedPacketCount.mockResolvedValue({ count: 0 });
    mocks.api.getChannels.mockResolvedValue([
      {
        key: PUBLIC_CHANNEL_KEY,
        name: 'Public',
        is_hashtag: false,
        on_radio: false,
        last_read_at: null,
        favorite: false,
      },
    ]);
    mocks.api.getContacts.mockResolvedValue([]);
  });

  it('clears jump target when user selects a non-search conversation', async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText('Open Search').length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getAllByText('Open Search')[0]);
    await waitFor(() => {
      expect(screen.getByText('Jump Result')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Jump Result'));

    await waitFor(() => {
      expect(mocks.useConversationMessagesCalls.mock.calls.some((call) => call[1] === 321)).toBe(
        true
      );
    });

    fireEvent.click(screen.getAllByText('Open Public')[0]);

    await waitFor(() => {
      const lastCall =
        mocks.useConversationMessagesCalls.mock.calls[
          mocks.useConversationMessagesCalls.mock.calls.length - 1
        ];
      expect(lastCall?.[1]).toBeNull();
    });
  });

  it('opens search with a prefilled query from the contact pane', async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText('Search Contact By Key')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Search Contact By Key'));

    await waitFor(() => {
      expect(screen.getByTestId('search-prefill')).toHaveTextContent(`user:${'aa'.repeat(32)}`);
      expect(
        screen
          .getAllByTestId('active-conversation')
          .some((node) => node.textContent === 'search:search')
      ).toBe(true);
    });
  });
});
