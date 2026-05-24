import { useState, useEffect, useCallback, useRef } from 'react';
import { toast } from '../components/ui/sonner';
import { api } from '../api';
import type { PushSubscriptionInfo } from '../types';

function generateLabel(): string {
  const ua = navigator.userAgent;
  if (/Firefox/i.test(ua)) {
    if (/Android/i.test(ua)) return 'Firefox on Android';
    if (/Mac/i.test(ua)) return 'Firefox on macOS';
    if (/Windows/i.test(ua)) return 'Firefox on Windows';
    if (/Linux/i.test(ua)) return 'Firefox on Linux';
    return 'Firefox';
  }
  if (/Chrome/i.test(ua) && !/Edg/i.test(ua)) {
    if (/Android/i.test(ua)) return 'Chrome on Android';
    if (/CrOS/i.test(ua)) return 'Chrome on ChromeOS';
    if (/Mac/i.test(ua)) return 'Chrome on macOS';
    if (/Windows/i.test(ua)) return 'Chrome on Windows';
    if (/Linux/i.test(ua)) return 'Chrome on Linux';
    return 'Chrome';
  }
  if (/Edg/i.test(ua)) return 'Edge';
  if (/Safari/i.test(ua)) {
    if (/iPhone|iPad/i.test(ua)) return 'Safari on iOS';
    return 'Safari on macOS';
  }
  return 'Browser';
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}

/** Race a promise against a timeout; rejects with a descriptive error on expiry. */
function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () =>
        reject(
          new Error(
            `${label} timed out — the service worker may have failed to install. ` +
              'Mobile browsers require a trusted TLS certificate for service workers, ' +
              'even if the page itself loads with a self-signed cert.'
          )
        ),
      ms
    );
    promise.then(
      (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      (e) => {
        clearTimeout(timer);
        reject(e);
      }
    );
  });
}

