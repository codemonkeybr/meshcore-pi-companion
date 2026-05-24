import { describe, it, expect } from 'vitest';
import {
  parsePathHops,
  extractPacketPayloadHex,
  findContactsByPrefix,
  calculateDistance,
  formatRouteLabel,
  formatRoutingOverrideInput,
  getEffectiveContactRoute,
  isValidLocation,
  resolvePath,
  formatDistance,
  formatHopCounts,
} from '../utils/pathUtils';
import type { Contact, RadioConfig } from '../types';
import { CONTACT_TYPE_REPEATER } from '../types';

// Helper to create mock contacts
function createContact(overrides: Partial<Contact> = {}): Contact {
  return {
    public_key: 'AAAAAAAAAAAABBBBBBBBBBBBCCCCCCCCCCCCDDDDDDDDDDDDEEEEEEEEEEEE',
    name: 'Test Contact',
    type: CONTACT_TYPE_REPEATER,
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
    ...overrides,
  };
}

// Helper to create mock config
function createConfig(overrides: Partial<RadioConfig> = {}): RadioConfig {
  return {
    public_key: 'FFFFFFFFFFFFEEEEEEEEEEEEDDDDDDDDDDDDCCCCCCCCCCCCBBBBBBBBBBBB',
    name: 'MyRadio',
    lat: 40.7128,
    lon: -74.006,
    tx_power: 10,
    max_tx_power: 20,
    radio: { freq: 915, bw: 250, sf: 10, cr: 8 },
    path_hash_mode: 0,
    path_hash_mode_supported: false,
    ...overrides,
  };
}

describe('parsePathHops', () => {
  it('returns empty array for null/empty path', () => {
    expect(parsePathHops(null)).toEqual([]);
    expect(parsePathHops(undefined)).toEqual([]);
    expect(parsePathHops('')).toEqual([]);
  });

  it('parses single hop', () => {
    expect(parsePathHops('1A')).toEqual(['1A']);
  });

  it('parses multiple hops', () => {
    expect(parsePathHops('1A2B3C')).toEqual(['1A', '2B', '3C']);
  });

  it('converts to uppercase', () => {
    expect(parsePathHops('1a2b')).toEqual(['1A', '2B']);
  });

  it('handles odd length by ignoring last character', () => {
    expect(parsePathHops('1A2B3')).toEqual(['1A', '2B']);
  });

  it('parses 2-byte hops when hopCount is provided', () => {
    // 8 hex chars / 2 hops = 4 chars per hop (2 bytes)
    expect(parsePathHops('AABBCCDD', 2)).toEqual(['AABB', 'CCDD']);
  });

  it('parses 3-byte hops when hopCount is provided', () => {
    // 12 hex chars / 2 hops = 6 chars per hop (3 bytes)
    expect(parsePathHops('AABBCCDDEEFF', 2)).toEqual(['AABBCC', 'DDEEFF']);
  });

  it('parses single 2-byte hop', () => {
    expect(parsePathHops('AABB', 1)).toEqual(['AABB']);
  });

  it('parses single 3-byte hop', () => {
    expect(parsePathHops('AABBCC', 1)).toEqual(['AABBCC']);
  });

  it('falls back to 2-char chunks when hopCount does not divide evenly', () => {
    // 6 hex chars / 2 hops = 3 chars per hop (odd, invalid)
    expect(parsePathHops('1A2B3C', 2)).toEqual(['1A', '2B', '3C']);
  });

  it('falls back to 2-char chunks when hopCount is null', () => {
    expect(parsePathHops('AABBCCDD', null)).toEqual(['AA', 'BB', 'CC', 'DD']);
  });

  it('falls back to 2-char chunks when hopCount is 0', () => {
    expect(parsePathHops('AABB', 0)).toEqual(['AA', 'BB']);
  });

  it('handles 2-byte hops with many hops', () => {
    // 3 hops × 4 chars = 12 hex chars
    expect(parsePathHops('AABB11223344', 3)).toEqual(['AABB', '1122', '3344']);
  });
});

