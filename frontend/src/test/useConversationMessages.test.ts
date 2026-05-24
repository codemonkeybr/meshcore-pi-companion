/**
 * Tests for useConversationMessages hook utilities.
 *
 * These tests verify the message deduplication key generation.
 */

import { describe, it, expect } from 'vitest';
import { mergePendingAck } from '../hooks/useConversationMessages';
import { getMessageContentKey } from '../utils/messageIdentity';
import type { Message } from '../types';

function createMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 1,
    type: 'CHAN',
    conversation_key: 'channel123',
    text: 'Hello world',
    sender_timestamp: 1700000000,
    received_at: 1700000001,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: false,
    acked: 0,
    sender_name: null,
    ...overrides,
  };
}

describe('getMessageContentKey', () => {
  it('generates key from type, conversation_key, text, and sender_timestamp', () => {
    const msg = createMessage({
      type: 'CHAN',
      conversation_key: 'abc123',
      text: 'Hello',
      sender_timestamp: 1700000000,
    });

    const key = getMessageContentKey(msg);

    expect(key).toBe('CHAN-abc123-Hello-1700000000');
  });

  it('generates different keys for different message types', () => {
    const chanMsg = createMessage({ type: 'CHAN' });
    const privMsg = createMessage({ type: 'PRIV' });

    expect(getMessageContentKey(chanMsg)).not.toBe(getMessageContentKey(privMsg));
  });

  it('generates different keys for different conversation keys', () => {
    const msg1 = createMessage({ conversation_key: 'channel1' });
    const msg2 = createMessage({ conversation_key: 'channel2' });

    expect(getMessageContentKey(msg1)).not.toBe(getMessageContentKey(msg2));
  });

  it('generates different keys for different text', () => {
    const msg1 = createMessage({ text: 'Hello' });
    const msg2 = createMessage({ text: 'World' });

    expect(getMessageContentKey(msg1)).not.toBe(getMessageContentKey(msg2));
  });

  it('generates different keys for different timestamps', () => {
    const msg1 = createMessage({ sender_timestamp: 1700000000 });
    const msg2 = createMessage({ sender_timestamp: 1700000001 });

    expect(getMessageContentKey(msg1)).not.toBe(getMessageContentKey(msg2));
  });

  it('generates same key for messages with same content', () => {
    const msg1 = createMessage({
      id: 1,
      type: 'CHAN',
      conversation_key: 'abc',
      text: 'Test',
      sender_timestamp: 1700000000,
    });
    const msg2 = createMessage({
      id: 2, // Different ID
      type: 'CHAN',
      conversation_key: 'abc',
      text: 'Test',
      sender_timestamp: 1700000000,
    });

    expect(getMessageContentKey(msg1)).toBe(getMessageContentKey(msg2));
  });

  it('handles null sender_timestamp by falling back to received_at and id', () => {
    const msg = createMessage({ sender_timestamp: null });

    const key = getMessageContentKey(msg);

    // Falls back to `r${received_at}-${id}` when sender_timestamp is null
    expect(key).toBe('CHAN-channel123-Hello world-r1700000001-1');
  });

  it('handles empty text', () => {
    const msg = createMessage({ text: '' });

    const key = getMessageContentKey(msg);

    expect(key).toContain('--'); // Empty text between dashes
  });

  it('handles text with special characters', () => {
    const msg = createMessage({ text: 'Hello: World! @user #channel' });

    const key = getMessageContentKey(msg);

    expect(key).toContain('Hello: World! @user #channel');
  });

  it('null-timestamp messages with different received_at produce different keys', () => {
    const msg1 = createMessage({ sender_timestamp: null, received_at: 1700000001 });
    const msg2 = createMessage({ sender_timestamp: null, received_at: 1700000002 });

    expect(getMessageContentKey(msg1)).not.toBe(getMessageContentKey(msg2));
  });

  it('null-timestamp key does not collide with numeric timestamp key', () => {
    // A message with sender_timestamp=null and received_at=123
    // should not match a message with sender_timestamp that looks similar
    const nullTsMsg = createMessage({ sender_timestamp: null, received_at: 123 });
    const numericTsMsg = createMessage({ sender_timestamp: 123 });

    expect(getMessageContentKey(nullTsMsg)).not.toBe(getMessageContentKey(numericTsMsg));
  });

  it('same text and null timestamp but different conversations produce different keys', () => {
    const msg1 = createMessage({
      sender_timestamp: null,
      conversation_key: 'chan1',
      received_at: 1700000001,
    });
    const msg2 = createMessage({
      sender_timestamp: null,
      conversation_key: 'chan2',
      received_at: 1700000001,
    });

    expect(getMessageContentKey(msg1)).not.toBe(getMessageContentKey(msg2));
  });

  it('null-timestamp messages with same text and same received_at but different ids produce different keys', () => {
    // This is the key fix: two genuinely different messages arriving in the same second
    // with null sender_timestamp must NOT collide, even if text is identical
    const msg1 = createMessage({ id: 10, sender_timestamp: null, received_at: 1700000001 });
    const msg2 = createMessage({ id: 11, sender_timestamp: null, received_at: 1700000001 });

    expect(getMessageContentKey(msg1)).not.toBe(getMessageContentKey(msg2));
  });

  it('null-timestamp messages with same id produce same key (true duplicates dedup)', () => {
    // Same message arriving via WS + API fetch has the same id — should still dedup
    const msg1 = createMessage({ id: 42, sender_timestamp: null, received_at: 1700000001 });
    const msg2 = createMessage({ id: 42, sender_timestamp: null, received_at: 1700000001 });

    expect(getMessageContentKey(msg1)).toBe(getMessageContentKey(msg2));
  });

  it('PRIV messages with different sender_key produce different keys (room dedup)', () => {
    const msg1 = createMessage({
      type: 'PRIV',
      conversation_key: 'room_pubkey',
      text: 'ok',
      sender_timestamp: 1700000000,
      sender_key: 'alice_key',
    });
    const msg2 = createMessage({
      type: 'PRIV',
      conversation_key: 'room_pubkey',
      text: 'ok',
      sender_timestamp: 1700000000,
      sender_key: 'bob_key',
    });

    expect(getMessageContentKey(msg1)).not.toBe(getMessageContentKey(msg2));
  });

  it('PRIV messages with same sender_key still dedup (true room echo)', () => {
    const msg1 = createMessage({
      type: 'PRIV',
      conversation_key: 'room_pubkey',
      text: 'ok',
      sender_timestamp: 1700000000,
      sender_key: 'alice_key',
    });
    const msg2 = createMessage({
      type: 'PRIV',
      conversation_key: 'room_pubkey',
      text: 'ok',
      sender_timestamp: 1700000000,
      sender_key: 'alice_key',
    });

    expect(getMessageContentKey(msg1)).toBe(getMessageContentKey(msg2));
  });

  it('CHAN messages ignore sender_key (channel dedup unchanged)', () => {
    const msg1 = createMessage({
      type: 'CHAN',
      text: 'hello',
      sender_timestamp: 1700000000,
      sender_key: 'alice_key',
    });
    const msg2 = createMessage({
      type: 'CHAN',
      text: 'hello',
      sender_timestamp: 1700000000,
      sender_key: 'bob_key',
    });

    expect(getMessageContentKey(msg1)).toBe(getMessageContentKey(msg2));
  });

  it('PRIV messages with null sender_key still dedup normally', () => {
    const msg1 = createMessage({
      type: 'PRIV',
      conversation_key: 'contact_key',
      text: 'hi',
      sender_timestamp: 1700000000,
      sender_key: null,
    });
    const msg2 = createMessage({
      type: 'PRIV',
      conversation_key: 'contact_key',
      text: 'hi',
      sender_timestamp: 1700000000,
      sender_key: null,
    });

    expect(getMessageContentKey(msg1)).toBe(getMessageContentKey(msg2));
  });
});

