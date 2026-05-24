import { describe, expect, it } from 'vitest';

import { parseWsEvent } from '../wsEvents';

describe('wsEvents', () => {
  it('parses contact_deleted events', () => {
    const event = parseWsEvent(
      JSON.stringify({ type: 'contact_deleted', data: { public_key: 'aa' } })
    );

    expect(event).toEqual({
      type: 'contact_deleted',
      data: { public_key: 'aa' },
    });
  });

  it('parses contact_resolved events', () => {
    const event = parseWsEvent(
      JSON.stringify({
        type: 'contact_resolved',
        data: {
          previous_public_key: 'abc123def456',
          contact: {
            public_key: 'aa'.repeat(32),
            name: null,
            type: 0,
            flags: 0,
            direct_path: null,
            direct_path_len: -1,
            direct_path_hash_mode: -1,
            last_advert: null,
            lat: null,
            lon: null,
            last_seen: null,
            on_radio: false,
            last_contacted: null,
            last_read_at: null,
            first_seen: null,
          },
        },
      })
    );

    expect(event).toEqual({
      type: 'contact_resolved',
      data: {
        previous_public_key: 'abc123def456',
        contact: {
          public_key: 'aa'.repeat(32),
          name: null,
          type: 0,
          flags: 0,
          direct_path: null,
          direct_path_len: -1,
          direct_path_hash_mode: -1,
          last_advert: null,
          lat: null,
          lon: null,
          last_seen: null,
          on_radio: false,
          last_contacted: null,
          last_read_at: null,
          first_seen: null,
        },
      },
    });
  });

  it('parses channel_deleted events', () => {
    const event = parseWsEvent(JSON.stringify({ type: 'channel_deleted', data: { key: 'bb' } }));

    expect(event).toEqual({
      type: 'channel_deleted',
      data: { key: 'bb' },
    });
  });

  it('returns unknown events with rawType preserved', () => {
    const event = parseWsEvent(JSON.stringify({ type: 'mystery', data: { ok: true } }));

    expect(event).toEqual({
      type: 'unknown',
      rawType: 'mystery',
      data: { ok: true },
    });
  });

  it('rejects invalid envelopes', () => {
    expect(() => parseWsEvent(JSON.stringify({ data: {} }))).toThrow(
      'Invalid WebSocket event envelope'
    );
  });
});