function uint8ArraysEqual(a: Uint8Array | null, b: Uint8Array): boolean {
  if (!a || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

function getApplicationServerKeyBytes(
  key: ArrayBuffer | ArrayBufferView | null | undefined
): Uint8Array | null {
  if (!key) return null;
  if (ArrayBuffer.isView(key)) {
    return new Uint8Array(key.buffer, key.byteOffset, key.byteLength);
  }
  return new Uint8Array(key);
}

export interface PushSubscriptionState {
  isSupported: boolean;
  isSubscribed: boolean;
  currentSubscriptionId: string | null;
  allSubscriptions: PushSubscriptionInfo[];
  /** Global list of push-enabled conversation state keys (device-independent). */
  pushConversations: string[];
  loading: boolean;
  subscribe: () => Promise<string | null>;
  unsubscribe: () => Promise<void>;
  /** Toggle a conversation in the global push list (device-independent). */
  toggleConversation: (conversationKey: string) => Promise<void>;
  isConversationPushEnabled: (conversationKey: string) => boolean;
  deleteSubscription: (subscriptionId: string) => Promise<void>;
  testPush: (subscriptionId: string) => Promise<void>;
  refreshSubscriptions: () => Promise<PushSubscriptionInfo[]>;
  refreshConversations: () => Promise<void>;
}

export function usePushSubscription(): PushSubscriptionState {
  const [isSupported, setIsSupported] = useState(false);
  const [currentSubscriptionId, setCurrentSubscriptionId] = useState<string | null>(null);
  const [allSubscriptions, setAllSubscriptions] = useState<PushSubscriptionInfo[]>([]);
  const [pushConversations, setPushConversations] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const vapidKeyRef = useRef<string | null>(null);

  const reconcileCurrentSubscription = useCallback(
    (subs: PushSubscriptionInfo[], endpoint: string | null) => {
      setAllSubscriptions(subs);
      if (!endpoint) {
        setCurrentSubscriptionId(null);
        return;
      }
      const match = subs.find((sub) => sub.endpoint === endpoint);
      setCurrentSubscriptionId(match?.id ?? null);
    },
    []
  );

  useEffect(() => {
    const supported =
      window.isSecureContext &&
      'serviceWorker' in navigator &&
      'PushManager' in window &&
      'Notification' in window;
    setIsSupported(supported);

    if (supported) {
      // Always load all registered devices so Settings can manage them even
      // when this particular browser isn't subscribed.
      const subsPromise = api.getPushSubscriptions().catch(() => [] as PushSubscriptionInfo[]);

      // Check if THIS browser has an active push subscription and match it
      // to a backend record.  Use a timeout so we don't hang forever when the
      // service worker failed to install (e.g. mobile + self-signed cert).
      withTimeout(navigator.serviceWorker.ready, 1_000, 'Service worker activation')
        .then((reg) => reg.pushManager.getSubscription())
        .then(async (sub) => {
          const existing = await subsPromise;
          reconcileCurrentSubscription(existing, sub?.endpoint ?? null);
        })
        .catch(() => {});

      // Load global conversation list
      api
        .getPushConversations()
        .then(setPushConversations)
        .catch(() => {});
    }
  }, [reconcileCurrentSubscription]);

  const refreshSubscriptions = useCallback(async () => {
    try {
      const subs = await api.getPushSubscriptions();
      const reg = await withTimeout(
        navigator.serviceWorker.ready,
        10_000,
        'Service worker activation'
      );
      const sub = await reg.pushManager.getSubscription();
      reconcileCurrentSubscription(subs, sub?.endpoint ?? null);
      return subs;
    } catch {
      return [];
    }
  }, [reconcileCurrentSubscription]);

  const refreshConversations = useCallback(async () => {
    try {
      const convos = await api.getPushConversations();
      setPushConversations(convos);
    } catch {
      // best effort
    }
  }, []);

  const subscribe = useCallback(async (): Promise<string | null> => {
    if (!isSupported) return null;
    setLoading(true);
    try {
      const resp = await api.getVapidPublicKey();
      vapidKeyRef.current = resp.public_key;
      const vapidKeyBytes = urlBase64ToUint8Array(resp.public_key);

      const reg = await withTimeout(
        navigator.serviceWorker.ready,
        3_000,
        'Service worker activation'
      );
      let pushSub = await reg.pushManager.getSubscription();
      const existingKeyBytes = getApplicationServerKeyBytes(pushSub?.options?.applicationServerKey);
      const requiresRecreate =
        pushSub !== null && !uint8ArraysEqual(existingKeyBytes, vapidKeyBytes);

      if (requiresRecreate) {
        await pushSub!.unsubscribe();
        pushSub = null;
      }

      if (!pushSub) {
        pushSub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: vapidKeyBytes.buffer as ArrayBuffer,
        });
      }

      const json = pushSub.toJSON();
      const result = await api.pushSubscribe({
        endpoint: json.endpoint!,
        p256dh: json.keys!.p256dh!,
        auth: json.keys!.auth!,
        label: generateLabel(),
      });

      setCurrentSubscriptionId(result.id);
      await refreshSubscriptions();
      return result.id;
    } catch (err) {
      console.error('Push subscribe failed:', err);
      toast.error('Failed to enable push notifications', {
        description: err instanceof Error ? err.message : 'Check that notifications are allowed',
        duration: 8_000,
      });
      return null;
    } finally {
      setLoading(false);
    }
  }, [isSupported, refreshSubscriptions]);

  const unsubscribe = useCallback(async () => {
    setLoading(true);
    try {
      const reg = await navigator.serviceWorker.ready;
      const pushSub = await reg.pushManager.getSubscription();
      if (pushSub) await pushSub.unsubscribe();

      if (currentSubscriptionId) {
        await api.deletePushSubscription(currentSubscriptionId).catch(() => {});
      }

      setCurrentSubscriptionId(null);
      await refreshSubscriptions();
    } catch (err) {
      console.error('Push unsubscribe failed:', err);
    } finally {
      setLoading(false);
    }
  }, [currentSubscriptionId, refreshSubscriptions]);

  const toggleConversation = useCallback(async (conversationKey: string) => {
    try {
      const updated = await api.togglePushConversation(conversationKey);
      setPushConversations(updated);
    } catch {
      toast.error('Failed to update push preferences');
    }
  }, []);

  const isConversationPushEnabled = useCallback(
    (conversationKey: string): boolean => {
      return pushConversations.includes(conversationKey);
    },
    [pushConversations]
  );

  const deleteSubscription = useCallback(
    async (subscriptionId: string) => {
      await api.deletePushSubscription(subscriptionId);
      if (subscriptionId === currentSubscriptionId) {
        setCurrentSubscriptionId(null);
        try {
          const reg = await navigator.serviceWorker.ready;
          const pushSub = await reg.pushManager.getSubscription();
          if (pushSub) await pushSub.unsubscribe();
        } catch {
          // best effort
        }
      }
      await refreshSubscriptions();
    },
    [currentSubscriptionId, refreshSubscriptions]
  );

  const testPush = useCallback(async (subscriptionId: string) => {
    try {
      await api.testPushSubscription(subscriptionId);
      toast.success('Test notification sent');
    } catch {
      toast.error('Test notification failed');
    }
  }, []);

  return {
    isSupported,
    isSubscribed: !!currentSubscriptionId,
    currentSubscriptionId,
    allSubscriptions,
    pushConversations,
    loading,
    subscribe,
    unsubscribe,
    toggleConversation,
    isConversationPushEnabled,
    deleteSubscription,
    testPush,
    refreshSubscriptions,
    refreshConversations,
  };
}