describe('mergePendingAck', () => {
  const paths1 = [{ path: 'A1B2', received_at: 1700000000 }];
  const paths2 = [
    { path: 'A1B2', received_at: 1700000000 },
    { path: 'C3D4', received_at: 1700000001 },
  ];

  it('creates new entry when no existing state', () => {
    const result = mergePendingAck(undefined, 1, paths1);
    expect(result).toEqual({ ackCount: 1, paths: paths1 });
  });

  it('creates new entry without paths when paths not provided', () => {
    const result = mergePendingAck(undefined, 1);
    expect(result).toEqual({ ackCount: 1 });
    expect('paths' in result).toBe(false);
  });

  it('higher ack count replaces existing', () => {
    const existing = { ackCount: 1, paths: paths1 };
    const result = mergePendingAck(existing, 3, paths2);
    expect(result).toEqual({ ackCount: 3, paths: paths2 });
  });

  it('higher ack count preserves existing paths when new paths undefined', () => {
    const existing = { ackCount: 1, paths: paths1 };
    const result = mergePendingAck(existing, 3);
    expect(result).toEqual({ ackCount: 3, paths: paths1 });
  });

  it('higher ack count drops existing paths when new paths explicitly provided', () => {
    const existing = { ackCount: 1, paths: paths2 };
    const result = mergePendingAck(existing, 3, paths1);
    expect(result).toEqual({ ackCount: 3, paths: paths1 });
  });

  it('lower ack count is ignored entirely', () => {
    const existing = { ackCount: 5, paths: paths2 };
    const result = mergePendingAck(existing, 2, paths1);
    expect(result).toBe(existing);
  });

  it('same ack count with no new paths returns existing', () => {
    const existing = { ackCount: 3, paths: paths1 };
    const result = mergePendingAck(existing, 3);
    expect(result).toBe(existing);
  });

  it('same ack count with more paths replaces', () => {
    const existing = { ackCount: 3, paths: paths1 };
    const result = mergePendingAck(existing, 3, paths2);
    expect(result).toEqual({ ackCount: 3, paths: paths2 });
  });

  it('same ack count with fewer paths keeps existing', () => {
    const existing = { ackCount: 3, paths: paths2 };
    const result = mergePendingAck(existing, 3, paths1);
    expect(result).toBe(existing);
  });

  it('same ack count with equal-length paths replaces (uses >=)', () => {
    const existing = { ackCount: 3, paths: paths1 };
    const newPaths = [{ path: 'X1Y2', received_at: 1700000005 }];
    const result = mergePendingAck(existing, 3, newPaths);
    expect(result).toEqual({ ackCount: 3, paths: newPaths });
  });

  it('same ack count with paths when existing has no paths', () => {
    const existing = { ackCount: 2 };
    const result = mergePendingAck(existing, 2, paths1);
    // existing.paths is undefined → length -1, paths1.length (1) >= -1 → replaces
    expect(result).toEqual({ ackCount: 2, paths: paths1 });
  });
});
