import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { Sidebar } from '../components/Sidebar';
import { CONTACT_TYPE_REPEATER, type Channel, type Contact, type Favorite } from '../types';
import { getStateKey, type ConversationTimes } from '../utils/conversationState';

function makeChannel(key: string, name: string): Channel {
  return {
    key,
    name,
    is_hashtag: false,
    on_radio: false,
    last_read_at: null,
  };
}

function makeContact(public_key: string, name: string, type = 1): Contact {
  return {
    public_key,
    name,
    type,
    flags: 0,
    last_path: null,
    last_path_len: -1,
    out_path_hash_mode: 0,
    last_advert: null,
    lat: null,
    lon: null,
    last_seen: null,
    on_radio: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: null,
  };
}

function renderSidebar(overrides?: {
  unreadCounts?: Record<string, number>;
  mentions?: Record<string, boolean>;
  favorites?: Favorite[];
  lastMessageTimes?: ConversationTimes;
  channels?: Channel[];
  isConversationNotificationsEnabled?: (type: 'channel' | 'contact', id: string) => boolean;
}) {
  const aliceName = 'Alice';
  const publicChannel = makeChannel('AA'.repeat(16), 'Public');
  const flightChannel = makeChannel('BB'.repeat(16), '#flight');
  const opsChannel = makeChannel('CC'.repeat(16), '#ops');
  const alice = makeContact('11'.repeat(32), aliceName);
  const relay = makeContact('22'.repeat(32), 'Relay', CONTACT_TYPE_REPEATER);

  const unreadCounts = overrides?.unreadCounts ?? {
    [getStateKey('channel', flightChannel.key)]: 2,
    [getStateKey('channel', opsChannel.key)]: 1,
    [getStateKey('contact', alice.public_key)]: 3,
    [getStateKey('contact', relay.public_key)]: 4,
  };

  const favorites = overrides?.favorites ?? [{ type: 'channel', id: flightChannel.key }];
  const channels = overrides?.channels ?? [publicChannel, flightChannel, opsChannel];

  const view = render(
    <Sidebar
      contacts={[alice, relay]}
      channels={channels}
      activeConversation={null}
      onSelectConversation={vi.fn()}
      onNewMessage={vi.fn()}
      lastMessageTimes={overrides?.lastMessageTimes ?? {}}
      unreadCounts={unreadCounts}
      mentions={overrides?.mentions ?? {}}
      showCracker={false}
      crackerRunning={false}
      onToggleCracker={vi.fn()}
      onMarkAllRead={vi.fn()}
      favorites={favorites}
      sortOrder="recent"
      onSortOrderChange={vi.fn()}
      isConversationNotificationsEnabled={overrides?.isConversationNotificationsEnabled}
    />
  );

  return { ...view, flightChannel, opsChannel, aliceName };
}

function getSectionHeaderContainer(title: string): HTMLElement {
  const btn = screen.getByRole('button', { name: new RegExp(title, 'i') });
  const container = btn.closest('div');
  if (!container) throw new Error(`Missing header container for section ${title}`);
  return container;
}

