import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

import * as messageCache from '../messageCache';
import { useConversationMessages } from '../hooks/useConversationMessages';
import type { Conversation, Message } from '../types';

const mockGetMessages = vi.fn<(...args: unknown[]) => Promise<Message[]>>();
const mockGetMessagesAround = vi.fn();

vi.mock('../api', () => ({
  api: {
    getMessages: (...args: unknown[]) => mockGetMessages(...args),
    getMessagesAround: (...args: unknown[]) => mockGetMessagesAround(...args),
  },
  isAbortError: (err: unknown) => err instanceof DOMException && err.name === 'AbortError',
}));

function createConversation(): Conversation {
  return {
    type: 'contact',
    id: 'abc123',
    name: 'Test Contact',
  };
}

function createMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 42,
    type: 'PRIV',
    conversation_key: 'abc123',
    text: 'hello',
    sender_timestamp: 1700000000,
    received_at: 1700000001,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: true,
    acked: 0,
    sender_name: null,
    ...overrides,
  };
}

function createDeferred<T>() {
  let resolve: (value: T | PromiseLike<T>) => void = () => {};
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe('useConversationMessages ACK ordering', () => {
  beforeEach(() => {
    mockGetMessages.mockReset();
    messageCache.clear();
  });

  it('applies buffered ACK when message is added after ACK event', async () => {
    mockGetMessages.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useConversationMessages(createConversation()));

    await waitFor(() => expect(mockGetMessages).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(result.current.messagesLoading).toBe(false));

    const paths = [{ path: 'A1B2', received_at: 1700000010 }];
    act(() => {
      result.current.updateMessageAck(42, 2, paths);
    });

    act(() => {
      const added = result.current.addMessageIfNew(
        createMessage({ id: 42, acked: 0, paths: null })
      );
      expect(added).toBe(true);
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].acked).toBe(2);
    expect(result.current.messages[0].paths).toEqual(paths);
  });

  it('applies buffered ACK to message returned by in-flight fetch', async () => {
    const deferred = createDeferred<Message[]>();
    mockGetMessages.mockReturnValueOnce(deferred.promise);

    const { result } = renderHook(() => useConversationMessages(createConversation()));
    await waitFor(() => expect(mockGetMessages).toHaveBeenCalledTimes(1));

    const paths = [{ path: 'C3D4', received_at: 1700000011 }];
    act(() => {
      result.current.updateMessageAck(42, 1, paths);
    });

    deferred.resolve([createMessage({ id: 42, acked: 0, paths: null })]);

    await waitFor(() => expect(result.current.messages).toHaveLength(1));
    expect(result.current.messages[0].acked).toBe(1);
    expect(result.current.messages[0].paths).toEqual(paths);
  });

  it('preserves a WebSocket-arrived message when latest fetch resolves afterward', async () => {
    const deferred = createDeferred<Message[]>();
    mockGetMessages.mockReturnValueOnce(deferred.promise);

    const { result } = renderHook(() => useConversationMessages(createConversation()));
    await waitFor(() => expect(mockGetMessages).toHaveBeenCalledTimes(1));

    act(() => {
      const added = result.current.addMessageIfNew(
        createMessage({
          id: 99,
          text: 'ws-arrived',
          sender_timestamp: 1700000099,
          received_at: 1700000099,
        })
      );
      expect(added).toBe(true);
    });

    deferred.resolve([
      createMessage({
        id: 42,
        text: 'rest-fetched',
        sender_timestamp: 1700000000,
        received_at: 1700000001,
      }),
    ]);

    await waitFor(() => expect(result.current.messagesLoading).toBe(false));
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages.some((msg) => msg.text === 'rest-fetched')).toBe(true);
    expect(result.current.messages.some((msg) => msg.text === 'ws-arrived')).toBe(true);
  });

  it('keeps highest ACK state when out-of-order ACK updates arrive', async () => {
    mockGetMessages.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useConversationMessages(createConversation()));

    await waitFor(() => expect(mockGetMessages).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(result.current.messagesLoading).toBe(false));

    act(() => {
      result.current.addMessageIfNew(createMessage({ id: 42, acked: 0, paths: null }));
    });

    const highAckPaths = [
      { path: 'A1B2', received_at: 1700000010 },
      { path: 'A1C3', received_at: 1700000011 },
    ];
    const staleAckPaths = [{ path: 'A1B2', received_at: 1700000010 }];

    act(() => {
      result.current.updateMessageAck(42, 3, highAckPaths);
      result.current.updateMessageAck(42, 2, staleAckPaths);
    });

    expect(result.current.messages[0].acked).toBe(3);
    expect(result.current.messages[0].paths).toEqual(highAckPaths);
  });
});

