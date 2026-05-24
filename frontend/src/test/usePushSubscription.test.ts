import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { usePushSubscription } from '../hooks/usePushSubscription';

const mocks = vi.hoisted(() => ({
  api: {
    getPushSubscriptions: vi.fn(),
    getPushConversations: vi.fn(),
    getVapidPublicKey: vi.fn(),
    pushSubscribe: vi.fn(),
    deletePushSubscription: vi.fn(),
    togglePushConversation: vi.fn(),
    testPushSubscription: vi.fn(),
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

function bytesToBase64Url(bytes: number[]): string {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/g, '');
}

describe('usePushSubscription', () => {
  const vapidOldBytes = [1, 2, 3, 4];
  const vapidNewBytes = [5, 6, 7, 8];
  const oldKey = new Uint8Array(vapidOldBytes).buffer;
  const newKeyBase64 = bytesToBase64Url(vapidNewBytes);

  let activeSubscription: {
    endpoint: string;
    options: { applicationServerKey: ArrayBuffer };
    toJSON: () => { endpoint: string; keys: { p256dh: string; auth: string } };
    unsubscribe: ReturnType<typeof vi.fn>;
  } | null;
  let replacementSubscription: {
    endpoint: string;
    options: { applicationServerKey: ArrayBuffer };
    toJSON: () => { endpoint: string; keys: { p256dh: string; auth: string } };
    unsubscribe: ReturnType<typeof vi.fn>;
  };
  let getSubscriptionMock: ReturnType<typeof vi.fn>;
  let subscribeMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();

    activeSubscription = {
      endpoint: 'https://push.example.test/sub-old',
      options: { applicationServerKey: oldKey },
      toJSON: () => ({
        endpoint: 'https://push.example.test/sub-old',
        keys: { p256dh: 'p256dh-old', auth: 'auth-old' },
      }),
      unsubscribe: vi.fn(async () => {
        activeSubscription = null;
        return true;
      }),
    };

    replacementSubscription = {
      endpoint: 'https://push.example.test/sub-new',
      options: { applicationServerKey: new Uint8Array(vapidNewBytes).buffer },
      toJSON: () => ({
        endpoint: 'https://push.example.test/sub-new',
        keys: { p256dh: 'p256dh-new', auth: 'auth-new' },
      }),
      unsubscribe: vi.fn(async () => true),
    };

    getSubscriptionMock = vi.fn(async () => activeSubscription);
    subscribeMock = vi.fn(async () => {
      activeSubscription = replacementSubscription;
      return replacementSubscription;
    });

    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    });
    Object.defineProperty(window, 'PushManager', {
      configurable: true,
      value: function PushManager() {},
    });
    Object.defineProperty(window, 'Notification', {
      configurable: true,
      value: function Notification() {},
    });
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        ready: Promise.resolve({
          pushManager: {
            getSubscription: getSubscriptionMock,
            subscribe: subscribeMock,
          },
        }),
      },
    });

    mocks.api.getPushConversations.mockResolvedValue([]);
    mocks.api.getPushSubscriptions.mockResolvedValue([
      {
        id: 'sub-1',
        endpoint: 'https://push.example.test/sub-old',
        p256dh: 'p256dh-old',
        auth: 'auth-old',
        label: 'Chrome on macOS',
        created_at: 1,
        last_success_at: null,
        failure_count: 0,
      },
    ]);
    mocks.api.getVapidPublicKey.mockResolvedValue({ public_key: newKeyBase64 });
    mocks.api.pushSubscribe.mockResolvedValue({
      id: 'sub-2',
      endpoint: 'https://push.example.test/sub-new',
    });
  });

  it('clears currentSubscriptionId when refresh no longer finds this browser on the backend', async () => {
    const { result } = renderHook(() => usePushSubscription());

    await waitFor(() => {
      expect(result.current.currentSubscriptionId).toBe('sub-1');
      expect(result.current.isSubscribed).toBe(true);
    });

    mocks.api.getPushSubscriptions.mockResolvedValueOnce([]);

    await act(async () => {
      await result.current.refreshSubscriptions();
    });

    expect(result.current.currentSubscriptionId).toBeNull();
    expect(result.current.isSubscribed).toBe(false);
    expect(result.current.allSubscriptions).toEqual([]);
  });

  it('times out and shows a toast when service worker never activates', async () => {
    // Replace serviceWorker.ready with a promise that never resolves
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        ready: new Promise(() => {}),
      },
    });

    const { result } = renderHook(() => usePushSubscription());

    await waitFor(() => {
      expect(result.current.isSupported).toBe(true);
    });

    // subscribe() will hang on serviceWorker.ready, then the 1s timeout fires
    await act(async () => {
      await result.current.subscribe();
    });

    expect(result.current.loading).toBe(false);
    expect(mocks.toast.error).toHaveBeenCalledWith(
      'Failed to enable push notifications',
      expect.objectContaining({
        description: expect.stringContaining('trusted TLS certificate for service workers'),
      })
    );
  }, 5_000);

  it('recreates a stale browser subscription when the server VAPID key changed', async () => {
    const oldSubscription = activeSubscription;
    mocks.api.getPushSubscriptions
      .mockReset()
      .mockResolvedValueOnce([
        {
          id: 'sub-1',
          endpoint: 'https://push.example.test/sub-old',
          p256dh: 'p256dh-old',
          auth: 'auth-old',
          label: 'Chrome on macOS',
          created_at: 1,
          last_success_at: null,
          failure_count: 0,
        },
      ])
      .mockResolvedValueOnce([
        {
          id: 'sub-2',
          endpoint: 'https://push.example.test/sub-new',
          p256dh: 'p256dh-new',
          auth: 'auth-new',
          label: 'Chrome on macOS',
          created_at: 2,
          last_success_at: null,
          failure_count: 0,
        },
      ]);

    const { result } = renderHook(() => usePushSubscription());

    await waitFor(() => {
      expect(result.current.isSupported).toBe(true);
    });

    await act(async () => {
      await result.current.subscribe();
    });

    expect(oldSubscription?.unsubscribe).toHaveBeenCalledTimes(1);
    expect(activeSubscription).toBe(replacementSubscription);
    expect(subscribeMock).toHaveBeenCalledTimes(1);
    expect(mocks.api.pushSubscribe).toHaveBeenCalledWith({
      endpoint: 'https://push.example.test/sub-new',
      p256dh: 'p256dh-new',
      auth: 'auth-new',
      label: expect.any(String),
    });
    expect(result.current.currentSubscriptionId).toBe('sub-2');
  });
});