describe('extractPacketPayloadHex', () => {
  it('extracts payload from legacy 1-byte-hop packet', () => {
    expect(extractPacketPayloadHex('0902AABB48656C6C6F')).toBe('48656C6C6F');
  });

  it('extracts payload from 2-byte-hop packet', () => {
    expect(extractPacketPayloadHex('0942AABBCCDD48656C6C6F')).toBe('48656C6C6F');
  });

  it('rejects reserved mode 3', () => {
    expect(extractPacketPayloadHex('09C1AABBCCDDEEFF')).toBeNull();
  });

  it('rejects oversized path encoding', () => {
    expect(extractPacketPayloadHex(`09BF${'AA'.repeat(189)}4869`)).toBeNull();
  });

  it('rejects packets with no payload after path', () => {
    expect(extractPacketPayloadHex('0902AABB')).toBeNull();
  });
});

describe('contact routing helpers', () => {
  it('prefers routing override over learned route', () => {
    const effective = getEffectiveContactRoute(
      createContact({
        direct_path: 'AABB',
        direct_path_len: 1,
        direct_path_hash_mode: 0,
        route_override_path: 'AE92F13E',
        route_override_len: 2,
        route_override_hash_mode: 1,
      })
    );

    expect(effective.path).toBe('AE92F13E');
    expect(effective.pathLen).toBe(2);
    expect(effective.pathHashMode).toBe(1);
    expect(effective.forced).toBe(true);
  });

  it('formats route labels and override input', () => {
    expect(formatRouteLabel(-1)).toBe('flood');
    expect(formatRouteLabel(0)).toBe('direct');
    expect(formatRouteLabel(2, true)).toBe('2 hops');

    expect(
      formatRoutingOverrideInput(
        createContact({
          route_override_path: 'AE92F13E',
          route_override_len: 2,
          route_override_hash_mode: 1,
        })
      )
    ).toBe('ae92,f13e');
  });
});

describe('findContactsByPrefix', () => {
  const contacts: Contact[] = [
    createContact({
      public_key: '1AAAAA' + 'A'.repeat(52),
      name: 'Repeater1',
      type: CONTACT_TYPE_REPEATER,
    }),
    createContact({
      public_key: '1ABBBB' + 'B'.repeat(52),
      name: 'Repeater2',
      type: CONTACT_TYPE_REPEATER,
    }),
    createContact({
      public_key: '2BAAAA' + 'A'.repeat(52),
      name: 'Repeater3',
      type: CONTACT_TYPE_REPEATER,
    }),
    createContact({
      public_key: '1ACCCC' + 'C'.repeat(52),
      name: 'Client1',
      type: 1, // client
    }),
  ];

  it('finds matching repeaters', () => {
    const matches = findContactsByPrefix('1A', contacts, true);
    expect(matches).toHaveLength(2);
    expect(matches.map((c) => c.name)).toContain('Repeater1');
    expect(matches.map((c) => c.name)).toContain('Repeater2');
  });

  it('returns empty array for no match', () => {
    expect(findContactsByPrefix('XX', contacts, true)).toEqual([]);
  });

  it('is case insensitive', () => {
    expect(findContactsByPrefix('1a', contacts, true)).toHaveLength(2);
  });

  it('excludes non-repeaters when repeatersOnly is true', () => {
    const matches = findContactsByPrefix('1A', contacts, true);
    expect(matches.every((c) => c.type === CONTACT_TYPE_REPEATER)).toBe(true);
    expect(matches.map((c) => c.name)).not.toContain('Client1');
  });

  it('includes all types when repeatersOnly is false', () => {
    const matches = findContactsByPrefix('1A', contacts, false);
    expect(matches).toHaveLength(3);
    expect(matches.map((c) => c.name)).toContain('Client1');
  });
});

