import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { VisualizerView } from '../components/VisualizerView';
import { resetRawPacketStore, seedRawPacketStore } from '../stores/rawPacketStore';
import type { RawPacket } from '../types';

// The 3D scene needs WebGL, which jsdom does not provide.
vi.mock('../components/PacketVisualizer3D', () => ({
  PacketVisualizer3D: () => <div data-testid="packet-visualizer-3d" />,
}));

function createPacket(overrides: Partial<RawPacket> = {}): RawPacket {
  return {
    id: 1,
    timestamp: 1700000000,
    data: '000000000000',
    payload_type: 'REQ',
    snr: null,
    rssi: null,
    decrypted: false,
    decrypted_info: null,
    ...overrides,
  };
}

describe('VisualizerView packet feed', () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetRawPacketStore();
  });

  it('opens the packet analyzer when a feed packet is clicked', () => {
    seedRawPacketStore({ packets: [createPacket({ id: 7, observation_id: 21 })] });
    render(<VisualizerView contacts={[]} channels={[]} config={null} />);

    expect(screen.queryByText('Packet Details')).not.toBeInTheDocument();

    // Desktop split-pane and mobile tab both render the feed, so take the first.
    fireEvent.click(screen.getAllByRole('button', { name: /TF/ })[0]);

    expect(screen.getByText('Packet Details')).toBeInTheDocument();
  });

  it('does not render the analyzer until a packet is selected', () => {
    render(<VisualizerView contacts={[]} channels={[]} config={null} />);

    expect(screen.queryByText('Packet Details')).not.toBeInTheDocument();
  });
});
