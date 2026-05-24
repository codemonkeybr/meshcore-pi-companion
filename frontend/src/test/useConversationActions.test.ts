import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useConversationActions } from '../hooks/useConversationActions';
import type { Channel, Contact, Conversation, Message, PathDiscoveryResponse } from '../types';

const mocks = vi.hoisted(() => ({
  api: {
    requestPathDiscovery: vi.fn(),
    requestTrace: vi.fn(),
    resendChannelMessage: vi.fn(),
    sendChannelMessage: vi.fn(),
    sendDirectMessage: vi.fn(),
    setChannelFloodScopeOverride: vi.fn(),
  },
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock('../api', () => ({
  api: mocks.api,
}));

vi.mock('../components/ui/sonner', () => ({
  toast: mocks.toast,
}));

const publicChannel: Channel = {
  key: '8B3387E9C5CDEA6AC9E5EDBAA115CD72',
  name: 'Public',
  is_hashtag: false,
  on_radio: false,
  last_read_at: null,
  favorite: false,
  muted: false,
};

const sentMessage: Message = {
  id: 42,
  type: 'CHAN',
  conversation_key: publicChannel.key,
  text: 'hello mesh',
  sender_timestamp: 1700000000,
  received_at: 1700000001,
  paths: null,
  txt_type: 0,
  signature: null,
  sender_key: null,
  outgoing: true,
  acked: 0,
  sender_name: 'Radio',
};

function createArgs(overrides: Partial<Parameters<typeof useConversationActions>[0]> = {}) {
  const activeConversation: Conversation = {
    type: 'channel',
    id: publicChannel.key,
    name: publicChannel.name,
  };

  return {
    activeConversation,
    activeConversationRef: { current: activeConversation },
    setContacts: vi.fn(),
    setChannels: vi.fn(),
    observeMessage: vi.fn(() => ({ added: true, activeConversation: true })),
    messageInputRef: { current: { appendText: vi.fn(), focus: vi.fn() } },
    ...overrides,
  };
}

describe('useConversationActions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('appends a sent message when the user is still in the same conversation', async () => {
    mocks.api.sendChannelMessage.mockResolvedValue(sentMessage);
    const args = createArgs();

    const { result } = renderHook(() => useConversationActions(args));

    await act(async () => {
      await result.current.handleSendMessage(sentMessage.text);
    });

    expect(mocks.api.sendChannelMessage).toHaveBeenCalledWith(publicChannel.key, sentMessage.text);
    expect(args.observeMessage).toHaveBeenCalledWith(sentMessage);
  });

  it('does not append a sent message after the active conversation changes', async () => {
    let resolveSend: ((message: Message) => void) | null = null;
    mocks.api.sendChannelMessage.mockImplementation(
      () =>
        new Promise<Message>((resolve) => {
          resolveSend = resolve;
        })
    );

    const args = createArgs();
    const { result } = renderHook(() => useConversationActions(args));

    await act(async () => {
      const sendPromise = result.current.handleSendMessage(sentMessage.text);
      args.activeConversationRef.current = {
        type: 'contact',
        id: 'aa'.repeat(32),
        name: 'Alice',
      };
      resolveSend?.(sentMessage);
      await sendPromise;
    });

    expect(args.observeMessage).not.toHaveBeenCalled();
  });

  it('appends sender mentions into the message input', () => {
    const args = createArgs();
    const { result } = renderHook(() => useConversationActions(args));

    act(() => {
      result.current.handleSenderClick('Alice');
    });

    expect(args.messageInputRef.current?.appendText).toHaveBeenCalledWith('@[Alice] ');
  });

  it('appends a new-timestamp resend immediately for the active channel', async () => {
    const resentMessage: Message = {
      ...sentMessage,
      id: 99,
      sender_timestamp: 1700000100,
      received_at: 1700000100,
    };
    mocks.api.resendChannelMessage.mockResolvedValue({
      status: 'ok',
      message_id: resentMessage.id,
      message: resentMessage,
    });
    const args = createArgs();

    const { result } = renderHook(() => useConversationActions(args));

    await act(async () => {
      await result.current.handleResendChannelMessage(sentMessage.id, true);
    });

    expect(mocks.api.resendChannelMessage).toHaveBeenCalledWith(sentMessage.id, true);
    expect(args.observeMessage).toHaveBeenCalledWith(resentMessage);
  });

  it('does not append a byte-perfect resend locally', async () => {
    mocks.api.resendChannelMessage.mockResolvedValue({
      status: 'ok',
      message_id: sentMessage.id,
    });
    const args = createArgs();

    const { result } = renderHook(() => useConversationActions(args));

    await act(async () => {
      await result.current.handleResendChannelMessage(sentMessage.id, false);
    });

    expect(args.observeMessage).not.toHaveBeenCalled();
  });

  it('does not append a resend if the user has switched conversations', async () => {
    const resentMessage: Message = {
      ...sentMessage,
      id: 100,
      sender_timestamp: 1700000200,
      received_at: 1700000200,
    };
    mocks.api.resendChannelMessage.mockResolvedValue({
      status: 'ok',
      message_id: resentMessage.id,
      message: resentMessage,
    });
    const args = createArgs();
    const { result } = renderHook(() => useConversationActions(args));

    await act(async () => {
      const resendPromise = result.current.handleResendChannelMessage(sentMessage.id, true);
      args.activeConversationRef.current = {
        type: 'channel',
        id: 'AA'.repeat(16),
        name: 'Other',
      };
      await resendPromise;
    });

    expect(args.observeMessage).not.toHaveBeenCalled();
  });

  it('merges returned contact data after path discovery', async () => {
    const contactKey = 'aa'.repeat(32);
    const discoveredContact: Contact = {
      public_key: contactKey,
      name: 'Alice',
      type: 1,
      flags: 0,
      direct_path: 'AABB',
      direct_path_len: 2,
      direct_path_hash_mode: 0,
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
    const response: PathDiscoveryResponse = {
      contact: discoveredContact,
      forward_path: { path: 'AABB', path_len: 2, path_hash_mode: 0 },
      return_path: { path: 'CC', path_len: 1, path_hash_mode: 0 },
    };
    mocks.api.requestPathDiscovery.mockResolvedValue(response);
    const setContacts = vi.fn();
    const args = createArgs({
      activeConversation: { type: 'contact', id: contactKey, name: 'Alice' },
      activeConversationRef: { current: { type: 'contact', id: contactKey, name: 'Alice' } },
      setContacts,
    });

    const { result } = renderHook(() => useConversationActions(args));

    await act(async () => {
      await result.current.handlePathDiscovery(contactKey);
    });

    expect(mocks.api.requestPathDiscovery).toHaveBeenCalledWith(contactKey);
    expect(setContacts).toHaveBeenCalledTimes(1);
    const updater = setContacts.mock.calls[0][0] as (contacts: Contact[]) => Contact[];
    expect(updater([])).toEqual([discoveredContact]);
  });
});
