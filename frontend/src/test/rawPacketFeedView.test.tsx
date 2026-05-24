import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { RawPacketFeedView } from '../components/RawPacketFeedView';
import type { RawPacketStatsSessionState } from '../utils/rawPacketStats';
import type { Channel, Contact, RawPacket } from '../types';

const GROUP_TEXT_PACKET_HEX =
  '1500E69C7A89DD0AF6A2D69F5823B88F9720731E4B887C56932BF889255D8D926D99195927144323A42DD8A158F878B518B8304DF55E80501C7D02A9FFD578D3518283156BBA257BF8413E80A237393B2E4149BBBC864371140A9BBC4E23EB9BF203EF0D029214B3E3AAC3C0295690ACDB89A28619E7E5F22C83E16073AD679D25FA904D07E5ACF1DB5A7C77D7E1719FB9AE5BF55541EE0D7F59ED890E12CF0FEED6700818';

const TEST_CHANNEL: Channel = {
  key: '7ABA109EDCF304A84433CB71D0F3AB73',
  name: '#six77',
  is_hashtag: true,
  on_radio: false,
  last_read_at: null,
  favorite: false,
  muted: false,
};

const COLLIDING_TEST_CHANNEL: Channel = {
  ...TEST_CHANNEL,
  name: '#collision',
};

function createSession(
  overrides: Partial<RawPacketStatsSessionState> = {}
): RawPacketStatsSessionState {
  return {
    sessionStartedAt: 1_700_000_000_000,
    totalObservedPackets: 3,
    trimmedObservationCount: 0,
    observations: [
      {
        observationKey: 'obs-1',
        timestamp: 1_700_000_000,
        payloadType: 'Advert',
        routeType: 'Flood',
        decrypted: false,
        rssi: -70,
        snr: 6,
        sourceKey: 'AA11',
        sourceLabel: 'AA11',
        pathTokenCount: 1,
        pathSignature: '01',
      },
      {
        observationKey: 'obs-2',
        timestamp: 1_700_000_030,
        payloadType: 'TextMessage',
        routeType: 'Direct',
        decrypted: true,
        rssi: -66,
        snr: 7,
        sourceKey: 'BB22',
        sourceLabel: 'BB22',
        pathTokenCount: 0,
        pathSignature: null,
      },
      {
        observationKey: 'obs-3',
        timestamp: 1_700_000_050,
        payloadType: 'Ack',
        routeType: 'Direct',
        decrypted: true,
        rssi: -80,
        snr: 4,
        sourceKey: 'BB22',
        sourceLabel: 'BB22',
        pathTokenCount: 0,
        pathSignature: null,
      },
    ],
    ...overrides,
  };
}

function createContact(overrides: Partial<Contact> = {}): Contact {
  return {
    public_key: 'aa11bb22cc33' + '0'.repeat(52),
    name: 'Alpha',
    type: 1,
    flags: 0,
    direct_path: null,
    direct_path_len: 0,
    direct_path_hash_mode: 0,
    last_advert: 1_700_000_000,
    lat: null,
    lon: null,
    last_seen: null,
    on_radio: false,
    favorite: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: null,
    ...overrides,
  };
}

function renderView({
  packets = [],
  contacts = [],
  channels = [],
  rawPacketStatsSession = createSession(),
}: {
  packets?: RawPacket[];
  contacts?: Contact[];
  channels?: Channel[];
  rawPacketStatsSession?: RawPacketStatsSessionState;
} = {}) {
  return render(
    <RawPacketFeedView
      packets={packets}
      rawPacketStatsSession={rawPacketStatsSession}
      contacts={contacts}
      channels={channels}
    />
  );
}