describe('useConversationMessages conversation switch', () => {
  beforeEach(() => {
    mockGetMessages.mockReset();
    messageCache.clear();
  });

  it('resets loadingOlder when switching conversations mid-fetch', async () => {
    const convA: Conversation = { type: 'contact', id: 'conv_a', name: 'Contact A' };
    const convB: Conversation = { type: 'contact', id: 'conv_b', name: 'Contact B' };

    // Conv A initial fetch: return 200 messages so hasOlderMessages = true
    const fullPage = Array.from({ length: 200 }, (_, i) =>
      createMessage({
        id: i + 1,
        conversation_key: 'conv_a',
        text: `msg-${i}`,
        sender_timestamp: 1700000000 + i,
        received_at: 1700000000 + i,
      })
    );
    mockGetMessages.mockResolvedValueOnce(fullPage);

    const { result, rerender } = renderHook(
      ({ conv }: { conv: Conversation }) => useConversationMessages(conv),
      { initialProps: { conv: convA } }
    );

    await waitFor(() => expect(result.current.messagesLoading).toBe(false));
    expect(result.current.hasOlderMessages).toBe(true);
    expect(result.current.messages).toHaveLength(200);

    // Start fetching older messages — use a deferred promise so it stays in-flight
    const olderDeferred = createDeferred<Message[]>();
    mockGetMessages.mockReturnValueOnce(olderDeferred.promise);

    act(() => {
      result.current.fetchOlderMessages();
    });

    expect(result.current.loadingOlder).toBe(true);

    // Switch to conv B while older-messages fetch is still pending
    mockGetMessages.mockResolvedValueOnce([createMessage({ id: 999, conversation_key: 'conv_b' })]);
    rerender({ conv: convB });

    // loadingOlder must reset immediately — no phantom spinner in conv B
    await waitFor(() => expect(result.current.loadingOlder).toBe(false));
    await waitFor(() => expect(result.current.messagesLoading).toBe(false));
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].conversation_key).toBe('conv_b');

    // Resolve the stale older-messages fetch — should not affect conv B's state
    olderDeferred.resolve([
      createMessage({ id: 500, conversation_key: 'conv_a', text: 'stale-old' }),
    ]);

    // Give the stale response time to be processed (it should be discarded)
    await new Promise((r) => setTimeout(r, 50));
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].conversation_key).toBe('conv_b');
  });

  it('aborts in-flight fetch when switching conversations', async () => {
    const convA: Conversation = { type: 'contact', id: 'conv_a', name: 'Contact A' };
    const convB: Conversation = { type: 'contact', id: 'conv_b', name: 'Contact B' };

    // Conv A: never resolves (simulates slow network)
    mockGetMessages.mockReturnValueOnce(new Promise(() => {}));

    const { result, rerender } = renderHook(
      ({ conv }: { conv: Conversation }) => useConversationMessages(conv),
      { initialProps: { conv: convA } }
    );

    // Should be loading
    expect(result.current.messagesLoading).toBe(true);

    // Verify the API was called with an AbortSignal
    const firstCallSignal = (mockGetMessages as Mock).mock.calls[0]?.[1];
    expect(firstCallSignal).toBeInstanceOf(AbortSignal);

    // Switch to conv B
    mockGetMessages.mockResolvedValueOnce([createMessage({ id: 1, conversation_key: 'conv_b' })]);
    rerender({ conv: convB });

    // The signal from conv A's fetch should have been aborted
    expect(firstCallSignal.aborted).toBe(true);

    // Conv B should load normally
    await waitFor(() => expect(result.current.messagesLoading).toBe(false));
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].conversation_key).toBe('conv_b');
  });
});

