import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ChatHeader } from '../components/ChatHeader';
import type { Channel, Conversation, Favorite } from '../types';

function makeChannel(key: string, name: string, isHashtag: boolean): Channel {
  return { key, name, is_hashtag: isHashtag, on_radio: false, last_read_at: null };
}

const noop = () => {};

const baseProps = {
  contacts: [],
  config: null,
  favorites: [] as Favorite[],
  notificationsSupported: true,
  notificationsEnabled: false,
  notificationsPermission: 'granted' as const,
  onTrace: noop,
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

  it('shows enabled notification state and toggles when clicked', () => {
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

    fireEvent.click(screen.getByText('Notifications On'));

    expect(screen.getByText('Notifications On')).toBeInTheDocument();
    expect(onToggleNotifications).toHaveBeenCalledTimes(1);
  });

  it('prompts for regional override when globe button is clicked', () => {
    const key = 'CD'.repeat(16);
    const channel = makeChannel(key, '#flightless', true);
    const conversation: Conversation = { type: 'channel', id: key, name: '#flightless' };
    const onSetChannelFloodScopeOverride = vi.fn();
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue('Esperance');

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        channels={[channel]}
        onSetChannelFloodScopeOverride={onSetChannelFloodScopeOverride}
      />
    );

    fireEvent.click(screen.getByTitle('Set regional override'));

    expect(promptSpy).toHaveBeenCalled();
    expect(onSetChannelFloodScopeOverride).toHaveBeenCalledWith(key, 'Esperance');
    promptSpy.mockRestore();
  });
});
