import { describe, expect, it } from 'vitest';

import { getUnreadBoundaryBackfillKey } from '../App';
import type { Conversation, Message } from '../types';

function createMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 1,
    type: 'CHAN',
    conversation_key: 'channel-1',
    text: 'Alice: hello',
    sender_timestamp: 1700000000,
    received_at: 1700000001,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: false,
    acked: 0,
    sender_name: 'Alice',
    ...overrides,
  };
}

const channelConversation: Conversation = {
  type: 'channel',
  id: 'channel-1',
  name: 'Busy room',
};

describe('getUnreadBoundaryBackfillKey', () => {
  it('returns a fetch key when the unread boundary is older than the loaded window', () => {
    expect(
      getUnreadBoundaryBackfillKey({
        activeConversation: channelConversation,
        unreadMarker: {
          channelId: 'channel-1',
          lastReadAt: 1700000000,
        },
        messages: [
          createMessage({ id: 20, received_at: 1700000200 }),
          createMessage({ id: 21, received_at: 1700000300 }),
        ],
        messagesLoading: false,
        loadingOlder: false,
        hasOlderMessages: true,
      })
    ).toBe('channel-1:1700000000:20');
  });

  it('does not backfill when the loaded window already reaches the unread boundary', () => {
    expect(
      getUnreadBoundaryBackfillKey({
        activeConversation: channelConversation,
        unreadMarker: {
          channelId: 'channel-1',
          lastReadAt: 1700000200,
        },
        messages: [
          createMessage({ id: 20, received_at: 1700000200 }),
          createMessage({ id: 21, received_at: 1700000300 }),
        ],
        messagesLoading: false,
        loadingOlder: false,
        hasOlderMessages: true,
      })
    ).toBeNull();
  });

  it('does not backfill when there is no older history to fetch', () => {
    expect(
      getUnreadBoundaryBackfillKey({
        activeConversation: channelConversation,
        unreadMarker: {
          channelId: 'channel-1',
          lastReadAt: 1700000000,
        },
        messages: [createMessage({ id: 20, received_at: 1700000200 })],
        messagesLoading: false,
        loadingOlder: false,
        hasOlderMessages: false,
      })
    ).toBeNull();
  });

  it('does not backfill for channels where everything is unread', () => {
    expect(
      getUnreadBoundaryBackfillKey({
        activeConversation: channelConversation,
        unreadMarker: {
          channelId: 'channel-1',
          lastReadAt: null,
        },
        messages: [createMessage({ id: 20, received_at: 1700000200 })],
        messagesLoading: false,
        loadingOlder: false,
        hasOlderMessages: true,
      })
    ).toBeNull();
  });
});