describe('useConversationMessages background reconcile ordering', () => {
  beforeEach(() => {
    mockGetMessages.mockReset();
    messageCache.clear();
  });

  it('ignores stale reconnect reconcile responses that finish after newer ones', async () => {
    const conv = createConversation();
    mockGetMessages.mockResolvedValueOnce([
      createMessage({ id: 42, text: 'initial snapshot', acked: 0 }),
    ]);

    const { result } = renderHook(() => useConversationMessages(conv));

    await waitFor(() => expect(result.current.messagesLoading).toBe(false));
    expect(result.current.messages[0].text).toBe('initial snapshot');

    const firstReconcile = createDeferred<Message[]>();
    const secondReconcile = createDeferred<Message[]>();
    mockGetMessages
      .mockReturnValueOnce(firstReconcile.promise)
      .mockReturnValueOnce(secondReconcile.promise);

    act(() => {
      result.current.triggerReconcile();
      result.current.triggerReconcile();
    });

    secondReconcile.resolve([createMessage({ id: 42, text: 'newer snapshot', acked: 2 })]);
    await waitFor(() => expect(result.current.messages[0].text).toBe('newer snapshot'));
    expect(result.current.messages[0].acked).toBe(2);

    firstReconcile.resolve([createMessage({ id: 42, text: 'stale snapshot', acked: 1 })]);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(result.current.messages[0].text).toBe('newer snapshot');
    expect(result.current.messages[0].acked).toBe(2);
  });

  it('clears stale hasOlderMessages when cached conversations reconcile to a short latest page', async () => {
    const conv = createConversation();
    const cachedMessage = createMessage({ id: 42, text: 'cached snapshot' });

    messageCache.set(conv.id, {
      messages: [cachedMessage],
      seenContent: new Set([
        `PRIV-${cachedMessage.conversation_key}-${cachedMessage.text}-${cachedMessage.sender_timestamp}`,
      ]),
      hasOlderMessages: true,
    });

    mockGetMessages.mockResolvedValueOnce([cachedMessage]);

    const { result } = renderHook(() => useConversationMessages(conv));

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.hasOlderMessages).toBe(true);

    await waitFor(() => expect(mockGetMessages).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(result.current.hasOlderMessages).toBe(false));
  });
});

describe('useConversationMessages older-page dedup and reentry', () => {
  beforeEach(() => {
    mockGetMessages.mockReset();
    messageCache.clear();
  });

  it('prevents duplicate overlapping older-page fetches in the same tick', async () => {
    const conv: Conversation = { type: 'contact', id: 'conv_a', name: 'Contact A' };

    const fullPage = Array.from({ length: 200 }, (_, i) =>
      createMessage({
        id: i + 1,
        conversation_key: 'conv_a',
        text: `msg-${i + 1}`,
        sender_timestamp: 1700000000 + i,
        received_at: 1700000000 + i,
      })
    );
    mockGetMessages.mockResolvedValueOnce(fullPage);

    const olderDeferred = createDeferred<Message[]>();
    mockGetMessages.mockReturnValueOnce(olderDeferred.promise);

    const { result } = renderHook(() => useConversationMessages(conv));

    await waitFor(() => expect(result.current.messagesLoading).toBe(false));
    expect(result.current.messages).toHaveLength(200);
    expect(result.current.hasOlderMessages).toBe(true);

    act(() => {
      void result.current.fetchOlderMessages();
      void result.current.fetchOlderMessages();
    });

    expect(mockGetMessages).toHaveBeenCalledTimes(2); // initial page + one older fetch

    olderDeferred.resolve([
      createMessage({
        id: 0,
        conversation_key: 'conv_a',
        text: 'older-msg',
        sender_timestamp: 1699999999,
        received_at: 1699999999,
      }),
    ]);

    await waitFor(() => expect(result.current.loadingOlder).toBe(false));
    expect(result.current.messages).toHaveLength(201);
    expect(result.current.messages.filter((msg) => msg.id === 0)).toHaveLength(1);
  });

  it('does not append duplicate messages from an overlapping older page', async () => {
    const conv: Conversation = { type: 'contact', id: 'conv_a', name: 'Contact A' };

    const fullPage = Array.from({ length: 200 }, (_, i) =>
      createMessage({
        id: i + 1,
        conversation_key: 'conv_a',
        text: `msg-${i + 1}`,
        sender_timestamp: 1700000000 + i,
        received_at: 1700000000 + i,
      })
    );
    mockGetMessages.mockResolvedValueOnce(fullPage);
    mockGetMessages.mockResolvedValueOnce([
      createMessage({
        id: 1,
        conversation_key: 'conv_a',
        text: 'msg-1',
        sender_timestamp: 1700000000,
        received_at: 1700000000,
      }),
      createMessage({
        id: 0,
        conversation_key: 'conv_a',
        text: 'older-msg',
        sender_timestamp: 1699999999,
        received_at: 1699999999,
      }),
    ]);

    const { result } = renderHook(() => useConversationMessages(conv));

    await waitFor(() => expect(result.current.messagesLoading).toBe(false));
    expect(result.current.messages).toHaveLength(200);

    await act(async () => {
      await result.current.fetchOlderMessages();
    });

    expect(result.current.messages.filter((msg) => msg.id === 1)).toHaveLength(1);
    expect(result.current.messages.filter((msg) => msg.id === 0)).toHaveLength(1);
    expect(result.current.messages).toHaveLength(201);
  });
});

