import { describe, expect, it, vi } from 'vitest';
import { PayloadType } from '@michaelhart/meshcore-decoder';

import {
  buildPacketNetworkContext,
  createPacketNetworkState,
  ingestPacketIntoPacketNetwork,
  projectCanonicalPath,
  projectPacketNetwork,
  snapshotNeighborIds,
} from '../networkGraph/packetNetworkGraph';
import { buildLinkKey } from '../utils/visualizerUtils';
import type { Contact, RadioConfig, RawPacket } from '../types';
import { CONTACT_TYPE_REPEATER } from '../types';

const { packetFixtures } = vi.hoisted(() => ({
  packetFixtures: new Map<string, unknown>(),
}));

vi.mock('../utils/visualizerUtils', async () => {
  const actual = await vi.importActual<typeof import('../utils/visualizerUtils')>(
    '../utils/visualizerUtils'
  );

  return {
    ...actual,
    parsePacket: vi.fn(
      (hexData: string) => packetFixtures.get(hexData) ?? actual.parsePacket(hexData)
    ),
  };
});

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
    direct_path: null,
    direct_path_len: 0,
    direct_path_hash_mode: 0,
    route_override_path: null,
    route_override_len: null,
    route_override_hash_mode: null,
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
}

function createPacket(data: string): RawPacket {
  return {
    id: 1,
    observation_id: 1,
    timestamp: 1_700_000_000,
    data,
    payload_type: 'TEXT',
    snr: null,
    rssi: null,
    decrypted: false,
    decrypted_info: null,
  };
}

