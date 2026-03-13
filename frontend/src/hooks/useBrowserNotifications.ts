import { useCallback, useEffect, useState } from 'react';
import { toast } from '../components/ui/sonner';
import type { Message } from '../types';
import { getStateKey } from '../utils/conversationState';

const STORAGE_KEY = 'meshcore_browser_notifications_enabled_by_conversation';
const NOTIFICATION_ICON_PATH = '/favicon-256x256.png';

type NotificationPermissionState = NotificationPermission | 'unsupported';
type ConversationNotificationMap = Record<string, boolean>;

function getConversationNotificationKey(type: 'channel' | 'contact', id: string): string {
  return getStateKey(type, id);
}

function readStoredEnabledMap(): ConversationNotificationMap {
  if (typeof window === 'undefined') {
    return {};
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== 'object') {
      return {};
    }
    return Object.fromEntries(
      Object.entries(parsed).filter(([key, value]) => typeof key === 'string' && value === true)
    );
  } catch {
    return {};
  }
}

function writeStoredEnabledMap(enabledByConversation: ConversationNotificationMap) {
  if (typeof window === 'undefined') {
    return;
  }
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(enabledByConversation));
}

function getInitialPermission(): NotificationPermissionState {
  if (typeof window === 'undefined' || !('Notification' in window)) {
    return 'unsupported';
  }
  return window.Notification.permission;
}

function shouldShowDesktopNotification(): boolean {
  if (typeof document === 'undefined') {
    return false;
  }
  return document.visibilityState !== 'visible' || !document.hasFocus();
}

function getMessageConversationNotificationKey(message: Message): string | null {
  if (message.type === 'PRIV' && message.conversation_key) {
    return getConversationNotificationKey('contact', message.conversation_key);
  }
  if (message.type === 'CHAN' && message.conversation_key) {
    return getConversationNotificationKey('channel', message.conversation_key);
  }
  return null;
}

function buildNotificationTitle(message: Message): string {
  if (message.type === 'PRIV') {
    return message.sender_name
      ? `New message from ${message.sender_name}`
      : `New message from ${message.conversation_key.slice(0, 12)}`;
  }

  const roomName = message.channel_name || message.conversation_key.slice(0, 8);
  return `New message in ${roomName}`;
}

function buildPreviewNotificationTitle(type: 'channel' | 'contact', label: string): string {
  return type === 'contact' ? `New message from ${label}` : `New message in ${label}`;
}

function buildMessageNotificationHash(message: Message): string | null {
  if (message.type === 'PRIV' && message.conversation_key) {
    const label = message.sender_name || message.conversation_key.slice(0, 12);
    return `#contact/${encodeURIComponent(message.conversation_key)}/${encodeURIComponent(label)}`;
  }
  if (message.type === 'CHAN' && message.conversation_key) {
    const label = message.channel_name || message.conversation_key.slice(0, 8);
    return `#channel/${encodeURIComponent(message.conversation_key)}/${encodeURIComponent(label)}`;
  }
  return null;
}

export function useBrowserNotifications() {
  const [permission, setPermission] = useState<NotificationPermissionState>(getInitialPermission);
  const [enabledByConversation, setEnabledByConversation] =
    useState<ConversationNotificationMap>(readStoredEnabledMap);

  useEffect(() => {
    setPermission(getInitialPermission());
  }, []);

  const isConversationNotificationsEnabled = useCallback(
    (type: 'channel' | 'contact', id: string) =>
      permission === 'granted' &&
      enabledByConversation[getConversationNotificationKey(type, id)] === true,
    [enabledByConversation, permission]
  );

  const toggleConversationNotifications = useCallback(
    async (type: 'channel' | 'contact', id: string, label: string) => {
      const conversationKey = getConversationNotificationKey(type, id);
      if (enabledByConversation[conversationKey]) {
        setEnabledByConversation((prev) => {
          const next = { ...prev };
          delete next[conversationKey];
          writeStoredEnabledMap(next);
          return next;
        });
        toast.success(`${label} notifications disabled`);
        return;
      }

      if (permission === 'unsupported') {
        toast.error('Browser notifications unavailable', {
          description: 'This browser does not support desktop notifications.',
        });
        return;
      }

      if (permission === 'denied') {
        toast.error('Browser notifications blocked', {
          description: 'Allow notifications in your browser settings, then try again.',
        });
        return;
      }

      const nextPermission = await window.Notification.requestPermission();
      setPermission(nextPermission);

      if (nextPermission === 'granted') {
        setEnabledByConversation((prev) => {
          const next = {
            ...prev,
            [conversationKey]: true,
          };
          writeStoredEnabledMap(next);
          return next;
        });
        new window.Notification(buildPreviewNotificationTitle(type, label), {
          body: 'Notifications will look like this. These require the tab to stay open, and will not be reliable on mobile.',
          icon: NOTIFICATION_ICON_PATH,
          tag: `meshcore-notification-preview-${conversationKey}`,
        });
        toast.success(`${label} notifications enabled`);
        return;
      }

      toast.error('Browser notifications not enabled', {
        description:
          nextPermission === 'denied'
            ? 'Permission was denied by the browser.'
            : 'Permission request was dismissed.',
      });
    },
    [enabledByConversation, permission]
  );

  const notifyIncomingMessage = useCallback(
    (message: Message) => {
      const conversationKey = getMessageConversationNotificationKey(message);
      if (
        permission !== 'granted' ||
        !conversationKey ||
        enabledByConversation[conversationKey] !== true ||
        !shouldShowDesktopNotification()
      ) {
        return;
      }

      const notification = new window.Notification(buildNotificationTitle(message), {
        body: message.text,
        icon: NOTIFICATION_ICON_PATH,
        tag: `meshcore-message-${message.id}`,
      });

      notification.onclick = () => {
        const hash = buildMessageNotificationHash(message);
        if (hash) {
          window.open(`${window.location.origin}${window.location.pathname}${hash}`, '_self');
        }
        window.focus();
        notification.close();
      };
    },
    [enabledByConversation, permission]
  );

  return {
    notificationsSupported: permission !== 'unsupported',
    notificationsPermission: permission,
    isConversationNotificationsEnabled,
    toggleConversationNotifications,
    notifyIncomingMessage,
  };
}