describe('calculateDistance', () => {
  it('returns null for null coordinates', () => {
    expect(calculateDistance(null, 0, 0, 0)).toBeNull();
    expect(calculateDistance(0, null, 0, 0)).toBeNull();
    expect(calculateDistance(0, 0, null, 0)).toBeNull();
    expect(calculateDistance(0, 0, 0, null)).toBeNull();
  });

  it('returns 0 for same point', () => {
    expect(calculateDistance(40.7128, -74.006, 40.7128, -74.006)).toBe(0);
  });

  it('calculates known distances approximately correctly', () => {
    // NYC (40.7128, -74.0060) to LA (34.0522, -118.2437) is approximately 3944 km
    const distance = calculateDistance(40.7128, -74.006, 34.0522, -118.2437);
    expect(distance).not.toBeNull();
    expect(distance).toBeGreaterThan(3900);
    expect(distance).toBeLessThan(4000);
  });

  it('handles short distances', () => {
    // About 1km apart in NYC
    const distance = calculateDistance(40.7128, -74.006, 40.7218, -74.006);
    expect(distance).not.toBeNull();
    expect(distance).toBeGreaterThan(0.9);
    expect(distance).toBeLessThan(1.1);
  });
});

describe('resolvePath', () => {
  const repeater1 = createContact({
    public_key: '1A' + 'A'.repeat(62),
    name: 'Repeater1',
    type: CONTACT_TYPE_REPEATER,
    lat: 40.75,
    lon: -74.0,
  });
  const repeater2 = createContact({
    public_key: '2B' + 'B'.repeat(62),
    name: 'Repeater2',
    type: CONTACT_TYPE_REPEATER,
    lat: 40.8,
    lon: -73.95,
  });
  const contacts = [repeater1, repeater2];

  const sender = {
    name: 'Sender',
    publicKeyOrPrefix: '5E' + 'E'.repeat(62),
    lat: 40.7,
    lon: -74.05,
  };

  const config = createConfig({
    public_key: 'FF' + 'F'.repeat(62),
    name: 'MyRadio',
    lat: 40.85,
    lon: -73.9,
  });

  it('resolves simple path with known repeaters', () => {
    const result = resolvePath('1A2B', sender, contacts, config);

    expect(result.sender.name).toBe('Sender');
    expect(result.sender.prefix).toBe('5E');
    expect(result.hops).toHaveLength(2);
    expect(result.hops[0].prefix).toBe('1A');
    expect(result.hops[0].matches).toHaveLength(1);
    expect(result.hops[0].matches[0].name).toBe('Repeater1');
    expect(result.hops[1].prefix).toBe('2B');
    expect(result.hops[1].matches).toHaveLength(1);
    expect(result.hops[1].matches[0].name).toBe('Repeater2');
    expect(result.receiver.name).toBe('MyRadio');
    expect(result.receiver.prefix).toBe('FF');
  });

  it('handles unknown repeaters (no matches)', () => {
    const result = resolvePath('XX', sender, contacts, config);

    expect(result.hops).toHaveLength(1);
    expect(result.hops[0].prefix).toBe('XX');
    expect(result.hops[0].matches).toHaveLength(0);
  });

  it('handles ambiguous repeaters (multiple matches)', () => {
    // Create two repeaters with same prefix
    const ambiguousContacts = [
      createContact({
        public_key: '1A' + 'A'.repeat(62),
        name: 'Repeater1A',
        type: CONTACT_TYPE_REPEATER,
        lat: 40.75,
        lon: -74.0,
      }),
      createContact({
        public_key: '1A' + 'B'.repeat(62),
        name: 'Repeater1B',
        type: CONTACT_TYPE_REPEATER,
        lat: 40.76,
        lon: -73.99,
      }),
    ];

    const result = resolvePath('1A', sender, ambiguousContacts, config);

    expect(result.hops).toHaveLength(1);
    expect(result.hops[0].matches).toHaveLength(2);
    // Should be sorted by distance from sender
    expect(result.hops[0].matches[0].name).toBe('Repeater1A');
  });

  it('calculates total distance when all locations known', () => {
    const result = resolvePath('1A2B', sender, contacts, config);

    expect(result.totalDistances).not.toBeNull();
    expect(result.totalDistances!.length).toBeGreaterThan(0);
    expect(result.totalDistances![0]).toBeGreaterThan(0);
  });

  it('returns null totalDistances when locations unknown', () => {
    const unknownRepeater = createContact({
      public_key: 'XX' + 'X'.repeat(62),
      name: 'Unknown',
      type: CONTACT_TYPE_REPEATER,
      lat: null,
      lon: null,
    });

    const result = resolvePath('XX', sender, [unknownRepeater], config);

    expect(result.totalDistances).toBeNull();
  });

  it('handles empty path', () => {
    const result = resolvePath('', sender, contacts, config);

    expect(result.hops).toHaveLength(0);
    expect(result.sender.name).toBe('Sender');
    expect(result.receiver.name).toBe('MyRadio');
  });

  it('uses explicit sender and receiver multibyte modes for endpoint prefixes', () => {
    const result = resolvePath(
      '',
      { ...sender, pathHashMode: 1 },
      contacts,
      createConfig({
        public_key: 'ABCDEF' + 'F'.repeat(58),
        path_hash_mode: 2,
      })
    );

    expect(result.sender.prefix).toBe('5EEE');
    expect(result.receiver.prefix).toBe('ABCDEF');
  });

  it('derives sender multibyte width from path metadata when sender mode is unknown', () => {
    const result = resolvePath(
      '1A2B3C4D',
      { ...sender, publicKeyOrPrefix: 'AABBCCDDEEFF' + '0'.repeat(52), pathHashMode: null },
      contacts,
      config,
      2
    );

    expect(result.sender.prefix).toBe('AABB');
  });

  it('handles null config gracefully', () => {
    const result = resolvePath('1A', sender, contacts, null);

    expect(result.receiver.name).toBe('Unknown');
    expect(result.receiver.prefix).toBe('??');
  });

  it('excludes receiver distance when receiver location is (0, 0)', () => {
    const configAtOrigin = createConfig({
      public_key: 'FF' + 'F'.repeat(62),
      name: 'MyRadio',
      lat: 0,
      lon: 0,
    });

    const result = resolvePath('1A', sender, contacts, configAtOrigin);

    // Total distance should NOT include the final leg to receiver
    // It should only be sender -> repeater1
    expect(result.totalDistances).not.toBeNull();
    const senderToRepeater = calculateDistance(
      sender.lat,
      sender.lon,
      repeater1.lat,
      repeater1.lon
    );
    expect(result.totalDistances![0]).toBeCloseTo(senderToRepeater!, 1);
  });

  it('skips distance after ambiguous hops', () => {
    // Create two repeaters with same prefix (ambiguous)
    const ambiguousContacts = [
      createContact({
        public_key: '1A' + 'A'.repeat(62),
        name: 'Repeater1A',
        type: CONTACT_TYPE_REPEATER,
        lat: 40.75,
        lon: -74.0,
      }),
      createContact({
        public_key: '1A' + 'B'.repeat(62),
        name: 'Repeater1B',
        type: CONTACT_TYPE_REPEATER,
        lat: 40.76,
        lon: -73.99,
      }),
      // Known repeater after the ambiguous one
      createContact({
        public_key: '2B' + 'B'.repeat(62),
        name: 'Repeater2',
        type: CONTACT_TYPE_REPEATER,
        lat: 40.8,
        lon: -73.95,
      }),
    ];

    const result = resolvePath('1A2B', sender, ambiguousContacts, config);

    // First hop is ambiguous, second hop is known
    expect(result.hops[0].matches).toHaveLength(2);
    expect(result.hops[1].matches).toHaveLength(1);

    // First hop is ambiguous, so no single distanceFromPrev
    // (UI shows individual distances for each match via getDistanceForContact)
    expect(result.hops[0].distanceFromPrev).toBeNull();

    // Second hop should also NOT have distanceFromPrev because previous hop was ambiguous
    expect(result.hops[1].distanceFromPrev).toBeNull();
  });

  it('calculates partial distance when sender has no location', () => {
    const senderNoLocation = {
      name: 'SenderNoLoc',
      publicKeyOrPrefix: '5E' + 'E'.repeat(62),
      lat: null,
      lon: null,
    };

    const result = resolvePath('1A2B', senderNoLocation, contacts, config);

    // First hop has no distance (can't calculate from unknown sender location)
    expect(result.hops[0].distanceFromPrev).toBeNull();

    // Second hop has distance (from first hop to second hop)
    expect(result.hops[1].distanceFromPrev).not.toBeNull();

    // Total distance should start from first known hop
    expect(result.totalDistances).not.toBeNull();
    expect(result.totalDistances![0]).toBeGreaterThan(0);
  });

  it('returns null totalDistances when all hops have no location', () => {
    const noLocationContacts = [
      createContact({
        public_key: '1A' + 'A'.repeat(62),
        name: 'NoLoc1',
        type: CONTACT_TYPE_REPEATER,
        lat: null,
        lon: null,
      }),
      createContact({
        public_key: '2B' + 'B'.repeat(62),
        name: 'NoLoc2',
        type: CONTACT_TYPE_REPEATER,
        lat: null,
        lon: null,
      }),
    ];

    const senderNoLocation = {
      name: 'SenderNoLoc',
      publicKeyOrPrefix: '5E' + 'E'.repeat(62),
      lat: null,
      lon: null,
    };

    const result = resolvePath('1A2B', senderNoLocation, noLocationContacts, config);

    expect(result.totalDistances).toBeNull();
  });

  it('treats contact at (0, 0) as having no location', () => {
    const contactAtOrigin = createContact({
      public_key: '1A' + 'A'.repeat(62),
      name: 'AtOrigin',
      type: CONTACT_TYPE_REPEATER,
      lat: 0,
      lon: 0,
    });

    const result = resolvePath('1A', sender, [contactAtOrigin], config);

    // Hop should match but have no distance (0, 0 treated as invalid)
    expect(result.hops).toHaveLength(1);
    expect(result.hops[0].matches).toHaveLength(1);
    expect(result.hops[0].distanceFromPrev).toBeNull();
  });

  it('treats sender at (0, 0) as having no location', () => {
    const senderAtOrigin = {
      name: 'SenderAtOrigin',
      publicKeyOrPrefix: '5E' + 'E'.repeat(62),
      lat: 0,
      lon: 0,
    };

    const result = resolvePath('1A2B', senderAtOrigin, contacts, config);

    // First hop should have no distance (sender at 0,0 treated as invalid)
    expect(result.hops[0].distanceFromPrev).toBeNull();

    // But second hop CAN have distance (from first hop)
    expect(result.hops[1].distanceFromPrev).not.toBeNull();
  });

  it('sets hasGaps to false when all hops are unambiguous with locations', () => {
    const result = resolvePath('1A2B', sender, contacts, config);

    expect(result.hasGaps).toBe(false);
  });

  it('sets hasGaps to true when path has unknown hops', () => {
    const result = resolvePath('XX', sender, contacts, config);

    expect(result.hasGaps).toBe(true);
  });

  it('sets hasGaps to true when path has ambiguous hops', () => {
    const ambiguousContacts = [
      createContact({
        public_key: '1A' + 'A'.repeat(62),
        name: 'Repeater1A',
        type: CONTACT_TYPE_REPEATER,
        lat: 40.75,
        lon: -74.0,
      }),
      createContact({
        public_key: '1A' + 'B'.repeat(62),
        name: 'Repeater1B',
        type: CONTACT_TYPE_REPEATER,
        lat: 40.76,
        lon: -73.99,
      }),
    ];

    const result = resolvePath('1A', sender, ambiguousContacts, config);

    expect(result.hasGaps).toBe(true);
  });

  it('sets hasGaps to true when sender has no location', () => {
    const senderNoLocation = {
      name: 'SenderNoLoc',
      publicKeyOrPrefix: '5E' + 'E'.repeat(62),
      lat: null,
      lon: null,
    };

    const result = resolvePath('1A', senderNoLocation, contacts, config);

    expect(result.hasGaps).toBe(true);
  });

  it('sets hasGaps to true when receiver has no valid location', () => {
    const configNoLocation = createConfig({
      public_key: 'FF' + 'F'.repeat(62),
      name: 'MyRadio',
      lat: 0,
      lon: 0,
    });

    const result = resolvePath('1A', sender, contacts, configNoLocation);

    expect(result.hasGaps).toBe(true);
  });

  it('includes receiver public key when config has one', () => {
    const result = resolvePath('1A', sender, contacts, config);

    expect(result.receiver.publicKey).toBe(config.public_key);
  });

  it('sets receiver public key to null when config has no public key', () => {
    const configNoKey = createConfig({
      public_key: undefined as unknown as string,
      name: 'NoKeyRadio',
    });

    const result = resolvePath('1A', sender, contacts, configNoKey);

    expect(result.receiver.publicKey).toBeNull();
  });

  it('resolves 2-byte hop path using hopCount parameter', () => {
    // Create repeaters whose public keys match 4-char prefixes
    const repeater2byte1 = createContact({
      public_key: '1A2B' + 'A'.repeat(60),
      name: 'Repeater2B1',
      type: CONTACT_TYPE_REPEATER,
      lat: 40.75,
      lon: -74.0,
    });
    const repeater2byte2 = createContact({
      public_key: '3C4D' + 'B'.repeat(60),
      name: 'Repeater2B2',
      type: CONTACT_TYPE_REPEATER,
      lat: 40.8,
      lon: -73.95,
    });
    const contacts2byte = [repeater2byte1, repeater2byte2];

    // Path "1A2B3C4D" with hopCount=2 → two 4-char hops: "1A2B", "3C4D"
    const result = resolvePath('1A2B3C4D', sender, contacts2byte, config, 2);

    expect(result.hops).toHaveLength(2);
    expect(result.hops[0].prefix).toBe('1A2B');
    expect(result.hops[0].matches).toHaveLength(1);
    expect(result.hops[0].matches[0].name).toBe('Repeater2B1');
    expect(result.hops[1].prefix).toBe('3C4D');
    expect(result.hops[1].matches).toHaveLength(1);
    expect(result.hops[1].matches[0].name).toBe('Repeater2B2');
  });

  it('resolves same path differently without hopCount (legacy fallback)', () => {
    // Without hopCount, "1A2B3C4D" → four 2-char hops: "1A", "2B", "3C", "4D"
    const result = resolvePath('1A2B3C4D', sender, contacts, config);

    expect(result.hops).toHaveLength(4);
    expect(result.hops[0].prefix).toBe('1A');
    expect(result.hops[1].prefix).toBe('2B');
    expect(result.hops[2].prefix).toBe('3C');
    expect(result.hops[3].prefix).toBe('4D');
  });
});

