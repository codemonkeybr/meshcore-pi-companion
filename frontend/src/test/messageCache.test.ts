/**
 * Tests for the LRU message cache.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import * as messageCache from '../messageCache';
import { MAX_CACHED_CONVERSATIONS, MAX_MESSAGES_PER_ENTRY } from '../messageCache';
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
    outgoing: false,
    acked: 0,
    ...overrides,
  };
}

function createEntry(messages: Message[] = [], hasOlderMessages = false): messageCache.CacheEntry {
  const seenContent = new Set<string>();
  for (const msg of messages) {
    seenContent.add(`${msg.type}-${msg.conversation_key}-${msg.text}-${msg.sender_timestamp}`);
  }
  return { messages, seenContent, hasOlderMessages };
}

describe('messageCache', () => {
  beforeEach(() => {
    messageCache.clear();
  });

  describe('get/set', () => {
    it('returns undefined for missing entries', () => {
      expect(messageCache.get('nonexistent')).toBeUndefined();
    });

    it('stores and retrieves entries', () => {
      const msg = createMessage();
      const entry = createEntry([msg], true);
      messageCache.set('conv1', entry);

      const result = messageCache.get('conv1');
      expect(result).toBeDefined();
      expect(result!.messages).toHaveLength(1);
      expect(result!.messages[0].text).toBe('Hello world');
      expect(result!.hasOlderMessages).toBe(true);
    });

    it('trims messages to MAX_MESSAGES_PER_ENTRY on set', () => {
      const messages = Array.from({ length: MAX_MESSAGES_PER_ENTRY + 50 }, (_, i) =>
        createMessage({ id: i, received_at: 1700000000 + i })
      );
      messageCache.set('conv1', createEntry(messages));

      const entry = messageCache.get('conv1');
      expect(entry!.messages).toHaveLength(MAX_MESSAGES_PER_ENTRY);
    });

    it('keeps the most recent messages when trimming', () => {
      const messages = Array.from({ length: MAX_MESSAGES_PER_ENTRY + 10 }, (_, i) =>
        createMessage({ id: i, received_at: 1700000000 + i })
      );
      messageCache.set('conv1', createEntry(messages));

      const entry = messageCache.get('conv1');
      // Most recent message (highest received_at) should be present
      const maxReceivedAt = MAX_MESSAGES_PER_ENTRY + 10 - 1;
      expect(entry!.messages.some((m) => m.received_at === 1700000000 + maxReceivedAt)).toBe(true);
      // Oldest messages should be trimmed
      expect(entry!.messages.some((m) => m.received_at === 1700000000)).toBe(false);
    });

    it('sets hasOlderMessages to true when trimming', () => {
      const messages = Array.from({ length: MAX_MESSAGES_PER_ENTRY + 1 }, (_, i) =>
        createMessage({ id: i, received_at: 1700000000 + i })
      );
      messageCache.set('conv1', createEntry(messages, false));

      const entry = messageCache.get('conv1');
      expect(entry!.hasOlderMessages).toBe(true);
    });

    it('overwrites existing entries', () => {
      const entry1 = createEntry([createMessage({ text: 'first' })]);
      const entry2 = createEntry([createMessage({ text: 'second' })]);

      messageCache.set('conv1', entry1);
      messageCache.set('conv1', entry2);

      const result = messageCache.get('conv1');
      expect(result!.messages[0].text).toBe('second');
      expect(messageCache.size()).toBe(1);
    });
  });

  describe('LRU eviction', () => {
    it('evicts least-recently-used entry when over capacity', () => {
      // Fill cache to capacity + 1
      for (let i = 0; i <= MAX_CACHED_CONVERSATIONS; i++) {
        messageCache.set(`conv${i}`, createEntry([createMessage({ id: i })]));
      }

      // conv0 (LRU) should be evicted
      expect(messageCache.get('conv0')).toBeUndefined();
      // Remaining entries should still exist
      for (let i = 1; i <= MAX_CACHED_CONVERSATIONS; i++) {
        expect(messageCache.get(`conv${i}`)).toBeDefined();
      }
    });

    it('promotes accessed entries to MRU', () => {
      // Fill cache to capacity
      for (let i = 0; i < MAX_CACHED_CONVERSATIONS; i++) {
        messageCache.set(`conv${i}`, createEntry([createMessage({ id: i })]));
      }

      // Access conv0, promoting it to MRU
      messageCache.get('conv0');

      // Add one more - conv1 should now be LRU and get evicted
      messageCache.set('conv_new', createEntry());

      expect(messageCache.get('conv0')).toBeDefined(); // Was promoted
      expect(messageCache.get('conv1')).toBeUndefined(); // Was LRU, evicted
      expect(messageCache.get('conv_new')).toBeDefined();
    });

    it('promotes set entries to MRU', () => {
      for (let i = 0; i < MAX_CACHED_CONVERSATIONS; i++) {
        messageCache.set(`conv${i}`, createEntry([createMessage({ id: i })]));
      }

      // Re-set conv0 (promotes to MRU)
      messageCache.set('conv0', createEntry([createMessage({ id: 100 })]));

      // Add one more - conv1 should be LRU and get evicted
      messageCache.set('conv_new', createEntry());

      expect(messageCache.get('conv0')).toBeDefined();
      expect(messageCache.get('conv1')).toBeUndefined();
    });
  });

  describe('addMessage', () => {
    it('adds message to existing cached conversation and returns true', () => {
      messageCache.set('conv1', createEntry([]));

      const msg = createMessage({ id: 10, text: 'New message' });
      const result = messageCache.addMessage(
        'conv1',
        msg,
        'CHAN-channel123-New message-1700000000'
      );

      expect(result).toBe(true);
      const entry = messageCache.get('conv1');
      expect(entry!.messages).toHaveLength(1);
      expect(entry!.messages[0].text).toBe('New message');
    });

    it('deduplicates by content key and returns false', () => {
      messageCache.set('conv1', createEntry([]));

      const msg1 = createMessage({ id: 10, text: 'Hello' });
      const contentKey = 'CHAN-channel123-Hello-1700000000';
      expect(messageCache.addMessage('conv1', msg1, contentKey)).toBe(true);

      // Same content key, different message id
      const msg2 = createMessage({ id: 11, text: 'Hello' });
      expect(messageCache.addMessage('conv1', msg2, contentKey)).toBe(false);

      const entry = messageCache.get('conv1');
      expect(entry!.messages).toHaveLength(1);
    });

    it('deduplicates by message id and returns false', () => {
      messageCache.set('conv1', createEntry([createMessage({ id: 10, text: 'Original' })]));

      // Same id, different content key
      const msg = createMessage({ id: 10, text: 'Different' });
      expect(messageCache.addMessage('conv1', msg, 'CHAN-channel123-Different-1700000000')).toBe(
        false
      );

      const entry = messageCache.get('conv1');
      expect(entry!.messages).toHaveLength(1);
      expect(entry!.messages[0].text).toBe('Original');
    });

    it('trims to MAX_MESSAGES_PER_ENTRY when adding to a full entry', () => {
      const messages = Array.from({ length: MAX_MESSAGES_PER_ENTRY }, (_, i) =>
        createMessage({ id: i, received_at: 1700000000 + i })
      );
      messageCache.set('conv1', createEntry(messages));

      // Add one more (newest)
      const newMsg = createMessage({
        id: MAX_MESSAGES_PER_ENTRY,
        text: 'newest',
        received_at: 1700000000 + MAX_MESSAGES_PER_ENTRY,
      });
      const result = messageCache.addMessage(
        'conv1',
        newMsg,
        `CHAN-channel123-newest-${newMsg.sender_timestamp}`
      );

      expect(result).toBe(true);
      const entry = messageCache.get('conv1');
      expect(entry!.messages).toHaveLength(MAX_MESSAGES_PER_ENTRY);
      // Newest message should be kept
      expect(entry!.messages.some((m) => m.id === MAX_MESSAGES_PER_ENTRY)).toBe(true);
      // Oldest message (id=0) should be trimmed
      expect(entry!.messages.some((m) => m.id === 0)).toBe(false);
    });

    it('auto-creates a minimal entry for never-visited conversations and returns true', () => {
      const msg = createMessage({ id: 10, text: 'First contact' });
      const result = messageCache.addMessage(
        'new_conv',
        msg,
        'CHAN-channel123-First contact-1700000000'
      );

      expect(result).toBe(true);
      expect(messageCache.size()).toBe(1);
      const entry = messageCache.get('new_conv');
      expect(entry).toBeDefined();
      expect(entry!.messages).toHaveLength(1);
      expect(entry!.messages[0].text).toBe('First contact');
      expect(entry!.hasOlderMessages).toBe(true);
      expect(entry!.seenContent.has('CHAN-channel123-First contact-1700000000')).toBe(true);
    });

    it('returns false for duplicate delivery to auto-created entry', () => {
      const msg = createMessage({ id: 10, text: 'Echo' });
      const contentKey = 'CHAN-channel123-Echo-1700000000';

      expect(messageCache.addMessage('new_conv', msg, contentKey)).toBe(true);
      // Duplicate via mesh echo
      expect(messageCache.addMessage('new_conv', msg, contentKey)).toBe(false);

      const entry = messageCache.get('new_conv');
      expect(entry!.messages).toHaveLength(1);
    });
  });

  describe('updateAck', () => {
    it('updates ack count for a message in cache', () => {
      const msg = createMessage({ id: 42, acked: 0 });
      messageCache.set('conv1', createEntry([msg]));

      messageCache.updateAck(42, 3);

      const entry = messageCache.get('conv1');
      expect(entry!.messages[0].acked).toBe(3);
    });

    it('updates paths when provided', () => {
      const msg = createMessage({ id: 42, acked: 0, paths: null });
      messageCache.set('conv1', createEntry([msg]));

      const newPaths = [{ path: '1A2B', received_at: 1700000000 }];
      messageCache.updateAck(42, 1, newPaths);

      const entry = messageCache.get('conv1');
      expect(entry!.messages[0].acked).toBe(1);
      expect(entry!.messages[0].paths).toEqual(newPaths);
    });

    it('does not modify paths when not provided', () => {
      const existingPaths = [{ path: '1A2B', received_at: 1700000000 }];
      const msg = createMessage({ id: 42, acked: 1, paths: existingPaths });
      messageCache.set('conv1', createEntry([msg]));

      messageCache.updateAck(42, 2);

      const entry = messageCache.get('conv1');
      expect(entry!.messages[0].acked).toBe(2);
      expect(entry!.messages[0].paths).toEqual(existingPaths);
    });

    it('scans across multiple cached conversations', () => {
      const msg1 = createMessage({ id: 10, conversation_key: 'conv1', acked: 0 });
      const msg2 = createMessage({ id: 20, conversation_key: 'conv2', acked: 0 });
      messageCache.set('conv1', createEntry([msg1]));
      messageCache.set('conv2', createEntry([msg2]));

      messageCache.updateAck(20, 5);

      expect(messageCache.get('conv1')!.messages[0].acked).toBe(0); // Unchanged
      expect(messageCache.get('conv2')!.messages[0].acked).toBe(5); // Updated
    });

    it('does nothing for unknown message id', () => {
      const msg = createMessage({ id: 42, acked: 0 });
      messageCache.set('conv1', createEntry([msg]));

      messageCache.updateAck(999, 3);

      expect(messageCache.get('conv1')!.messages[0].acked).toBe(0);
    });
  });

  describe('remove', () => {
    it('removes a specific conversation', () => {
      messageCache.set('conv1', createEntry());
      messageCache.set('conv2', createEntry());

      messageCache.remove('conv1');

      expect(messageCache.get('conv1')).toBeUndefined();
      expect(messageCache.get('conv2')).toBeDefined();
      expect(messageCache.size()).toBe(1);
    });

    it('does nothing for non-existent key', () => {
      messageCache.set('conv1', createEntry());
      messageCache.remove('nonexistent');
      expect(messageCache.size()).toBe(1);
    });
  });

  describe('reconcile', () => {
    it('returns null when cache matches fetched data (happy path)', () => {
      const msgs = [
        createMessage({ id: 1, acked: 2 }),
        createMessage({ id: 2, acked: 0 }),
        createMessage({ id: 3, acked: 1 }),
      ];
      const fetched = [
        createMessage({ id: 1, acked: 2 }),
        createMessage({ id: 2, acked: 0 }),
        createMessage({ id: 3, acked: 1 }),
      ];

      expect(messageCache.reconcile(msgs, fetched)).toBeNull();
    });

    it('detects new messages missing from cache', () => {
      const current = [createMessage({ id: 1 }), createMessage({ id: 2 })];
      const fetched = [
        createMessage({ id: 1 }),
        createMessage({ id: 2 }),
        createMessage({ id: 3, text: 'missed via WS' }),
      ];

      const merged = messageCache.reconcile(current, fetched);
      expect(merged).not.toBeNull();
      expect(merged!.map((m) => m.id)).toEqual([1, 2, 3]);
    });

    it('detects stale ack counts', () => {
      const current = [createMessage({ id: 1, acked: 0 })];
      const fetched = [createMessage({ id: 1, acked: 3 })];

      const merged = messageCache.reconcile(current, fetched);
      expect(merged).not.toBeNull();
      expect(merged![0].acked).toBe(3);
    });

    it('preserves older paginated messages not in fetch', () => {
      // Current state has recent page + older paginated messages
      const current = [
        createMessage({ id: 3 }),
        createMessage({ id: 2 }),
        createMessage({ id: 1 }), // older, from pagination
      ];
      // Fetch only returns recent page with a new message
      const fetched = [
        createMessage({ id: 4, text: 'new' }),
        createMessage({ id: 3 }),
        createMessage({ id: 2 }),
      ];

      const merged = messageCache.reconcile(current, fetched);
      expect(merged).not.toBeNull();
      // Should have fetched page + older paginated message
      expect(merged!.map((m) => m.id)).toEqual([4, 3, 2, 1]);
    });

    it('returns null for empty fetched and empty current', () => {
      expect(messageCache.reconcile([], [])).toBeNull();
    });

    it('detects difference when current is empty but fetch has messages', () => {
      const fetched = [createMessage({ id: 1 })];

      const merged = messageCache.reconcile([], fetched);
      expect(merged).not.toBeNull();
      expect(merged!).toHaveLength(1);
    });

    it('detects stale paths', () => {
      const current = [
        createMessage({ id: 1, acked: 1, paths: [{ path: '1A', received_at: 1700000000 }] }),
      ];
      const fetched = [
        createMessage({
          id: 1,
          acked: 1,
          paths: [
            { path: '1A', received_at: 1700000000 },
            { path: '2B', received_at: 1700000001 },
          ],
        }),
      ];

      const merged = messageCache.reconcile(current, fetched);
      expect(merged).not.toBeNull();
      expect(merged![0].paths).toHaveLength(2);
    });

    it('detects stale text (e.g. post-decryption)', () => {
      const current = [createMessage({ id: 1, text: '[encrypted]' })];
      const fetched = [createMessage({ id: 1, text: 'Hello world' })];

      const merged = messageCache.reconcile(current, fetched);
      expect(merged).not.toBeNull();
      expect(merged![0].text).toBe('Hello world');
    });

    it('returns null when acked, paths length, and text all match', () => {
      const paths = [{ path: '1A', received_at: 1700000000 }];
      const current = [createMessage({ id: 1, acked: 2, paths, text: 'Hello' })];
      const fetched = [createMessage({ id: 1, acked: 2, paths, text: 'Hello' })];

      expect(messageCache.reconcile(current, fetched)).toBeNull();
    });
  });

  describe('clear', () => {
    it('removes all entries', () => {
      messageCache.set('conv1', createEntry());
      messageCache.set('conv2', createEntry());
      messageCache.set('conv3', createEntry());

      messageCache.clear();

      expect(messageCache.size()).toBe(0);
      expect(messageCache.get('conv1')).toBeUndefined();
    });
  });
});