describe('useConversationMessages forward pagination', () => {
  beforeEach(() => {
    mockGetMessages.mockReset();
    mockGetMessagesAround.mockReset();
    messageCache.clear();
  });

  it('fetchNewerMessages loads newer messages and appends them', async () => {
    const conv: Conversation = { type: 'channel', id: 'ch1', name: 'Channel' };

    // Initial load returns messages that indicate there are newer ones
    // (we'll set hasNewerMessages via targetMessageId + getMessagesAround)
    const initialMessages = Array.from({ length: 3 }, (_, i) =>
      createMessage({
        id: i + 1,
        conversation_key: 'ch1',
        text: `msg-${i}`,
        sender_timestamp: 1700000000 + i,
        received_at: 1700000000 + i,
      })
    );

    mockGetMessagesAround.mockResolvedValueOnce({
      messages: initialMessages,
      has_older: false,
      has_newer: true,
    });

    const { result } = renderHook(
      ({ conv, target }: { conv: Conversation; target: number | null }) =>
        useConversationMessages(conv, target),
      { initialProps: { conv, target: 2 } }
    );

    await waitFor(() => expect(result.current.messagesLoading).toBe(false));
    expect(result.current.messages).toHaveLength(3);
    expect(result.current.hasNewerMessages).toBe(true);

    // Now fetch newer messages
    const newerMessages = [
      createMessage({
        id: 4,
        conversation_key: 'ch1',
        text: 'msg-3',
        sender_timestamp: 1700000003,
        received_at: 1700000003,
      }),
    ];
    mockGetMessages.mockResolvedValueOnce(newerMessages);

    await act(async () => {
      await result.current.fetchNewerMessages();
    });

    expect(result.current.messages).toHaveLength(4);
    // Less than page size → no more newer messages
    expect(result.current.hasNewerMessages).toBe(false);
  });

  it('fetchNewerMessages deduplicates against seen messages', async () => {
    const conv: Conversation = { type: 'channel', id: 'ch1', name: 'Channel' };

    const initialMessages = [
      createMessage({
        id: 1,
        conversation_key: 'ch1',
        text: 'msg-0',
        sender_timestamp: 1700000000,
        received_at: 1700000000,
      }),
    ];

    mockGetMessagesAround.mockResolvedValueOnce({
      messages: initialMessages,
      has_older: false,
      has_newer: true,
    });

    const { result } = renderHook(
      ({ conv, target }: { conv: Conversation; target: number | null }) =>
        useConversationMessages(conv, target),
      { initialProps: { conv, target: 1 } }
    );

    await waitFor(() => expect(result.current.messagesLoading).toBe(false));

    // Simulate WS adding a message with the same content key
    act(() => {
      result.current.addMessageIfNew(
        createMessage({
          id: 2,
          conversation_key: 'ch1',
          text: 'duplicate-content',
          sender_timestamp: 1700000001,
          received_at: 1700000001,
        })
      );
    });

    // fetchNewerMessages returns the same content (different id but same content key)
    mockGetMessages.mockResolvedValueOnce([
      createMessage({
        id: 3,
        conversation_key: 'ch1',
        text: 'duplicate-content',
        sender_timestamp: 1700000001,
        received_at: 1700000001,
      }),
    ]);

    await act(async () => {
      await result.current.fetchNewerMessages();
    });

    // Should not have a duplicate
    const dupes = result.current.messages.filter((m) => m.text === 'duplicate-content');
    expect(dupes).toHaveLength(1);
  });

  it('jumpToBottom clears hasNewerMessages and refetches latest', async () => {
    const conv: Conversation = { type: 'channel', id: 'ch1', name: 'Channel' };

    const aroundMessages = [
      createMessage({
        id: 5,
        conversation_key: 'ch1',
        text: 'around-msg',
        sender_timestamp: 1700000005,
        received_at: 1700000005,
      }),
    ];

    mockGetMessagesAround.mockResolvedValueOnce({
      messages: aroundMessages,
      has_older: true,
      has_newer: true,
    });

    const { result } = renderHook(
      ({ conv, target }: { conv: Conversation; target: number | null }) =>
        useConversationMessages(conv, target),
      { initialProps: { conv, target: 5 } }
    );

    await waitFor(() => expect(result.current.messagesLoading).toBe(false));
    expect(result.current.hasNewerMessages).toBe(true);

    // Jump to bottom
    const latestMessages = [
      createMessage({
        id: 10,
        conversation_key: 'ch1',
        text: 'latest-msg',
        sender_timestamp: 1700000010,
        received_at: 1700000010,
      }),
    ];
    mockGetMessages.mockResolvedValueOnce(latestMessages);

    act(() => {
      result.current.jumpToBottom();
    });

    await waitFor(() => expect(result.current.messagesLoading).toBe(false));
    expect(result.current.hasNewerMessages).toBe(false);
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].text).toBe('latest-msg');
  });

  it('preserves around-loaded messages when the jump target is cleared in the same conversation', async () => {
    const conv: Conversation = { type: 'channel', id: 'ch1', name: 'Channel' };

    const aroundMessages = [
      createMessage({
        id: 4,
        conversation_key: 'ch1',
        text: 'older-context',
        sender_timestamp: 1700000004,
        received_at: 1700000004,
      }),
      createMessage({
        id: 5,
        conversation_key: 'ch1',
        text: 'target-message',
        sender_timestamp: 1700000005,
        received_at: 1700000005,
      }),
      createMessage({
        id: 6,
        conversation_key: 'ch1',
        text: 'newer-context',
        sender_timestamp: 1700000006,
        received_at: 1700000006,
      }),
    ];

    mockGetMessagesAround.mockResolvedValueOnce({
      messages: aroundMessages,
      has_older: true,
      has_newer: true,
    });

    const { result, rerender } = renderHook<
      ReturnType<typeof useConversationMessages>,
      { conv: Conversation; target: number | null }
    >(({ conv, target }) => useConversationMessages(conv, target), {
      initialProps: { conv, target: 5 },
    });

    await waitFor(() => expect(result.current.messagesLoading).toBe(false));
    expect(result.current.messages.map((message) => message.text)).toEqual([
      'older-context',
      'target-message',
      'newer-context',
    ]);
    expect(mockGetMessages).not.toHaveBeenCalled();

    rerender({ conv, target: null });

    await waitFor(() =>
      expect(result.current.messages.map((message) => message.text)).toEqual([
        'older-context',
        'target-message',
        'newer-context',
      ])
    );
    expect(mockGetMessages).not.toHaveBeenCalled();
    expect(result.current.hasNewerMessages).toBe(true);
  });
});
