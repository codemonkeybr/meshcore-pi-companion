import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PayloadType } from '@michaelhart/meshcore-decoder';

import type { Contact, ContactAdvertPathSummary, RadioConfig, RawPacket } from '../types';
import { CONTACT_TYPE_REPEATER } from '../types';
import { buildLinkKey } from '../utils/visualizerUtils';

const { packetFixtures } = vi.hoisted(() => ({
  packetFixtures: new Map<string, unknown>(),
}));

vi.mock('../utils/visualizerUtils', async () => {
  const actual = await vi.importActual<typeof import('../utils/visualizerUtils')>(
    '../utils/visualizerUtils'
  );

  return {
    ...actual,
    parsePacket: vi.fn((hexData: string) => packetFixtures.get(hexData) ?? null),
  };
});

import { useVisualizerData3D } from '../components/visualizer/useVisualizerData3D';

function createConfig(publicKey: string): RadioConfig {
  return {
    public_key: publicKey,
    name: 'Me',
    lat: 0,
    lon: 0,
    tx_power: 0,
    max_tx_power: 0,
    radio: { freq: 0, bw: 0, sf: 0, cr: 0 },
    path_hash_mode: 0,
    path_hash_mode_supported: true,
    advert_location_source: 'off',
  };
}

function createContact(publicKey: string, name: string, type = 1): Contact {
  return {
    public_key: publicKey,
    name,
    type,
    flags: 0,
    last_path: null,
    last_path_len: 0,
    out_path_hash_mode: 0,
    route_override_path: null,
    route_override_len: null,
    route_override_hash_mode: null,
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

function createPacket(
  data: string,
  { id = 1, observationId = id }: { id?: number; observationId?: number } = {}
): RawPacket {
  return {
    id,
    observation_id: observationId,
    timestamp: 1_700_000_000,
    data,
    payload_type: 'TEXT',
    snr: null,
    rssi: null,
    decrypted: false,
    decrypted_info: null,
  };
}

function renderVisualizerData({
  packets,
  contacts,
  config,
  showAmbiguousPaths = false,
  showAmbiguousNodes = false,
  collapseLikelyKnownSiblingRepeaters = true,
  repeaterAdvertPaths = [],
  useAdvertPathHints = false,
}: {
  packets: RawPacket[];
  contacts: Contact[];
  config: RadioConfig;
  showAmbiguousPaths?: boolean;
  showAmbiguousNodes?: boolean;
  collapseLikelyKnownSiblingRepeaters?: boolean;
  repeaterAdvertPaths?: ContactAdvertPathSummary[];
  useAdvertPathHints?: boolean;
}) {
  return renderHook(() =>
    useVisualizerData3D({
      packets,
      contacts,
      config,
      repeaterAdvertPaths,
      showAmbiguousPaths,
      showAmbiguousNodes,
      useAdvertPathHints,
      collapseLikelyKnownSiblingRepeaters,
      splitAmbiguousByTraffic: false,
      chargeStrength: -200,
      letEmDrift: false,
      particleSpeedMultiplier: 1,
      observationWindowSec: 15,
      pruneStaleNodes: false,
      pruneStaleMinutes: 5,
    })
  );
}

afterEach(() => {
  packetFixtures.clear();
});

describe('useVisualizerData3D', () => {
  it('hard-pins the self node at the origin', async () => {
    const selfKey = 'ffffffffffff0000000000000000000000000000000000000000000000000000';

    const { result } = renderVisualizerData({
      packets: [],
      contacts: [],
      config: createConfig(selfKey),
    });

    await waitFor(() => expect(result.current.nodes.get('self')).toBeDefined());

    const selfNode = result.current.nodes.get('self');
    expect(selfNode).toMatchObject({
      x: 0,
      y: 0,
      z: 0,
      fx: 0,
      fy: 0,
      fz: 0,
      vx: 0,
      vy: 0,
      vz: 0,
    });
  });

  it('keeps canonical adjacency stable when ambiguous repeaters are shown or hidden', async () => {
    const selfKey = 'ffffffffffff0000000000000000000000000000000000000000000000000000';
    const aliceKey = 'aaaaaaaaaaaa0000000000000000000000000000000000000000000000000000';
    packetFixtures.set('dm-canonical-stable', {
      payloadType: PayloadType.TextMessage,
      messageHash: 'dm-canonical-stable',
      pathBytes: ['32'],
      srcHash: 'aaaaaaaaaaaa',
      dstHash: 'ffffffffffff',
      advertPubkey: null,
      groupTextSender: null,
      anonRequestPubkey: null,
    });

    const packets = [createPacket('dm-canonical-stable')];
    const contacts = [createContact(aliceKey, 'Alice')];

    const hidden = renderVisualizerData({
      packets,
      contacts,
      config: createConfig(selfKey),
      showAmbiguousPaths: false,
    });
    const shown = renderVisualizerData({
      packets,
      contacts,
      config: createConfig(selfKey),
      showAmbiguousPaths: true,
    });

    await waitFor(() =>
      expect(hidden.result.current.canonicalNeighborIds.get('aaaaaaaaaaaa')).toEqual(['?32'])
    );
    await waitFor(() =>
      expect(shown.result.current.canonicalNeighborIds.get('aaaaaaaaaaaa')).toEqual(['?32'])
    );

    expect(hidden.result.current.canonicalNeighborIds).toEqual(
      shown.result.current.canonicalNeighborIds
    );
    expect(hidden.result.current.links.has(buildLinkKey('aaaaaaaaaaaa', 'self'))).toBe(true);
    expect(hidden.result.current.links.has(buildLinkKey('aaaaaaaaaaaa', '?32'))).toBe(false);
    expect(shown.result.current.links.has(buildLinkKey('aaaaaaaaaaaa', '?32'))).toBe(true);
  });

  it('marks compressed hidden-repeater routes as dashed links instead of direct solid links', async () => {
    const selfKey = 'ffffffffffff0000000000000000000000000000000000000000000000000000';
    const aliceKey = 'aaaaaaaaaaaa0000000000000000000000000000000000000000000000000000';

    packetFixtures.set('dm-hidden-hop', {
      payloadType: PayloadType.TextMessage,
      messageHash: 'dm-hidden-hop',
      pathBytes: ['32'],
      srcHash: 'aaaaaaaaaaaa',
      dstHash: 'ffffffffffff',
      advertPubkey: null,
      groupTextSender: null,
      anonRequestPubkey: null,
    });

    const { result } = renderVisualizerData({
      packets: [createPacket('dm-hidden-hop')],
      contacts: [createContact(aliceKey, 'Alice')],
      config: createConfig(selfKey),
    });

    await waitFor(() => expect(result.current.links.size).toBe(1));

    const link = result.current.links.get(buildLinkKey('aaaaaaaaaaaa', 'self'));
    expect(link).toBeDefined();
    expect(link?.hasHiddenIntermediate).toBe(true);
    expect(link?.hasDirectObservation).toBe(false);
    expect(result.current.canonicalNeighborIds.get('aaaaaaaaaaaa')).toEqual(['?32']);
    expect(result.current.canonicalNeighborIds.get('self')).toEqual(['?32']);
    expect(result.current.renderedNodeIds.has('?32')).toBe(false);
  });

  it('does not place a resolved outgoing DM destination into topology', async () => {
    const selfKey = 'ffffffffffff0000000000000000000000000000000000000000000000000000';
    const bobKey = 'bbbbbbbbbbbb0000000000000000000000000000000000000000000000000000';
    const repeaterKey = '3232323232320000000000000000000000000000000000000000000000000000';

    packetFixtures.set('dm-outgoing-known-dst', {
      payloadType: PayloadType.TextMessage,
      messageHash: 'dm-outgoing-known-dst',
      pathBytes: ['323232323232'],
      srcHash: 'ffffffffffff',
      dstHash: 'bbbbbbbbbbbb',
      advertPubkey: null,
      groupTextSender: null,
      anonRequestPubkey: null,
    });

    const { result } = renderVisualizerData({
      packets: [createPacket('dm-outgoing-known-dst')],
      contacts: [
        createContact(bobKey, 'Bob'),
        createContact(repeaterKey, 'Relay', CONTACT_TYPE_REPEATER),
      ],
      config: createConfig(selfKey),
      showAmbiguousPaths: true,
    });

    await waitFor(() => expect(result.current.links.size).toBe(1));

    expect(result.current.links.has(buildLinkKey('self', '323232323232'))).toBe(true);
    expect(result.current.links.has(buildLinkKey('323232323232', 'bbbbbbbbbbbb'))).toBe(false);
    expect(result.current.links.has(buildLinkKey('self', 'bbbbbbbbbbbb'))).toBe(false);
    expect(result.current.renderedNodeIds.has('bbbbbbbbbbbb')).toBe(false);
  });

  it('does not place a third-party DM destination into topology', async () => {
    const selfKey = 'ffffffffffff0000000000000000000000000000000000000000000000000000';
    const aliceKey = 'aaaaaaaaaaaa0000000000000000000000000000000000000000000000000000';
    const bobKey = 'bbbbbbbbbbbb0000000000000000000000000000000000000000000000000000';
    const repeaterKey = '3232323232320000000000000000000000000000000000000000000000000000';

    packetFixtures.set('dm-third-party-known-dst', {
      payloadType: PayloadType.TextMessage,
      messageHash: 'dm-third-party-known-dst',
      pathBytes: ['323232323232'],
      srcHash: 'aaaaaaaaaaaa',
      dstHash: 'bbbbbbbbbbbb',
      advertPubkey: null,
      groupTextSender: null,
      anonRequestPubkey: null,
    });

    const { result } = renderVisualizerData({
      packets: [createPacket('dm-third-party-known-dst')],
      contacts: [
        createContact(aliceKey, 'Alice'),
        createContact(bobKey, 'Bob'),
        createContact(repeaterKey, 'Relay', CONTACT_TYPE_REPEATER),
      ],
      config: createConfig(selfKey),
      showAmbiguousPaths: true,
    });

    await waitFor(() => expect(result.current.links.size).toBe(2));

    expect(result.current.links.has(buildLinkKey('aaaaaaaaaaaa', '323232323232'))).toBe(true);
    expect(result.current.links.has(buildLinkKey('323232323232', 'self'))).toBe(true);
    expect(result.current.links.has(buildLinkKey('323232323232', 'bbbbbbbbbbbb'))).toBe(false);
    expect(result.current.renderedNodeIds.has('bbbbbbbbbbbb')).toBe(false);
  });

  it('collapses a high-confidence ambiguous repeater into its known sibling when both share the same next hop', async () => {
    const selfKey = 'ffffffffffff0000000000000000000000000000000000000000000000000000';
    const aliceKey = 'aaaaaaaaaaaa0000000000000000000000000000000000000000000000000000';
    const carolKey = 'cccccccccccc0000000000000000000000000000000000000000000000000000';
    const knownRelayKey = '3232323232320000000000000000000000000000000000000000000000000000';
    const otherRelayKey = '32ababababab0000000000000000000000000000000000000000000000000000';
    const nextRelayKey = '5656565656560000000000000000000000000000000000000000000000000000';

    packetFixtures.set('dm-ambiguous-sibling', {
      payloadType: PayloadType.TextMessage,
      messageHash: 'dm-ambiguous-sibling',
      pathBytes: ['32', '565656565656'],
      srcHash: 'aaaaaaaaaaaa',
      dstHash: 'ffffffffffff',
      advertPubkey: null,
      groupTextSender: null,
      anonRequestPubkey: null,
    });
    packetFixtures.set('dm-known-sibling', {
      payloadType: PayloadType.TextMessage,
      messageHash: 'dm-known-sibling',
      pathBytes: ['323232323232', '565656565656'],
      srcHash: 'cccccccccccc',
      dstHash: 'ffffffffffff',
      advertPubkey: null,
      groupTextSender: null,
      anonRequestPubkey: null,
    });

    const sharedArgs = {
      packets: [
        createPacket('dm-ambiguous-sibling', { id: 1, observationId: 1 }),
        createPacket('dm-known-sibling', { id: 2, observationId: 2 }),
      ],
      contacts: [
        createContact(aliceKey, 'Alice'),
        createContact(carolKey, 'Carol'),
        createContact(knownRelayKey, 'Relay A', CONTACT_TYPE_REPEATER),
        createContact(otherRelayKey, 'Relay B', CONTACT_TYPE_REPEATER),
        createContact(nextRelayKey, 'Relay Next', CONTACT_TYPE_REPEATER),
      ],
      config: createConfig(selfKey),
      showAmbiguousPaths: true,
      useAdvertPathHints: true,
      repeaterAdvertPaths: [
        {
          public_key: knownRelayKey,
          paths: [
            {
              path: '',
              path_len: 1,
              next_hop: '565656565656',
              first_seen: 1,
              last_seen: 2,
              heard_count: 4,
            },
          ],
        },
      ],
    };

    const collapsed = renderVisualizerData({
      ...sharedArgs,
      collapseLikelyKnownSiblingRepeaters: true,
    });
    const separated = renderVisualizerData({
      ...sharedArgs,
      collapseLikelyKnownSiblingRepeaters: false,
    });

    await waitFor(() =>
      expect(collapsed.result.current.renderedNodeIds.has('323232323232')).toBe(true)
    );
    await waitFor(() => expect(separated.result.current.renderedNodeIds.has('?32')).toBe(true));

    expect(collapsed.result.current.renderedNodeIds.has('?32')).toBe(false);
    expect(collapsed.result.current.links.has(buildLinkKey('aaaaaaaaaaaa', '323232323232'))).toBe(
      true
    );
    expect(collapsed.result.current.links.has(buildLinkKey('323232323232', '565656565656'))).toBe(
      true
    );
    expect(separated.result.current.renderedNodeIds.has('?32')).toBe(true);
    expect(separated.result.current.links.has(buildLinkKey('aaaaaaaaaaaa', '?32'))).toBe(true);
  });

  it('picks back up with known repeaters after hiding ambiguous repeater segments', async () => {
    const selfKey = 'ffffffffffff0000000000000000000000000000000000000000000000000000';
    const aliceKey = 'aaaaaaaaaaaa0000000000000000000000000000000000000000000000000000';
    const repeaterKey = '5656565656560000000000000000000000000000000000000000000000000000';

    packetFixtures.set('dm-hidden-then-known', {
      payloadType: PayloadType.TextMessage,
      messageHash: 'dm-hidden-then-known',
      pathBytes: ['32', '565656565656'],
      srcHash: 'aaaaaaaaaaaa',
      dstHash: 'ffffffffffff',
      advertPubkey: null,
      groupTextSender: null,
      anonRequestPubkey: null,
    });

    const { result } = renderVisualizerData({
      packets: [createPacket('dm-hidden-then-known')],
      contacts: [
        createContact(aliceKey, 'Alice'),
        createContact(repeaterKey, 'Relay B', CONTACT_TYPE_REPEATER),
      ],
      config: createConfig(selfKey),
      showAmbiguousPaths: false,
    });

    await waitFor(() => expect(result.current.links.size).toBe(2));

    expect(result.current.links.has(buildLinkKey('aaaaaaaaaaaa', '565656565656'))).toBe(true);
    expect(result.current.links.has(buildLinkKey('565656565656', 'self'))).toBe(true);
    expect(result.current.links.has(buildLinkKey('aaaaaaaaaaaa', 'self'))).toBe(false);
    expect(result.current.renderedNodeIds.has('565656565656')).toBe(true);
    expect(result.current.renderedNodeIds.has('?32')).toBe(false);
    expect(result.current.canonicalNeighborIds.get('?32')).toEqual(
      expect.arrayContaining(['aaaaaaaaaaaa', '565656565656'])
    );
  });

  it('does not create a fake self edge for an unresolved outgoing direct DM', async () => {
    const selfKey = 'ffffffffffff0000000000000000000000000000000000000000000000000000';

    packetFixtures.set('dm-outgoing-unknown-dst', {
      payloadType: PayloadType.TextMessage,
      messageHash: 'dm-outgoing-unknown-dst',
      pathBytes: [],
      srcHash: 'ffffffffffff',
      dstHash: 'cccccccccccc',
      advertPubkey: null,
      groupTextSender: null,
      anonRequestPubkey: null,
    });

    const { result } = renderVisualizerData({
      packets: [createPacket('dm-outgoing-unknown-dst')],
      contacts: [],
      config: createConfig(selfKey),
    });

    await waitFor(() => expect(result.current.stats.processed).toBe(1));

    expect(result.current.links.size).toBe(0);
    expect(Array.from(result.current.nodes.keys())).toEqual(['self']);
  });
});
