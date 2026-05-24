/**
 * Tests for message parsing utilities.
 *
 * These tests verify the sender extraction logic used to parse
 * channel messages in "sender: message" format.
 */

import { describe, it, expect } from 'vitest';
import {
  findLinkedChannelReferences,
  formatTime,
  HASHTAG_CHANNEL_NAME_PATTERN,
  parseSenderFromText,
} from '../utils/messageParser';

describe('parseSenderFromText', () => {
  it('extracts sender and content from "sender: message" format', () => {
    const result = parseSenderFromText('Alice: Hello everyone!');

    expect(result.sender).toBe('Alice');
    expect(result.content).toBe('Hello everyone!');
  });

  it('handles sender names with spaces', () => {
    const result = parseSenderFromText('Bob Smith: How are you?');

    expect(result.sender).toBe('Bob Smith');
    expect(result.content).toBe('How are you?');
  });

  it('returns null sender for plain messages without colon-space', () => {
    const result = parseSenderFromText('Just a plain message');

    expect(result.sender).toBeNull();
    expect(result.content).toBe('Just a plain message');
  });

  it('returns null sender when colon has no space after', () => {
    const result = parseSenderFromText('Note:this is not a sender');

    expect(result.sender).toBeNull();
    expect(result.content).toBe('Note:this is not a sender');
  });

  it('rejects sender containing colon', () => {
    const result = parseSenderFromText('12:30: Time announcement');

    expect(result.sender).toBeNull();
    expect(result.content).toBe('12:30: Time announcement');
  });

  it('rejects sender names longer than 50 characters', () => {
    const longName = 'A'.repeat(60);
    const result = parseSenderFromText(`${longName}: message`);

    expect(result.sender).toBeNull();
  });

  it('handles empty string', () => {
    const result = parseSenderFromText('');

    expect(result.sender).toBeNull();
    expect(result.content).toBe('');
  });

  it('handles message with multiple colons', () => {
    const result = parseSenderFromText('User: Check this URL: https://example.com');

    expect(result.sender).toBe('User');
    expect(result.content).toBe('Check this URL: https://example.com');
  });

  it('handles colon at start of message', () => {
    const result = parseSenderFromText(': no sender here');

    expect(result.sender).toBeNull();
    expect(result.content).toBe(': no sender here');
  });
});

describe('formatTime', () => {
  it('formats today timestamp as time only', () => {
    // Use current time to ensure it's "today"
    const now = Math.floor(Date.now() / 1000);

    const result = formatTime(now);

    // Should be just time (HH:MM format)
    expect(result).toMatch(/^\d{1,2}:\d{2}( [AP]M)?$/);
  });

  it('formats older timestamp with date and time', () => {
    // Use a timestamp from 2023 (definitely not today)
    const timestamp = 1700000000; // 2023-11-14

    const result = formatTime(timestamp);

    // Should contain month, day, and time
    expect(result).toMatch(/\w+ \d{1,2}/); // e.g., "Nov 14"
    expect(result).toMatch(/\d{1,2}:\d{2}/); // time portion
  });
});

describe('linked channel references', () => {
  it('accepts lowercase alphanumeric names with single dashes', () => {
    expect(HASHTAG_CHANNEL_NAME_PATTERN.test('ops')).toBe(true);
    expect(HASHTAG_CHANNEL_NAME_PATTERN.test('ops-1')).toBe(true);
    expect(HASHTAG_CHANNEL_NAME_PATTERN.test('1-2-3')).toBe(true);
  });

  it('rejects uppercase, leading or trailing dashes, and repeated dashes', () => {
    expect(HASHTAG_CHANNEL_NAME_PATTERN.test('Ops')).toBe(false);
    expect(HASHTAG_CHANNEL_NAME_PATTERN.test('-ops')).toBe(false);
    expect(HASHTAG_CHANNEL_NAME_PATTERN.test('ops-')).toBe(false);
    expect(HASHTAG_CHANNEL_NAME_PATTERN.test('ops--room')).toBe(false);
  });

  it('finds standalone linked channel references in message text', () => {
    expect(findLinkedChannelReferences('Join #mesh-room then say hi in #ops2')).toEqual([
      { label: '#mesh-room', start: 5, end: 15 },
      { label: '#ops2', start: 31, end: 36 },
    ]);
  });

  it('finds linked channel references terminated by clause punctuation', () => {
    expect(
      findLinkedChannelReferences('Join #mesh-room, then #ops2; finally #alpha-room.')
    ).toEqual([
      { label: '#mesh-room', start: 5, end: 15 },
      { label: '#ops2', start: 22, end: 27 },
      { label: '#alpha-room', start: 37, end: 48 },
    ]);
  });

  it('ignores invalid or embedded channel-like text', () => {
    const references = findLinkedChannelReferences(
      'skip #Bad #bad--name abc#ops #ops- #opsRoom #ops_room #good-room,'
    );

    expect(references.map((reference) => reference.label)).toEqual(['#good-room']);
  });
});
