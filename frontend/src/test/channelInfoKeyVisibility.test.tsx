import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ChannelInfoPane } from '../components/ChannelInfoPane';
import type { Channel, ChannelDetail, Favorite } from '../types';

// Mock the api module
vi.mock('../api', () => ({
  api: {
    getChannelDetail: vi.fn(),
  },
}));

import { api } from '../api';
const mockGetChannelDetail = vi.mocked(api.getChannelDetail);

function makeChannel(key: string, name: string, isHashtag: boolean): Channel {
  return { key, name, is_hashtag: isHashtag, on_radio: false, last_read_at: null };
}

function makeDetail(channel: Channel): ChannelDetail {
  return {
    channel,
    message_counts: { last_1h: 0, last_24h: 0, last_48h: 0, last_7d: 0, all_time: 0 },
    first_message_at: null,
    unique_sender_count: 0,
    top_senders_24h: [],
  };
}

const noop = () => {};

const baseProps = {
  onClose: noop,
  favorites: [] as Favorite[],
  onToggleFavorite: noop,
};

describe('ChannelInfoPane key visibility', () => {
  it('shows key directly for hashtag channels', async () => {
    const key = 'AA'.repeat(16);
    const channel = makeChannel(key, '#general', true);
    mockGetChannelDetail.mockResolvedValue(makeDetail(channel));

    render(<ChannelInfoPane {...baseProps} channelKey={key} channels={[channel]} />);

    await waitFor(() => {
      expect(screen.getByText(key.toLowerCase())).toBeInTheDocument();
    });
    expect(screen.queryByText('Show Key')).not.toBeInTheDocument();
  });

  it('hides key behind "Show Key" button for private channels', async () => {
    const key = 'BB'.repeat(16);
    const channel = makeChannel(key, 'Secret', false);
    mockGetChannelDetail.mockResolvedValue(makeDetail(channel));

    render(<ChannelInfoPane {...baseProps} channelKey={key} channels={[channel]} />);

    await waitFor(() => {
      expect(screen.getByText('Secret')).toBeInTheDocument();
    });
    expect(screen.queryByText(key.toLowerCase())).not.toBeInTheDocument();
    expect(screen.getByText('Show Key')).toBeInTheDocument();
  });

  it('reveals key when "Show Key" is clicked', async () => {
    const key = 'CC'.repeat(16);
    const channel = makeChannel(key, 'Private', false);
    mockGetChannelDetail.mockResolvedValue(makeDetail(channel));

    render(<ChannelInfoPane {...baseProps} channelKey={key} channels={[channel]} />);

    await waitFor(() => {
      expect(screen.getByText('Show Key')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Show Key'));

    expect(screen.getByText(key.toLowerCase())).toBeInTheDocument();
    expect(screen.queryByText('Show Key')).not.toBeInTheDocument();
  });

  it('resets key visibility when channel changes', async () => {
    const key1 = 'DD'.repeat(16);
    const key2 = 'EE'.repeat(16);
    const ch1 = makeChannel(key1, 'Room1', false);
    const ch2 = makeChannel(key2, 'Room2', false);
    mockGetChannelDetail.mockImplementation((key) =>
      Promise.resolve(key === key1 ? makeDetail(ch1) : makeDetail(ch2))
    );

    const { rerender } = render(
      <ChannelInfoPane {...baseProps} channelKey={key1} channels={[ch1, ch2]} />
    );

    await waitFor(() => {
      expect(screen.getByText('Show Key')).toBeInTheDocument();
    });

    // Reveal key for first channel
    fireEvent.click(screen.getByText('Show Key'));
    expect(screen.getByText(key1.toLowerCase())).toBeInTheDocument();

    // Switch channel — key should be hidden again
    rerender(<ChannelInfoPane {...baseProps} channelKey={key2} channels={[ch1, ch2]} />);

    await waitFor(() => {
      expect(screen.getByText('Room2')).toBeInTheDocument();
    });
    expect(screen.queryByText(key2.toLowerCase())).not.toBeInTheDocument();
    expect(screen.getByText('Show Key')).toBeInTheDocument();
  });
});