describe('RawPacketFeedView', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('opens a stats drawer with window controls and grouped summaries', () => {
    renderView();

    expect(screen.getByText('Raw Packet Feed')).toBeInTheDocument();
    expect(screen.queryByText('Packet Types')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /show stats/i }));

    expect(screen.getByLabelText('Stats window')).toBeInTheDocument();
    expect(screen.getByText('Packet Types')).toBeInTheDocument();
    expect(screen.getByText('Hop Byte Width')).toBeInTheDocument();
    expect(screen.getByText('Most-Heard Neighbors')).toBeInTheDocument();
    expect(screen.getByText('Traffic Timeline')).toBeInTheDocument();
  });

  it('analyzes a pasted raw packet without adding it to the live feed', () => {
    renderView({ channels: [TEST_CHANNEL] });

    fireEvent.click(screen.getByRole('button', { name: 'Analyze Packet' }));

    expect(screen.getByRole('heading', { name: 'Analyze Packet' })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Packet Hex'), {
      target: { value: GROUP_TEXT_PACKET_HEX },
    });

    expect(screen.getByText('Full packet hex')).toBeInTheDocument();
    expect(screen.getByText('Packet fields')).toBeInTheDocument();
    expect(screen.getByText('Payload fields')).toBeInTheDocument();
  });

  it('shows stats by default on desktop', () => {
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation((query: string) => ({
        matches: query === '(min-width: 768px)',
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }))
    );

    renderView();

    expect(screen.getByText('Packet Types')).toBeInTheDocument();
    expect(screen.getByText('Hop Byte Width')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /hide stats/i })).toBeInTheDocument();
  });

  it('refreshes coverage when packet or session props update without counter deltas', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2024-01-01T00:00:30Z'));

    const initialPackets: RawPacket[] = [];
    const nextPackets: RawPacket[] = [
      {
        id: 1,
        timestamp: 1_704_067_255,
        data: '00',
        decrypted: false,
        payload_type: 'Unknown',
        rssi: null,
        snr: null,
        observation_id: 1,
        decrypted_info: null,
      },
    ];
    const initialSession = createSession({
      sessionStartedAt: Date.parse('2024-01-01T00:00:00Z'),
      totalObservedPackets: 10,
      trimmedObservationCount: 1,
      observations: [
        {
          observationKey: 'obs-1',
          timestamp: 1_704_067_220,
          payloadType: 'Advert',
          routeType: 'Flood',
          decrypted: false,
          rssi: -70,
          snr: 6,
          sourceKey: 'AA11',
          sourceLabel: 'AA11',
          pathTokenCount: 1,
          pathSignature: '01',
        },
      ],
    });

    const { rerender } = renderView({
      packets: initialPackets,
      rawPacketStatsSession: initialSession,
      contacts: [],
    });

    fireEvent.click(screen.getByRole('button', { name: /show stats/i }));
    fireEvent.change(screen.getByLabelText('Stats window'), { target: { value: '1m' } });
    expect(screen.getByText(/only covered for 10 sec/i)).toBeInTheDocument();

    vi.setSystemTime(new Date('2024-01-01T00:01:10Z'));
    rerender(
      <RawPacketFeedView
        packets={nextPackets}
        rawPacketStatsSession={initialSession}
        contacts={[]}
        channels={[]}
      />
    );
    expect(screen.getByText(/only covered for 50 sec/i)).toBeInTheDocument();

    vi.setSystemTime(new Date('2024-01-01T00:01:30Z'));
    const nextSession = {
      ...initialSession,
      sessionStartedAt: Date.parse('2024-01-01T00:01:00Z'),
      observations: [
        {
          ...initialSession.observations[0],
          timestamp: 1_704_067_280,
        },
      ],
    };
    rerender(
      <RawPacketFeedView
        packets={nextPackets}
        rawPacketStatsSession={nextSession}
        contacts={[]}
        channels={[]}
      />
    );
    expect(screen.getByText(/only covered for 10 sec/i)).toBeInTheDocument();

    vi.useRealTimers();
  });

  it('resolves neighbor labels from matching contacts when identity is available', () => {
    renderView({
      rawPacketStatsSession: createSession({
        totalObservedPackets: 1,
        observations: [
          {
            observationKey: 'obs-1',
            timestamp: 1_700_000_000,
            payloadType: 'Advert',
            routeType: 'Flood',
            decrypted: false,
            rssi: -70,
            snr: 6,
            sourceKey: 'AA11BB22CC33',
            sourceLabel: 'AA11BB22CC33',
            pathTokenCount: 1,
            pathSignature: '01',
          },
        ],
      }),
      contacts: [createContact()],
    });

    fireEvent.click(screen.getByRole('button', { name: /show stats/i }));
    fireEvent.change(screen.getByLabelText('Stats window'), { target: { value: 'session' } });
    expect(screen.getAllByText('Alpha').length).toBeGreaterThan(0);
    expect(screen.getByText('Strongest Neighbor')).toBeInTheDocument();
    expect(screen.getByText('-70 dBm best heard')).toBeInTheDocument();
  });

  it('marks unresolved neighbor identities explicitly', () => {
    renderView({
      rawPacketStatsSession: createSession({
        totalObservedPackets: 1,
        observations: [
          {
            observationKey: 'obs-1',
            timestamp: 1_700_000_000,
            payloadType: 'Advert',
            routeType: 'Flood',
            decrypted: false,
            rssi: -70,
            snr: 6,
            sourceKey: 'DEADBEEF1234',
            sourceLabel: 'DEADBEEF1234',
            pathTokenCount: 1,
            pathSignature: '01',
          },
        ],
      }),
      contacts: [],
    });

    fireEvent.click(screen.getByRole('button', { name: /show stats/i }));
    fireEvent.change(screen.getByLabelText('Stats window'), { target: { value: 'session' } });
    expect(screen.getAllByText('Identity not resolvable').length).toBeGreaterThan(0);
  });

  it('collapses uniquely resolved hash buckets into the same visible contact row', () => {
    const alphaContact = createContact({
      public_key: 'aa11bb22cc33' + '0'.repeat(52),
      name: 'Alpha',
    });

    renderView({
      rawPacketStatsSession: createSession({
        totalObservedPackets: 2,
        observations: [
          {
            observationKey: 'obs-1',
            timestamp: 1_700_000_000,
            payloadType: 'TextMessage',
            routeType: 'Direct',
            decrypted: true,
            rssi: -70,
            snr: 6,
            sourceKey: 'hash1:AA',
            sourceLabel: 'AA',
            pathTokenCount: 0,
            pathSignature: null,
          },
          {
            observationKey: 'obs-2',
            timestamp: 1_700_000_030,
            payloadType: 'TextMessage',
            routeType: 'Direct',
            decrypted: true,
            rssi: -67,
            snr: 7,
            sourceKey: alphaContact.public_key.toUpperCase(),
            sourceLabel: alphaContact.public_key.slice(0, 12).toUpperCase(),
            pathTokenCount: 0,
            pathSignature: null,
          },
        ],
      }),
      contacts: [alphaContact],
    });

    fireEvent.click(screen.getByRole('button', { name: /show stats/i }));
    fireEvent.change(screen.getByLabelText('Stats window'), { target: { value: 'session' } });

    expect(screen.getAllByText('Alpha').length).toBeGreaterThan(0);
    expect(screen.queryByText('Identity not resolvable')).not.toBeInTheDocument();
  });

  it('opens a packet detail modal from the raw feed and decrypts channel messages when a key is loaded', () => {
    renderView({
      packets: [
        {
          id: 1,
          observation_id: 10,
          timestamp: 1_700_000_000,
          data: GROUP_TEXT_PACKET_HEX,
          decrypted: false,
          payload_type: 'GroupText',
          rssi: -72,
          snr: 5.5,
          decrypted_info: null,
        },
      ],
      channels: [TEST_CHANNEL],
    });

    fireEvent.click(screen.getByRole('button', { name: /gt from flightless/i }));

    expect(screen.getByText('Packet Details')).toBeInTheDocument();
    expect(screen.getByText('Payload fields')).toBeInTheDocument();
    expect(screen.getByText('Full packet hex')).toBeInTheDocument();
    expect(screen.getByText('#six77')).toBeInTheDocument();
    expect(screen.getByText(/bytes · decrypted/i)).toBeInTheDocument();
    expect(screen.getAllByText(/sender: flightless/i).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/hello there; this hashtag room is essentially public/i)
    ).toBeInTheDocument();
  });

  it('does not guess a channel name when multiple loaded channels collide on the group hash', () => {
    renderView({
      packets: [
        {
          id: 1,
          observation_id: 10,
          timestamp: 1_700_000_000,
          data: GROUP_TEXT_PACKET_HEX,
          decrypted: false,
          payload_type: 'GroupText',
          rssi: -72,
          snr: 5.5,
          decrypted_info: null,
        },
      ],
      channels: [TEST_CHANNEL, COLLIDING_TEST_CHANNEL],
    });

    fireEvent.click(screen.getByRole('button', { name: /gt from flightless/i }));

    expect(screen.getByText(/channel hash e6/i)).toBeInTheDocument();
    expect(screen.queryByText('#six77')).not.toBeInTheDocument();
    expect(screen.queryByText('#collision')).not.toBeInTheDocument();
  });
});
