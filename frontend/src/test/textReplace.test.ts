import { describe, it, expect, beforeEach } from 'vitest';
import {
  getTextReplaceEnabled,
  setTextReplaceEnabled,
  getTextReplaceMapJson,
  setTextReplaceMapJson,
  applyTextReplacements,
  DEFAULT_MAP_JSON,
} from '../utils/textReplace';

beforeEach(() => {
  localStorage.clear();
});

describe('enabled toggle', () => {
  it('defaults to disabled', () => {
    expect(getTextReplaceEnabled()).toBe(false);
  });

  it('persists enabled state', () => {
    setTextReplaceEnabled(true);
    expect(getTextReplaceEnabled()).toBe(true);
    setTextReplaceEnabled(false);
    expect(getTextReplaceEnabled()).toBe(false);
  });
});

describe('map JSON persistence', () => {
  it('returns default map when nothing stored', () => {
    expect(getTextReplaceMapJson()).toBe(DEFAULT_MAP_JSON);
  });

  it('persists valid JSON and returns null', () => {
    const json = '{"a":"b"}';
    expect(setTextReplaceMapJson(json)).toBeNull();
    expect(getTextReplaceMapJson()).toBe(json);
  });

  it('rejects invalid JSON with error string', () => {
    const err = setTextReplaceMapJson('not json');
    expect(err).toBeTypeOf('string');
    // localStorage unchanged — still returns default
    expect(getTextReplaceMapJson()).toBe(DEFAULT_MAP_JSON);
  });

  it('rejects arrays', () => {
    expect(setTextReplaceMapJson('["a","b"]')).toBeTypeOf('string');
  });

  it('rejects non-string values', () => {
    expect(setTextReplaceMapJson('{"a":123}')).toBeTypeOf('string');
  });

  it('rejects null', () => {
    expect(setTextReplaceMapJson('null')).toBeTypeOf('string');
  });

  it('accepts empty object', () => {
    expect(setTextReplaceMapJson('{}')).toBeNull();
  });
});

describe('re-expansion validation', () => {
  it('rejects when a key appears in its own replacement', () => {
    const err = setTextReplaceMapJson(JSON.stringify({ a: 'aa' }));
    expect(err).toBeTypeOf('string');
    expect(err).toContain('"a"');
    expect(err).toContain('"aa"');
  });

  it('rejects when a key appears in another replacement', () => {
    const err = setTextReplaceMapJson(JSON.stringify({ a: 'X', b: 'ab' }));
    expect(err).toBeTypeOf('string');
    expect(err).toContain('"a"');
    expect(err).toContain('"ab"');
  });

  it('allows replacements that do not contain any key', () => {
    expect(setTextReplaceMapJson(JSON.stringify({ a: 'X', b: 'Y' }))).toBeNull();
  });

  it('allows the default Cyrillic map', () => {
    expect(setTextReplaceMapJson(DEFAULT_MAP_JSON)).toBeNull();
  });

  it('does not check empty keys for re-expansion', () => {
    // Empty key is silently skipped by buildReplacements, so it should not
    // cause a re-expansion rejection for other entries.
    expect(setTextReplaceMapJson(JSON.stringify({ '': 'x', b: 'Y' }))).toBeNull();
  });
});

describe('applyTextReplacements', () => {
  const simpleMap = JSON.stringify({ a: 'X', b: 'Y' });

  it('returns null when no replacements match', () => {
    expect(applyTextReplacements('hello', 5, simpleMap)).toBeNull();
  });

  it('returns null for empty map', () => {
    expect(applyTextReplacements('abc', 3, '{}')).toBeNull();
  });

  it('returns null for invalid JSON', () => {
    expect(applyTextReplacements('abc', 3, 'broken')).toBeNull();
  });

  it('replaces a single character with cursor at end', () => {
    const result = applyTextReplacements('a', 1, simpleMap);
    expect(result).toEqual({ text: 'X', cursor: 1 });
  });

  it('replaces multiple characters in one pass', () => {
    const result = applyTextReplacements('ab', 2, simpleMap);
    expect(result).toEqual({ text: 'XY', cursor: 2 });
  });

  it('adjusts cursor when replacement is longer than needle', () => {
    const map = JSON.stringify({ ':)': 'smiley' });
    // "hello :)" cursor at end (8)
    const result = applyTextReplacements('hello :)', 8, map);
    expect(result).toEqual({ text: 'hello smiley', cursor: 12 });
  });

  it('adjusts cursor when replacement is shorter than needle', () => {
    const map = JSON.stringify({ abc: 'Z' });
    // "abcdef" cursor at end (6)
    const result = applyTextReplacements('abcdef', 6, map);
    expect(result).toEqual({ text: 'Zdef', cursor: 4 });
  });

  it('preserves cursor position when replacement is before cursor', () => {
    const map = JSON.stringify({ a: 'XX' });
    // "a_b" cursor at 2 (on 'b'), 'a' replaced with 'XX'
    const result = applyTextReplacements('a_b', 2, map);
    expect(result).toEqual({ text: 'XX_b', cursor: 3 });
  });

  it('does not adjust cursor for replacements after cursor', () => {
    const map = JSON.stringify({ b: 'YY' });
    // "ab" cursor at 1 (after 'a'), 'b' is after cursor
    const result = applyTextReplacements('ab', 1, map);
    expect(result).toEqual({ text: 'aYY', cursor: 1 });
  });

  it('places cursor after replacement when cursor is inside a multi-char match', () => {
    const map = JSON.stringify({ abc: 'Z' });
    // "abc" cursor at 2 (inside the match)
    const result = applyTextReplacements('abc', 2, map);
    expect(result).toEqual({ text: 'Z', cursor: 1 });
  });

  it('handles multiple replacements with cursor tracking', () => {
    const map = JSON.stringify({ ':)': 'S' });
    // ":):)" cursor at end (4) — two replacements, each shrinks by 1
    const result = applyTextReplacements(':):)', 4, map);
    expect(result).toEqual({ text: 'SS', cursor: 2 });
  });

  it('cursor between two replacements stays correct', () => {
    const map = JSON.stringify({ ':)': 'S' });
    // ":):)" cursor at 2 (between the two smileys)
    const result = applyTextReplacements(':):)', 2, map);
    expect(result).toEqual({ text: 'SS', cursor: 1 });
  });

  it('uses longest match first', () => {
    const map = JSON.stringify({ ab: 'LONG', a: 'X' });
    const result = applyTextReplacements('ab', 2, map);
    expect(result).toEqual({ text: 'LONG', cursor: 4 });
  });

  it('ignores empty-string keys (no infinite loop)', () => {
    const map = JSON.stringify({ '': 'oops', a: 'X' });
    const result = applyTextReplacements('abc', 3, map);
    expect(result).toEqual({ text: 'Xbc', cursor: 3 });
  });

  it('works with the default Cyrillic map', () => {
    // "Привет" — П has no mapping, р→p, и has no mapping, в has no mapping, е→e, т has no mapping
    const result = applyTextReplacements('Привет', 6, DEFAULT_MAP_JSON);
    expect(result).not.toBeNull();
    expect(result!.text).toBe('Пpивeт');
    expect(result!.cursor).toBe(6);
  });

  it('handles paste with many replacements', () => {
    const map = JSON.stringify({ А: 'A', В: 'B', С: 'C' });
    const result = applyTextReplacements('АВС', 3, map);
    expect(result).toEqual({ text: 'ABC', cursor: 3 });
  });
});
