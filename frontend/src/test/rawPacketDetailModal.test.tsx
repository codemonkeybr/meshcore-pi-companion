import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { RawPacketDetailModal } from '../components/RawPacketDetailModal';
import type { Channel, RawPacket } from '../types';

vi.mock('../components/ui/sonner', () => ({
  toast: Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  }),
}));

const { toast } = await import('../components/ui/sonner');
const mockToast = toast as unknown as {
  success: ReturnType<typeof vi.fn>;
};

const BOT_CHANNEL: Channel = {
  key: 'eb50a1bcb3e4e5d7bf69a57c9dada211',
  name: '#bot',
  is_hashtag: true,
  on_radio: false,
  last_read_at: null,
  favorite: false,
  muted: false,
};

const BOT_PACKET: RawPacket = {
  id: 1,
  observation_id: 10,
  timestamp: 1_700_000_000,
  data: '15833fa002860ccae0eed9ca78b9ab0775d477c1f6490a398bf4edc75240',
  decrypted: false,
  payload_type: 'GroupText',
  rssi: -72,
  snr: 5.5,
  decrypted_info: null,
};

// TransportFlood ACK: header 0C (route=0 TransportFlood, type=3 ACK, ver=0),
// transport codes 3412 7856 (LE: 0x1234, 0x5678), path_len 00, ACK checksum AABBCCDD
const SCOPED_PACKET: RawPacket = {
  id: 2,
  timestamp: 1_700_000_000,
  data: '0C3412785600AABBCCDD',
  decrypted: false,
  payload_type: 'Ack',
  rssi: -80,
  snr: 3.0,
  decrypted_info: null,
};

describe('RawPacketDetailModal', () => {
  it('copies the full packet hex to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, {
      clipboard: { writeText },
    });

    render(<RawPacketDetailModal packet={BOT_PACKET} channels={[BOT_CHANNEL]} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));

    expect(writeText).toHaveBeenCalledWith(BOT_PACKET.data);
    expect(mockToast.success).toHaveBeenCalledWith('Packet hex copied!');
  });

  it('renders path hops as nowrap arrow-delimited groups and links hover state to the full packet hex', () => {
    render(<RawPacketDetailModal packet={BOT_PACKET} channels={[BOT_CHANNEL]} onClose={vi.fn()} />);

    const pathDescription = screen.getByText(
      'Historical route taken (3-byte hashes added as packet floods through network)'
    );
    const pathFieldBox = pathDescription.closest('[class*="rounded-lg"]');
    expect(pathFieldBox).not.toBeNull();

    const pathField = within(pathFieldBox as HTMLElement);
    expect(pathField.getByText('3FA002 →')).toHaveClass('whitespace-nowrap');
    expect(pathField.getByText('860CCA →')).toHaveClass('whitespace-nowrap');
    expect(pathField.getByText('E0EED9')).toHaveClass('whitespace-nowrap');

    const pathRun = screen.getByText('3F A0 02 86 0C CA E0 EE D9');
    const idleClassName = pathRun.className;

    fireEvent.mouseEnter(pathFieldBox as HTMLElement);
    expect(pathRun.className).not.toBe(idleClassName);

    fireEvent.mouseLeave(pathFieldBox as HTMLElement);
    expect(pathRun.className).toBe(idleClassName);
  });

  it('shows scope card with transport codes for scoped packets', () => {
    render(<RawPacketDetailModal packet={SCOPED_PACKET} channels={[]} onClose={vi.fn()} />);

    expect(screen.getByText('Scope')).toBeInTheDocument();
    expect(screen.getByText('Regional')).toBeInTheDocument();
    expect(screen.getByText('0x1234, 0x5678')).toBeInTheDocument();
  });

  it('does not show scope card for non-transport packets', () => {
    render(<RawPacketDetailModal packet={BOT_PACKET} channels={[BOT_CHANNEL]} onClose={vi.fn()} />);

    expect(screen.queryByText('Scope')).not.toBeInTheDocument();
  });
});
