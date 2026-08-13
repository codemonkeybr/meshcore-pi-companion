/**
 * Tests for MeshCore Open rich-chat payload parsing (GIFs and reactions).
 *
 * Formats are ported from meshcore-open; see meshcoreOpenPayloads.ts.
 */

import { describe, it, expect } from 'vitest';
import {
  REACTION_EMOJIS,
  giphyUrlForId,
  parseGif,
  parseReaction,
  splitReplyMention,
} from '../utils/meshcoreOpenPayloads';

describe('parseGif', () => {
  it('parses a g:<id> payload', () => {
    expect(parseGif('g:abc123')).toBe('abc123');
  });

  it('accepts ids with underscores and dashes', () => {
    expect(parseGif('g:aB3_-xY')).toBe('aB3_-xY');
  });

  it('trims surrounding whitespace', () => {
    expect(parseGif('  g:abc123  ')).toBe('abc123');
  });

  it('returns null for non-gif text', () => {
    expect(parseGif('hello world')).toBeNull();
    expect(parseGif('g:')).toBeNull();
    expect(parseGif('g:abc 123')).toBeNull();
    expect(parseGif('prefix g:abc')).toBeNull();
    expect(parseGif('g:abc!')).toBeNull();
  });

  it('builds the Giphy media URL', () => {
    expect(giphyUrlForId('abc123')).toBe('https://media.giphy.com/media/abc123/giphy.gif');
  });
});

describe('parseReaction', () => {
  it('decodes the first emoji (index 00)', () => {
    const result = parseReaction('r:1a2b:00');
    expect(result).toEqual({ emoji: REACTION_EMOJIS[0], targetHash: '1a2b' });
    expect(result?.emoji).toBe('👍');
  });

  it('decodes a non-zero index', () => {
    // index 0x06 -> first smiley (after the 6 quick emojis)
    const result = parseReaction('r:ffff:06');
    expect(result?.emoji).toBe(REACTION_EMOJIS[6]);
    expect(result?.targetHash).toBe('ffff');
  });

  it('trims surrounding whitespace', () => {
    expect(parseReaction('  r:1a2b:00  ')?.emoji).toBe('👍');
  });

  it('returns null for an out-of-range index', () => {
    // 0xff (255) is beyond the emoji list length
    expect(parseReaction('r:1a2b:ff')).toBeNull();
  });

  it('returns null for malformed reactions', () => {
    expect(parseReaction('r:1a2b')).toBeNull();
    expect(parseReaction('r:1a2:00')).toBeNull(); // hash too short
    expect(parseReaction('r:1A2B:00')).toBeNull(); // uppercase hex not accepted
    expect(parseReaction('r:1a2b:0')).toBeNull(); // index too short
    expect(parseReaction('hello')).toBeNull();
  });

  it('exposes a stable, deduplication-free emoji index range', () => {
    // 6 quick + 64 smileys + 33 gestures + 32 hearts + 49 objects
    expect(REACTION_EMOJIS.length).toBe(184);
    // every defined index decodes to a string
    for (let i = 0; i < REACTION_EMOJIS.length; i++) {
      const hex = i.toString(16).padStart(2, '0');
      expect(parseReaction(`r:0000:${hex}`)?.emoji).toBe(REACTION_EMOJIS[i]);
    }
  });
});

describe('splitReplyMention', () => {
  it('splits a reply-prefixed gif into mention + body (issue #291)', () => {
    // meshcore-open sends GIF replies as "@[senderName] g:<id>".
    expect(splitReplyMention('@[Alice] g:abc123')).toEqual({
      mention: '@[Alice]',
      body: 'g:abc123',
    });
  });

  it('the split body parses as a gif while the whole string does not', () => {
    const whole = '@[Alice] g:abc123';
    expect(parseGif(whole)).toBeNull(); // anchored regex rejects the prefix
    const split = splitReplyMention(whole);
    expect(split && parseGif(split.body)).toBe('abc123');
  });

  it('splits a reply-prefixed reaction', () => {
    expect(splitReplyMention('@[Bob] r:1a2b:00')).toEqual({
      mention: '@[Bob]',
      body: 'r:1a2b:00',
    });
  });

  it('trims surrounding whitespace and preserves names with spaces', () => {
    expect(splitReplyMention('  @[Node One]   g:xy  ')).toEqual({
      mention: '@[Node One]',
      body: 'g:xy',
    });
  });

  it('returns null without a leading reply mention', () => {
    expect(splitReplyMention('g:abc123')).toBeNull();
    expect(splitReplyMention('hello world')).toBeNull();
    expect(splitReplyMention('@[Alice]')).toBeNull(); // mention only, no body
    expect(splitReplyMention('text @[Alice] g:abc')).toBeNull(); // not a leading mention
  });
});