describe('isValidLocation', () => {
  it('rejects null and unset coordinates', () => {
    expect(isValidLocation(null, -122.3)).toBe(false);
    expect(isValidLocation(47.6, null)).toBe(false);
    expect(isValidLocation(0, 0)).toBe(false);
  });

  it('rejects out-of-range coordinates', () => {
    expect(isValidLocation(-593.497573, -1659.939204)).toBe(false);
    expect(isValidLocation(91, 0)).toBe(false);
    expect(isValidLocation(0, 181)).toBe(false);
  });

  it('accepts sane coordinates', () => {
    expect(isValidLocation(47.6062, -122.3321)).toBe(true);
  });
});

describe('formatDistance', () => {
  const formatInteger = (value: number) => value.toLocaleString();
  const formatOneDecimal = (value: number) =>
    value.toLocaleString(undefined, {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    });

  it('defaults to imperial formatting', () => {
    expect(formatDistance(0.01)).toBe(`${formatInteger(33)}ft`);
    expect(formatDistance(0.5)).toBe(`${formatOneDecimal(0.5 * 0.621371)}mi`);
    expect(formatDistance(1)).toBe(`${formatOneDecimal(0.621371)}mi`);
  });

  it('formats metric distances in meters and kilometers', () => {
    expect(formatDistance(0.5, 'metric')).toBe(`${formatInteger(500)}m`);
    expect(formatDistance(0.123, 'metric')).toBe(`${formatInteger(123)}m`);
    expect(formatDistance(0.9999, 'metric')).toBe(`${formatInteger(1000)}m`);
    expect(formatDistance(1, 'metric')).toBe(`${formatOneDecimal(1)}km`);
    expect(formatDistance(12.34, 'metric')).toBe(`${formatOneDecimal(12.34)}km`);
  });

  it('formats smoot distances using 1.7018 meters per smoot', () => {
    expect(formatDistance(0.0017018, 'smoots')).toBe(`${formatOneDecimal(1)} smoot`);
    expect(formatDistance(0.001, 'smoots')).toBe(`${formatOneDecimal(0.6)} smoots`);
    expect(formatDistance(1, 'smoots')).toBe(`${formatInteger(588)} smoots`);
  });

  it('applies locale separators to large values', () => {
    expect(formatDistance(1.234, 'metric')).toBe(`${formatOneDecimal(1.234)}km`);
    expect(formatDistance(1234, 'metric')).toBe(`${formatOneDecimal(1234)}km`);
    expect(formatDistance(2.1, 'smoots')).toContain(
      formatInteger(Math.round((2.1 * 1000) / 1.7018))
    );
  });
});