describe('packetNetworkGraph', () => {
  it('preserves canonical adjacency while projection hides ambiguous repeaters', () => {
    const selfKey = 'ffffffffffff0000000000000000000000000000000000000000000000000000';
    const aliceKey = 'aaaaaaaaaaaa0000000000000000000000000000000000000000000000000000';
    packetFixtures.set('dm-semantic-hide', {
      payloadType: PayloadType.TextMessage,
      messageHash: 'dm-semantic-hide',
      pathBytes: ['32'],
      srcHash: 'aaaaaaaaaaaa',
      dstHash: 'ffffffffffff',
      advertPubkey: null,
      groupTextSender: null,
      anonRequestPubkey: null,
    });

    const state = createPacketNetworkState('Me');
    const context = buildPacketNetworkContext({
      contacts: [createContact(aliceKey, 'Alice')],
      config: createConfig(selfKey),
      repeaterAdvertPaths: [],
      splitAmbiguousByTraffic: false,
      useAdvertPathHints: false,
    });

    ingestPacketIntoPacketNetwork(state, context, createPacket('dm-semantic-hide'));

    const hiddenProjection = projectPacketNetwork(state, {
      showAmbiguousNodes: false,
      showAmbiguousPaths: false,
      collapseLikelyKnownSiblingRepeaters: true,
    });
    const shownProjection = projectPacketNetwork(state, {
      showAmbiguousNodes: false,
      showAmbiguousPaths: true,
      collapseLikelyKnownSiblingRepeaters: true,
    });

    expect(snapshotNeighborIds(state)).toEqual(
      new Map([
        ['?32', ['aaaaaaaaaaaa', 'self']],
        ['aaaaaaaaaaaa', ['?32']],
        ['self', ['?32']],
      ])
    );
    expect(hiddenProjection.links.has('aaaaaaaaaaaa->self')).toBe(true);
    expect(shownProjection.links.has('?32->aaaaaaaaaaaa')).toBe(true);
    expect(shownProjection.links.has('?32->self')).toBe(true);
  });

  it('projects hidden ambiguous runs as dashed bridges but keeps later known repeaters visible', () => {
    const selfKey = 'ffffffffffff0000000000000000000000000000000000000000000000000000';
    const aliceKey = 'aaaaaaaaaaaa0000000000000000000000000000000000000000000000000000';
    const repeaterKey = '5656565656560000000000000000000000000000000000000000000000000000';

    packetFixtures.set('dm-hidden-chain', {
      payloadType: PayloadType.TextMessage,
      messageHash: 'dm-hidden-chain',
      pathBytes: ['32', '565656565656'],
      srcHash: 'aaaaaaaaaaaa',
      dstHash: 'ffffffffffff',
      advertPubkey: null,
      groupTextSender: null,
      anonRequestPubkey: null,
    });

    const state = createPacketNetworkState('Me');
    const context = buildPacketNetworkContext({
      contacts: [
        createContact(aliceKey, 'Alice'),
        createContact(repeaterKey, 'Relay B', CONTACT_TYPE_REPEATER),
      ],
      config: createConfig(selfKey),
      repeaterAdvertPaths: [],
      splitAmbiguousByTraffic: false,
      useAdvertPathHints: false,
    });

    const ingested = ingestPacketIntoPacketNetwork(state, context, createPacket('dm-hidden-chain'));

    expect(ingested?.canonicalPath).toEqual(['aaaaaaaaaaaa', '?32', '565656565656', 'self']);

    const projectedPath = projectCanonicalPath(state, ingested!.canonicalPath, {
      showAmbiguousNodes: false,
      showAmbiguousPaths: false,
      collapseLikelyKnownSiblingRepeaters: true,
    });
    const projection = projectPacketNetwork(state, {
      showAmbiguousNodes: false,
      showAmbiguousPaths: false,
      collapseLikelyKnownSiblingRepeaters: true,
    });

    expect(projectedPath.nodes).toEqual(['aaaaaaaaaaaa', '565656565656', 'self']);
    expect(Array.from(projectedPath.dashedLinkDetails.keys())).toEqual([
      '565656565656->aaaaaaaaaaaa',
    ]);
    expect(projection.links.get('565656565656->aaaaaaaaaaaa')?.hasHiddenIntermediate).toBe(true);
    expect(projection.links.get('565656565656->self')?.hasDirectObservation).toBe(true);
  });

  it('does not bridge across hidden ambiguous sender endpoints', () => {
    const selfKey = 'ffffffffffff0000000000000000000000000000000000000000000000000000';
    const repeaterOneKey = '1111111111110000000000000000000000000000000000000000000000000000';
    const repeaterTwoKey = '2222222222220000000000000000000000000000000000000000000000000000';
    const repeaterThreeKey = '3333333333330000000000000000000000000000000000000000000000000000';
    const repeaterFourKey = '4444444444440000000000000000000000000000000000000000000000000000';

    packetFixtures.set('dm-hidden-ambiguous-sender-a', {
      payloadType: PayloadType.TextMessage,
      messageHash: 'dm-hidden-ambiguous-sender-a',
      pathBytes: ['111111111111', '222222222222'],
      srcHash: '32',
      dstHash: 'ffffffffffff',
      advertPubkey: null,
      groupTextSender: null,
      anonRequestPubkey: null,
    });
    packetFixtures.set('dm-hidden-ambiguous-sender-b', {
      payloadType: PayloadType.TextMessage,
      messageHash: 'dm-hidden-ambiguous-sender-b',
      pathBytes: ['333333333333', '444444444444'],
      srcHash: '32',
      dstHash: 'ffffffffffff',
      advertPubkey: null,
      groupTextSender: null,
      anonRequestPubkey: null,
    });

    const state = createPacketNetworkState('Me');
    const context = buildPacketNetworkContext({
      contacts: [
        createContact(repeaterOneKey, 'Relay 1', CONTACT_TYPE_REPEATER),
        createContact(repeaterTwoKey, 'Relay 2', CONTACT_TYPE_REPEATER),
        createContact(repeaterThreeKey, 'Relay 3', CONTACT_TYPE_REPEATER),
        createContact(repeaterFourKey, 'Relay 4', CONTACT_TYPE_REPEATER),
      ],
      config: createConfig(selfKey),
      repeaterAdvertPaths: [],
      splitAmbiguousByTraffic: false,
      useAdvertPathHints: false,
    });

    ingestPacketIntoPacketNetwork(state, context, createPacket('dm-hidden-ambiguous-sender-a'));
    ingestPacketIntoPacketNetwork(state, context, createPacket('dm-hidden-ambiguous-sender-b'));

    const projection = projectPacketNetwork(state, {
      showAmbiguousNodes: false,
      showAmbiguousPaths: true,
      collapseLikelyKnownSiblingRepeaters: true,
    });

    expect(projection.links.has(buildLinkKey('111111111111', '222222222222'))).toBe(true);
    expect(projection.links.has(buildLinkKey('333333333333', '444444444444'))).toBe(true);
    expect(projection.links.has(buildLinkKey('111111111111', '333333333333'))).toBe(false);
    expect(projection.links.has(buildLinkKey('222222222222', '444444444444'))).toBe(false);
  });

  it('does not add a DM recipient node from destination metadata alone', () => {
    const selfKey = 'ffffffffffff0000000000000000000000000000000000000000000000000000';
    const aliceKey = 'aaaaaaaaaaaa0000000000000000000000000000000000000000000000000000';
    const bobKey = 'bbbbbbbbbbbb0000000000000000000000000000000000000000000000000000';
    const repeaterKey = '5656565656560000000000000000000000000000000000000000000000000000';

    packetFixtures.set('dm-third-party-no-dst-node', {
      payloadType: PayloadType.TextMessage,
      messageHash: 'dm-third-party-no-dst-node',
      pathBytes: ['565656565656'],
      srcHash: 'aaaaaaaaaaaa',
      dstHash: 'bbbbbbbbbbbb',
      advertPubkey: null,
      groupTextSender: null,
      anonRequestPubkey: null,
    });

    const state = createPacketNetworkState('Me');
    const context = buildPacketNetworkContext({
      contacts: [
        createContact(aliceKey, 'Alice'),
        createContact(bobKey, 'Bob'),
        createContact(repeaterKey, 'Relay', CONTACT_TYPE_REPEATER),
      ],
      config: createConfig(selfKey),
      repeaterAdvertPaths: [],
      splitAmbiguousByTraffic: false,
      useAdvertPathHints: false,
    });

    const ingested = ingestPacketIntoPacketNetwork(
      state,
      context,
      createPacket('dm-third-party-no-dst-node')
    );

    expect(ingested?.canonicalPath).toEqual(['aaaaaaaaaaaa', '565656565656', 'self']);
    expect(state.nodes.has('bbbbbbbbbbbb')).toBe(false);
    expect(snapshotNeighborIds(state)).toEqual(
      new Map([
        ['565656565656', ['aaaaaaaaaaaa', 'self']],
        ['aaaaaaaaaaaa', ['565656565656']],
        ['self', ['565656565656']],
      ])
    );
  });

  it('replays real advert packets through the semantic layer', () => {
    const state = createPacketNetworkState('Me');
    const context = buildPacketNetworkContext({
      contacts: [],
      config: createConfig('ffffffffffff0000000000000000000000000000000000000000000000000000'),
      repeaterAdvertPaths: [],
      splitAmbiguousByTraffic: false,
      useAdvertPathHints: false,
    });

    const packet = createPacket(
      '1106538B1CD273868576DC7F679B493F9AB5AC316173E1A56D3388BC3BA75F583F63AB0D1BA2A8ABD0BC6669DBF719E67E4C8517BA4E0D6F8C96A323E9D13A77F2630DED965A5C17C3EC6ED1601EEFE857749DA24E9F39CBEACD722C3708F433DB5FA9BAF0BAF9BC5B1241069290FEEB029A839EF843616E204F204D657368203220F09FA5AB'
    );
    packet.payload_type = 'ADVERT';

    const ingested = ingestPacketIntoPacketNetwork(state, context, packet);

    expect(ingested?.canonicalPath).toEqual([
      '8576dc7f679b',
      '?53',
      '?8b',
      '?1c',
      '?d2',
      '?73',
      '?86',
      'self',
    ]);
    expect(snapshotNeighborIds(state).get('?73')).toEqual(['?86', '?d2']);
  });

  it('collapses a likely ambiguous repeater into its known sibling when both share the same next hop', () => {
    const selfKey = 'ffffffffffff0000000000000000000000000000000000000000000000000000';
    const state = createPacketNetworkState('Me');
    const context = buildPacketNetworkContext({
      contacts: [
        createContact('aaaaaaaaaaaa0000000000000000000000000000000000000000000000000000', 'Alice'),
        createContact('cccccccccccc0000000000000000000000000000000000000000000000000000', 'Carol'),
        createContact(
          '3232323232320000000000000000000000000000000000000000000000000000',
          'Relay A',
          CONTACT_TYPE_REPEATER
        ),
        createContact(
          '32ababababab0000000000000000000000000000000000000000000000000000',
          'Relay B',
          CONTACT_TYPE_REPEATER
        ),
        createContact(
          '5656565656560000000000000000000000000000000000000000000000000000',
          'Relay Next',
          CONTACT_TYPE_REPEATER
        ),
      ],
      config: createConfig(selfKey),
      repeaterAdvertPaths: [
        {
          public_key: '3232323232320000000000000000000000000000000000000000000000000000',
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
      splitAmbiguousByTraffic: false,
      useAdvertPathHints: true,
    });

    packetFixtures.set('graph-ambiguous-sibling', {
      payloadType: PayloadType.TextMessage,
      messageHash: 'graph-ambiguous-sibling',
      pathBytes: ['32', '565656565656'],
      srcHash: 'aaaaaaaaaaaa',
      dstHash: 'ffffffffffff',
      advertPubkey: null,
      groupTextSender: null,
      anonRequestPubkey: null,
    });
    packetFixtures.set('graph-known-sibling', {
      payloadType: PayloadType.TextMessage,
      messageHash: 'graph-known-sibling',
      pathBytes: ['323232323232', '565656565656'],
      srcHash: 'cccccccccccc',
      dstHash: 'ffffffffffff',
      advertPubkey: null,
      groupTextSender: null,
      anonRequestPubkey: null,
    });

    ingestPacketIntoPacketNetwork(state, context, createPacket('graph-ambiguous-sibling'));
    ingestPacketIntoPacketNetwork(state, context, createPacket('graph-known-sibling'));

    const collapsed = projectPacketNetwork(state, {
      showAmbiguousNodes: false,
      showAmbiguousPaths: true,
      collapseLikelyKnownSiblingRepeaters: true,
    });
    const separated = projectPacketNetwork(state, {
      showAmbiguousNodes: false,
      showAmbiguousPaths: true,
      collapseLikelyKnownSiblingRepeaters: false,
    });

    expect(collapsed.renderedNodeIds.has('?32')).toBe(false);
    expect(collapsed.renderedNodeIds.has('323232323232')).toBe(true);
    expect(collapsed.links.has('323232323232->aaaaaaaaaaaa')).toBe(true);
    expect(separated.renderedNodeIds.has('?32')).toBe(true);
    expect(separated.links.has('?32->aaaaaaaaaaaa')).toBe(true);
  });
});
