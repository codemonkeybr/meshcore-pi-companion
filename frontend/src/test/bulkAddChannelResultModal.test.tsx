import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { BulkAddChannelResultModal } from '../components/BulkAddChannelResultModal';

describe('BulkAddChannelResultModal', () => {
  it('renders links only for newly created rooms', () => {
    render(
      <BulkAddChannelResultModal
        open
        onClose={() => {}}
        result={{
          created_channels: [
            {
              key: 'AA'.repeat(16),
              name: '#ops',
              is_hashtag: true,
              on_radio: false,
              last_read_at: null,
              favorite: false,
              muted: false,
            },
            {
              key: 'BB'.repeat(16),
              name: '#mesh-room',
              is_hashtag: true,
              on_radio: false,
              last_read_at: null,
              favorite: false,
              muted: false,
            },
          ],
          existing_count: 3,
          invalid_names: ['bad_room'],
          decrypt_started: true,
          decrypt_total_packets: 8,
          message: 'Created 2 rooms',
        }}
      />
    );

    const opsLink = screen.getByRole('link', { name: '#ops' });
    const meshLink = screen.getByRole('link', { name: '#mesh-room' });

    expect(opsLink.getAttribute('href')).toContain('#channel/');
    expect(meshLink.getAttribute('href')).toContain('#channel/');
    expect(screen.queryByRole('link', { name: /bad_room/i })).toBeNull();
    expect(screen.getByText(/Ignored invalid channel names: bad_room/)).toBeTruthy();
  });
});