describe('formatHopCounts', () => {
  it('returns empty for null paths', () => {
    const result = formatHopCounts(null);
    expect(result.display).toBe('');
    expect(result.allDirect).toBe(true);
    expect(result.hasMultiple).toBe(false);
  });

  it('returns empty for empty paths array', () => {
    const result = formatHopCounts([]);
    expect(result.display).toBe('');
    expect(result.allDirect).toBe(true);
    expect(result.hasMultiple).toBe(false);
  });

  it('formats single direct path as "d"', () => {
    const result = formatHopCounts([{ path: '', received_at: 1700000000 }]);
    expect(result.display).toBe('d');
    expect(result.allDirect).toBe(true);
    expect(result.hasMultiple).toBe(false);
  });

  it('formats single multi-hop path with hop count', () => {
    const result = formatHopCounts([{ path: '1A2B', received_at: 1700000000 }]);
    expect(result.display).toBe('2');
    expect(result.allDirect).toBe(false);
    expect(result.hasMultiple).toBe(false);
  });

  it('formats multiple paths sorted by hop count', () => {
    const result = formatHopCounts([
      { path: '1A2B3C', received_at: 1700000000 }, // 3 hops
      { path: '', received_at: 1700000001 }, // direct
      { path: '1A', received_at: 1700000002 }, // 1 hop
      { path: '1A2B3C', received_at: 1700000003 }, // 3 hops
    ]);
    expect(result.display).toBe('d/1/3/3');
    expect(result.allDirect).toBe(false);
    expect(result.hasMultiple).toBe(true);
  });

  it('formats multiple direct paths', () => {
    const result = formatHopCounts([
      { path: '', received_at: 1700000000 },
      { path: '', received_at: 1700000001 },
    ]);
    expect(result.display).toBe('d/d');
    expect(result.allDirect).toBe(true);
    expect(result.hasMultiple).toBe(true);
  });

  it('handles mixed paths with multiple direct routes', () => {
    const result = formatHopCounts([
      { path: '1A', received_at: 1700000000 }, // 1 hop
      { path: '', received_at: 1700000001 }, // direct
      { path: '', received_at: 1700000002 }, // direct
    ]);
    expect(result.display).toBe('d/d/1');
    expect(result.allDirect).toBe(false);
    expect(result.hasMultiple).toBe(true);
  });

  it('uses path_len metadata for 2-byte hops instead of hex length', () => {
    // 8 hex chars with path_len=2 → 2 hops (not 4 as legacy would infer)
    const result = formatHopCounts([{ path: 'AABBCCDD', path_len: 2, received_at: 1700000000 }]);
    expect(result.display).toBe('2');
    expect(result.allDirect).toBe(false);
  });

  it('uses path_len metadata for 3-byte hops', () => {
    // 12 hex chars with path_len=2 → 2 hops (not 6 as legacy)
    const result = formatHopCounts([
      { path: 'AABBCCDDEEFF', path_len: 2, received_at: 1700000000 },
    ]);
    expect(result.display).toBe('2');
  });

  it('falls back to legacy count when path_len is null', () => {
    // 8 hex chars, no path_len → legacy: 8/2 = 4 hops
    const result = formatHopCounts([{ path: 'AABBCCDD', received_at: 1700000000 }]);
    expect(result.display).toBe('4');
  });

  it('mixes paths with and without path_len metadata', () => {
    const result = formatHopCounts([
      { path: 'AABBCCDD', path_len: 2, received_at: 1700000000 }, // 2 hops (2-byte)
      { path: '1A2B', received_at: 1700000001 }, // 2 hops (legacy)
      { path: '', received_at: 1700000002 }, // direct
    ]);
    expect(result.display).toBe('d/2/2');
    expect(result.allDirect).toBe(false);
    expect(result.hasMultiple).toBe(true);
  });
});