describe('Sidebar section summaries', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('shows muted section unread totals in each visible section header', () => {
    renderSidebar();

    expect(within(getSectionHeaderContainer('Favorites')).getByText('2')).toBeInTheDocument();
    expect(within(getSectionHeaderContainer('Channels')).getByText('1')).toBeInTheDocument();
    expect(within(getSectionHeaderContainer('Contacts')).getByText('3')).toBeInTheDocument();
    expect(within(getSectionHeaderContainer('Repeaters')).getByText('4')).toBeInTheDocument();
  });

  it('turns favorites and channels rollups red when they contain a mention', () => {
    renderSidebar({
      mentions: {
        [getStateKey('channel', 'BB'.repeat(16))]: true,
        [getStateKey('channel', 'CC'.repeat(16))]: true,
      },
    });

    expect(within(getSectionHeaderContainer('Favorites')).getByText('2')).toHaveClass(
      'bg-badge-mention',
      'text-badge-mention-foreground'
    );
    expect(within(getSectionHeaderContainer('Channels')).getByText('1')).toHaveClass(
      'bg-badge-mention',
      'text-badge-mention-foreground'
    );
  });

  it('keeps contact row badges normal while the contacts rollup is always red', () => {
    const { aliceName } = renderSidebar();

    expect(within(getSectionHeaderContainer('Contacts')).getByText('3')).toHaveClass(
      'bg-badge-mention',
      'text-badge-mention-foreground'
    );

    const aliceRow = screen.getByText(aliceName).closest('div');
    if (!aliceRow) throw new Error('Missing Alice row');
    expect(within(aliceRow).getByText('3')).toHaveClass(
      'bg-badge-unread/90',
      'text-badge-unread-foreground'
    );
  });

  it('expands collapsed sections during search and restores collapse state after clearing search', async () => {
    const { opsChannel, aliceName } = renderSidebar();

    fireEvent.click(screen.getByRole('button', { name: /Tools/i }));
    fireEvent.click(screen.getByRole('button', { name: /Channels/i }));
    fireEvent.click(screen.getByRole('button', { name: /Contacts/i }));

    expect(screen.queryByText('Packet Feed')).not.toBeInTheDocument();
    expect(screen.queryByText(opsChannel.name)).not.toBeInTheDocument();
    expect(screen.queryByText(aliceName)).not.toBeInTheDocument();

    const search = screen.getByLabelText('Search conversations');
    fireEvent.change(search, { target: { value: 'alice' } });

    await waitFor(() => {
      expect(screen.getByText(aliceName)).toBeInTheDocument();
    });

    fireEvent.change(search, { target: { value: '' } });

    await waitFor(() => {
      expect(screen.queryByText('Packet Feed')).not.toBeInTheDocument();
      expect(screen.queryByText(opsChannel.name)).not.toBeInTheDocument();
      expect(screen.queryByText(aliceName)).not.toBeInTheDocument();
    });
  });

  it('persists collapsed section state across unmount and remount', () => {
    const { opsChannel, aliceName, unmount } = renderSidebar();

    fireEvent.click(screen.getByRole('button', { name: /Tools/i }));
    fireEvent.click(screen.getByRole('button', { name: /Channels/i }));
    fireEvent.click(screen.getByRole('button', { name: /Contacts/i }));

    expect(screen.queryByText('Packet Feed')).not.toBeInTheDocument();
    expect(screen.queryByText(opsChannel.name)).not.toBeInTheDocument();
    expect(screen.queryByText(aliceName)).not.toBeInTheDocument();

    unmount();
    renderSidebar();

    expect(screen.queryByText('Packet Feed')).not.toBeInTheDocument();
    expect(screen.queryByText(opsChannel.name)).not.toBeInTheDocument();
    expect(screen.queryByText(aliceName)).not.toBeInTheDocument();
  });

  it('renders same-name channels when keys differ and allows selecting both', () => {
    const publicChannel = makeChannel('AA'.repeat(16), 'Public');
    const channelA = makeChannel('DD'.repeat(16), '#shared');
    const channelB = makeChannel('EE'.repeat(16), '#shared');
    const onSelectConversation = vi.fn();

    render(
      <Sidebar
        contacts={[]}
        channels={[publicChannel, channelA, channelB]}
        activeConversation={null}
        onSelectConversation={onSelectConversation}
        onNewMessage={vi.fn()}
        lastMessageTimes={{}}
        unreadCounts={{}}
        mentions={{}}
        showCracker={false}
        crackerRunning={false}
        onToggleCracker={vi.fn()}
        onMarkAllRead={vi.fn()}
        favorites={[]}
        sortOrder="recent"
        onSortOrderChange={vi.fn()}
      />
    );

    const sharedRows = screen.getAllByText('#shared');
    expect(sharedRows).toHaveLength(2);

    fireEvent.click(sharedRows[0]);
    fireEvent.click(sharedRows[1]);

    const selectedIds = onSelectConversation.mock.calls.map(([conv]) => conv.id);
    expect(new Set(selectedIds)).toEqual(new Set([channelA.key, channelB.key]));
  });

  it('shows a notification bell for conversations with notifications enabled', () => {
    const { aliceName } = renderSidebar({
      unreadCounts: {},
      isConversationNotificationsEnabled: (type, id) =>
        (type === 'contact' && id === '11'.repeat(32)) ||
        (type === 'channel' && id === 'BB'.repeat(16)),
    });

    const aliceRow = screen.getByText(aliceName).closest('div');
    const flightRow = screen.getByText('#flight').closest('div');
    if (!aliceRow || !flightRow) throw new Error('Missing sidebar rows');

    expect(within(aliceRow).getByLabelText('Notifications enabled')).toBeInTheDocument();
    expect(within(flightRow).getByLabelText('Notifications enabled')).toBeInTheDocument();
  });

  it('keeps the notification bell to the left of the unread pill when both are present', () => {
    const { aliceName } = renderSidebar({
      unreadCounts: {
        [getStateKey('contact', '11'.repeat(32))]: 3,
      },
      isConversationNotificationsEnabled: (type, id) =>
        type === 'contact' && id === '11'.repeat(32),
    });

    const aliceRow = screen.getByText(aliceName).closest('div');
    if (!aliceRow) throw new Error('Missing Alice row');

    const bell = within(aliceRow).getByLabelText('Notifications enabled');
    const unread = within(aliceRow).getByText('3');
    expect(bell.compareDocumentPosition(unread) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
