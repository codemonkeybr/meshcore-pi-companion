import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useBrowserNotifications } from '../hooks/useBrowserNotifications';
import type { Message } from '../types';

const mocks = vi.hoisted(() => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock('../components/ui/sonner', () => ({
  toast: mocks.toast,
}));

const incomingChannelMessage: Message = {
  id: 42,
  type: 'CHAN',
  conversation_key: 'ab'.repeat(16),
  text: 'hello room',
  sender_timestamp: 1700000000,
  received_at: 1700000001,
  paths: null,
  txt_type: 0,
  signature: null,
  sender_key: 'cd'.repeat(32),
  outgoing: false,
  acked: 0,
  sender_name: 'Alice',
  channel_name: '#flightless',
};

describe('useBrowserNotifications', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    window.location.hash = '';
    vi.spyOn(window, 'open').mockReturnValue(null);

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'hidden',
    });
    vi.spyOn(document, 'hasFocus').mockReturnValue(false);

    const NotificationMock = vi.fn().mockImplementation(function (this: Record<string, unknown>) {
      this.close = vi.fn();
      this.onclick = null;
    });
    Object.assign(NotificationMock, {
      permission: 'granted',
      requestPermission: vi.fn(async () => 'granted'),
    });
    Object.defineProperty(window, 'Notification', {
      configurable: true,
      value: NotificationMock,
    });
  });

  it('stores notification opt-in per conversation', async () => {
    const { result } = renderHook(() => useBrowserNotifications());

    await act(async () => {
      await result.current.toggleConversationNotifications(
        'channel',
        incomingChannelMessage.conversation_key,
        '#flightless'
      );
    });

    expect(
      result.current.isConversationNotificationsEnabled(
        'channel',
        incomingChannelMessage.conversation_key
      )
    ).toBe(true);
    expect(result.current.isConversationNotificationsEnabled('contact', 'ef'.repeat(32))).toBe(
      false
    );
    expect(window.Notification).toHaveBeenCalledWith('New message in #flightless', {
      body: 'Notifications will look like this. These require the tab to stay open, and will not be reliable on mobile.',
      icon: '/favicon-256x256.png',
      tag: `meshcore-notification-preview-channel-${incomingChannelMessage.conversation_key}`,
    });
  });

  it('only sends desktop notifications for opted-in conversations', async () => {
    const { result } = renderHook(() => useBrowserNotifications());

    await act(async () => {
      await result.current.toggleConversationNotifications(
        'channel',
        incomingChannelMessage.conversation_key,
        '#flightless'
      );
    });

    act(() => {
      result.current.notifyIncomingMessage(incomingChannelMessage);
      result.current.notifyIncomingMessage({
        ...incomingChannelMessage,
        id: 43,
        conversation_key: '34'.repeat(16),
        channel_name: '#elsewhere',
      });
    });

    expect(window.Notification).toHaveBeenCalledTimes(2);
    expect(window.Notification).toHaveBeenNthCalledWith(2, 'New message in #flightless', {
      body: 'hello room',
      icon: '/favicon-256x256.png',
      tag: 'meshcore-message-42',
    });
  });

  it('notification click deep-links to the conversation hash', async () => {
    const focusSpy = vi.spyOn(window, 'focus').mockImplementation(() => {});
    const { result } = renderHook(() => useBrowserNotifications());

    await act(async () => {
      await result.current.toggleConversationNotifications(
        'channel',
        incomingChannelMessage.conversation_key,
        '#flightless'
      );
    });

    act(() => {
      result.current.notifyIncomingMessage(incomingChannelMessage);
    });

    const notificationInstance = (window.Notification as unknown as ReturnType<typeof vi.fn>).mock
      .instances[1] as {
      onclick: (() => void) | null;
      close: ReturnType<typeof vi.fn>;
    };

    act(() => {
      notificationInstance.onclick?.();
    });

    expect(window.open).toHaveBeenCalledWith(
      `${window.location.origin}${window.location.pathname}#channel/${incomingChannelMessage.conversation_key}/%23flightless`,
      '_self'
    );
    expect(focusSpy).toHaveBeenCalledTimes(1);
    expect(notificationInstance.close).toHaveBeenCalledTimes(1);
  });
});
