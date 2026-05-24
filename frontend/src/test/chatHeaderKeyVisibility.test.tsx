import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ChatHeader } from '../components/ChatHeader';
import type { Channel, Contact, Conversation, PathDiscoveryResponse } from '../types';
import { CONTACT_TYPE_ROOM } from '../types';
import { PUBLIC_CHANNEL_KEY } from '../utils/publicChannel';

function makeChannel(key: string, name: string, isHashtag: boolean): Channel {
  return {
    key,
    name,
    is_hashtag: isHashtag,
    on_radio: false,
    last_read_at: null,
    favorite: false,
    muted: false,
  };
}

const noop = () => {};

const baseProps = {
  contacts: [],
  config: null,
  notificationsSupported: true,
  notificationsEnabled: false,
  notificationsPermission: 'granted' as const,
  onTrace: noop,
  onPathDiscovery: vi.fn(async () => {
    throw new Error('unused');
  }) as (_: string) => Promise<PathDiscoveryResponse>,
  onToggleNotifications: noop,
  onToggleFavorite: noop,
  onSetChannelFloodScopeOverride: noop,
  onDeleteChannel: noop,
  onDeleteContact: noop,
};

describe('ChatHeader key visibility', () => {
  it('shows key directly for hashtag channels', () => {
    const key = 'AA'.repeat(16);
    const channel = makeChannel(key, '#general', true);
    const conversation: Conversation = { type: 'channel', id: key, name: '#general' };

    render(<ChatHeader {...baseProps} conversation={conversation} channels={[channel]} />);

    expect(screen.getByText(key.toLowerCase())).toBeInTheDocument();
    expect(screen.queryByText('Show Key')).not.toBeInTheDocument();
  });

  it('hides key behind "Show Key" button for private channels', () => {
    const key = 'BB'.repeat(16);
    const channel = makeChannel(key, 'Secret Room', false);
    const conversation: Conversation = { type: 'channel', id: key, name: 'Secret Room' };

    render(<ChatHeader {...baseProps} conversation={conversation} channels={[channel]} />);

    expect(screen.queryByText(key.toLowerCase())).not.toBeInTheDocument();
    expect(screen.getByText('Show Key')).toBeInTheDocument();
  });

  it('reveals key when "Show Key" is clicked', () => {
    const key = 'CC'.repeat(16);
    const channel = makeChannel(key, 'Private', false);
    const conversation: Conversation = { type: 'channel', id: key, name: 'Private' };

    render(<ChatHeader {...baseProps} conversation={conversation} channels={[channel]} />);

    fireEvent.click(screen.getByText('Show Key'));

    expect(screen.getByText(key.toLowerCase())).toBeInTheDocument();
    expect(screen.queryByText('Show Key')).not.toBeInTheDocument();
  });

  it('resets key visibility when conversation changes', () => {
    const key1 = 'DD'.repeat(16);
    const key2 = 'EE'.repeat(16);
    const ch1 = makeChannel(key1, 'Room1', false);
    const ch2 = makeChannel(key2, 'Room2', false);
    const conv1: Conversation = { type: 'channel', id: key1, name: 'Room1' };
    const conv2: Conversation = { type: 'channel', id: key2, name: 'Room2' };

    const { rerender } = render(
      <ChatHeader {...baseProps} conversation={conv1} channels={[ch1, ch2]} />
    );

    // Reveal key for first conversation
    fireEvent.click(screen.getByText('Show Key'));
    expect(screen.getByText(key1.toLowerCase())).toBeInTheDocument();

    // Switch conversation — key should be hidden again
    rerender(<ChatHeader {...baseProps} conversation={conv2} channels={[ch1, ch2]} />);

    expect(screen.queryByText(key2.toLowerCase())).not.toBeInTheDocument();
    expect(screen.getByText('Show Key')).toBeInTheDocument();
  });

  it('shows key directly for contacts', () => {
    const pubKey = '11'.repeat(32);
    const conversation: Conversation = { type: 'contact', id: pubKey, name: 'Alice' };

    render(<ChatHeader {...baseProps} conversation={conversation} channels={[]} />);

    expect(screen.getByText(pubKey)).toBeInTheDocument();
    expect(screen.queryByText('Show Key')).not.toBeInTheDocument();
  });

  it('renders the clickable conversation title as a real button inside the heading', () => {
    const pubKey = '12'.repeat(32);
    const conversation: Conversation = { type: 'contact', id: pubKey, name: 'Alice' };
    const onOpenContactInfo = vi.fn();

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        channels={[]}
        onOpenContactInfo={onOpenContactInfo}
      />
    );

    const heading = screen.getByRole('heading', { name: /alice/i });
    const titleButton = within(heading).getByRole('button', { name: 'View info for Alice' });

    expect(heading).toContainElement(titleButton);
    fireEvent.click(titleButton);
    expect(onOpenContactInfo).toHaveBeenCalledWith(pubKey);
  });

  it('copies key to clipboard when revealed key is clicked', async () => {
    const key = 'FF'.repeat(16);
    const channel = makeChannel(key, 'Priv', false);
    const conversation: Conversation = { type: 'channel', id: key, name: 'Priv' };

    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<ChatHeader {...baseProps} conversation={conversation} channels={[channel]} />);

    // Reveal key then click to copy
    fireEvent.click(screen.getByText('Show Key'));
    fireEvent.click(screen.getByText(key.toLowerCase()));

    expect(writeText).toHaveBeenCalledWith(key);
  });

  it('shows active regional override badge for channels', () => {
    const key = 'AB'.repeat(16);
    const channel = {
      ...makeChannel(key, '#flightless', true),
      flood_scope_override: '#Esperance',
    };
    const conversation: Conversation = { type: 'channel', id: key, name: '#flightless' };

    render(<ChatHeader {...baseProps} conversation={conversation} channels={[channel]} />);

    expect(screen.getAllByText('#Esperance')).toHaveLength(2);
  });

  it('shows filled bell when notifications are enabled and toggles via dropdown', () => {
    const conversation: Conversation = { type: 'contact', id: '11'.repeat(32), name: 'Alice' };
    const onToggleNotifications = vi.fn();

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        channels={[]}
        notificationsEnabled
        onToggleNotifications={onToggleNotifications}
      />
    );

    // Bell button should be present; open the dropdown
    const bellBtn = screen.getByRole('button', { name: 'Notification settings' });
    fireEvent.click(bellBtn);

    // Desktop notifications checkbox should be checked
    const checkbox = screen.getByRole('checkbox', { name: /desktop notifications/i });
    expect(checkbox).toBeChecked();

    // Toggling calls the handler
    fireEvent.click(checkbox);
    expect(onToggleNotifications).toHaveBeenCalledTimes(1);
  });

  it('keeps desktop notifications available when web push is also supported', () => {
    const conversation: Conversation = { type: 'contact', id: '13'.repeat(32), name: 'Alice' };

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        channels={[]}
        pushSupported
        pushSubscribed
        pushEnabledForConversation
        onTogglePush={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Notification settings' }));

    expect(screen.getByRole('checkbox', { name: /desktop notifications/i })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /web push/i })).toBeInTheDocument();
  });

  it('hides trace and notification controls for room-server contacts', () => {
    const pubKey = '41'.repeat(32);
    const contact: Contact = {
      public_key: pubKey,
      name: 'Ops Board',
      type: CONTACT_TYPE_ROOM,
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
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };
    const conversation: Conversation = { type: 'contact', id: pubKey, name: 'Ops Board' };

    render(
      <ChatHeader {...baseProps} conversation={conversation} channels={[]} contacts={[contact]} />
    );

    expect(screen.queryByRole('button', { name: 'Path Discovery' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Direct Trace' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Notification settings' })).not.toBeInTheDocument();
  });

  it('hides the delete button for the canonical Public channel', () => {
    const channel = makeChannel(PUBLIC_CHANNEL_KEY, 'Public', false);
    const conversation: Conversation = { type: 'channel', id: PUBLIC_CHANNEL_KEY, name: 'Public' };

    render(<ChatHeader {...baseProps} conversation={conversation} channels={[channel]} />);

    expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument();
  });

  it('still shows the delete button for non-canonical channels named Public', () => {
    const key = 'AB'.repeat(16);
    const channel = makeChannel(key, 'Public', false);
    const conversation: Conversation = { type: 'channel', id: key, name: 'Public' };

    render(<ChatHeader {...baseProps} conversation={conversation} channels={[channel]} />);

    expect(screen.getByRole('button', { name: 'Delete' })).toBeInTheDocument();
  });

  it('opens path discovery modal for contacts and runs the request on demand', async () => {
    const pubKey = '21'.repeat(32);
    const contact: Contact = {
      public_key: pubKey,
      name: 'Alice',
      type: 1,
      flags: 0,
      direct_path: 'AA',
      direct_path_len: 1,
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
    const conversation: Conversation = { type: 'contact', id: pubKey, name: 'Alice' };
    const onPathDiscovery = vi.fn().mockResolvedValue({
      contact,
      forward_path: { path: 'AA', path_len: 1, path_hash_mode: 0 },
      return_path: { path: '', path_len: 0, path_hash_mode: 0 },
    } satisfies PathDiscoveryResponse);

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        channels={[]}
        contacts={[contact]}
        onPathDiscovery={onPathDiscovery}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Path Discovery' }));

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Run path discovery' }));

    await waitFor(() => {
      expect(onPathDiscovery).toHaveBeenCalledWith(pubKey);
    });
  });

  it('shows an override warning in the path discovery modal when forced routing is set', async () => {
    const pubKey = '31'.repeat(32);
    const contact: Contact = {
      public_key: pubKey,
      name: 'Alice',
      type: 1,
      flags: 0,
      direct_path: 'AA',
      direct_path_len: 1,
      direct_path_hash_mode: 0,
      route_override_path: 'BBDD',
      route_override_len: 2,
      route_override_hash_mode: 0,
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
    const conversation: Conversation = { type: 'contact', id: pubKey, name: 'Alice' };

    render(
      <ChatHeader {...baseProps} conversation={conversation} channels={[]} contacts={[contact]} />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Path Discovery' }));

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(/current learned route: 1 hop \(AA\)/i)).toBeInTheDocument();
    expect(screen.getByText(/current forced route: 2 hops \(BB -> DD\)/i)).toBeInTheDocument();
    expect(screen.getByText(/forced route override is currently set/i)).toBeInTheDocument();
    expect(screen.getByText(/clearing the forced route afterward is enough/i)).toBeInTheDocument();
  });

  it('opens the regional override modal and applies the entered region', async () => {
    const key = 'CD'.repeat(16);
    const channel = makeChannel(key, '#flightless', true);
    const conversation: Conversation = { type: 'channel', id: key, name: '#flightless' };
    const onSetChannelFloodScopeOverride = vi.fn();

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        channels={[channel]}
        onSetChannelFloodScopeOverride={onSetChannelFloodScopeOverride}
      />
    );

    fireEvent.click(screen.getByTitle('Set regional override'));

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Region'), { target: { value: 'Esperance' } });
    fireEvent.click(screen.getByRole('button', { name: 'Use Esperance region for #flightless' }));

    expect(onSetChannelFloodScopeOverride).toHaveBeenCalledWith(key, 'Esperance');
  });
});
