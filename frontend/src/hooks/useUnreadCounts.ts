import { useState, useCallback, useEffect, useRef } from 'react';
import { api } from '../api';
import {
  getLastMessageTimes,
  setLastMessageTime,
  renameConversationTimeKey,
  getStateKey,
  type ConversationTimes,
} from '../utils/conversationState';
import type { Channel, Contact, Conversation, Message, UnreadCounts } from '../types';
import { takePrefetchOrFetch } from '../prefetch';

interface UseUnreadCountsResult {
  unreadCounts: Record<string, number>;
  /** Tracks which conversations have unread messages that mention the user */
  mentions: Record<string, boolean>;
  lastMessageTimes: ConversationTimes;
  unreadLastReadAts: Record<string, number | null>;
  incrementUnread: (stateKey: string, hasMention?: boolean) => void;
  renameConversationState: (oldStateKey: string, newStateKey: string) => void;
  markAllRead: () => void;
  trackNewMessage: (msg: Message) => void;
  refreshUnreads: () => Promise<void>;
}

export function useUnreadCounts(
  channels: Channel[],
  contacts: Contact[],
  activeConversation: Conversation | null
): UseUnreadCountsResult {
  const [unreadCounts, setUnreadCounts] = useState<Record<string, number>>({});
  const [mentions, setMentions] = useState<Record<string, boolean>>({});
  const [lastMessageTimes, setLastMessageTimes] = useState<ConversationTimes>(getLastMessageTimes);
  const [unreadLastReadAts, setUnreadLastReadAts] = useState<Record<string, number | null>>({});

  // Track active conversation via ref so applyUnreads can filter without
  // destabilizing the callback chain (avoids re-creating fetchUnreads on
  // every conversation switch).
  const activeConvRef = useRef(activeConversation);
  activeConvRef.current = activeConversation;

  // Apply unreads data to state, filtering out the active conversation
  // (the user is already viewing it, so its count should stay at 0).
  const applyUnreads = useCallback((data: UnreadCounts) => {
    const ac = activeConvRef.current;
    const activeKey =
      ac &&
      ac.type !== 'raw' &&
      ac.type !== 'map' &&
      ac.type !== 'visualizer' &&
      ac.type !== 'search'
        ? getStateKey(ac.type as 'channel' | 'contact', ac.id)
        : null;

    if (activeKey) {
      const counts = { ...data.counts };
      const mentionsData = { ...data.mentions };
      delete counts[activeKey];
      delete mentionsData[activeKey];
      setUnreadCounts(counts);
      setMentions(mentionsData);
    } else {
      setUnreadCounts(data.counts);
      setMentions(data.mentions);
    }

    setUnreadLastReadAts(data.last_read_ats);

    if (Object.keys(data.last_message_times).length > 0) {
      for (const [key, ts] of Object.entries(data.last_message_times)) {
        setLastMessageTime(key, ts);
      }
      setLastMessageTimes(getLastMessageTimes());
    }
  }, []);

  // Fetch unreads from the server-side endpoint.
  // Also re-marks the active conversation as read so the server's last_read_at
  // stays current (otherwise subsequent fetches would re-report the same unreads).
  const fetchUnreads = useCallback(async () => {
    try {
      applyUnreads(await api.getUnreads());
    } catch (err) {
      console.error('Failed to fetch unreads:', err);
    }
    const ac = activeConvRef.current;
    if (ac?.type === 'channel') {
      api.markChannelRead(ac.id).catch(() => {});
    } else if (ac?.type === 'contact') {
      api.markContactRead(ac.id).catch(() => {});
    }
  }, [applyUnreads]);

  // On mount, consume the prefetched promise (started in index.html before
  // React loaded) or fall back to a fresh fetch.
  // Re-fetch when channel/contact count changes mid-session (new sync, cracker
  // channel created, etc.). Skip only the very first run of this effect; after
  // that, any count change should trigger a refresh, even if the other
  // collection is still empty.
  const channelsLen = channels.length;
  const contactsLen = contacts.length;
  const hasObservedCountsRef = useRef(false);
  useEffect(() => {
    takePrefetchOrFetch('unreads', api.getUnreads)
      .then(applyUnreads)
      .catch((err) => {
        console.error('Failed to fetch unreads:', err);
      });
  }, [applyUnreads]);
  useEffect(() => {
    if (!hasObservedCountsRef.current) {
      hasObservedCountsRef.current = true;
      return;
    }
    fetchUnreads();
  }, [channelsLen, contactsLen, fetchUnreads]);

  // Mark conversation as read when user views it
  // Calls server API to persist read state across devices
  useEffect(() => {
    if (
      activeConversation &&
      activeConversation.type !== 'raw' &&
      activeConversation.type !== 'map' &&
      activeConversation.type !== 'visualizer'
    ) {
      const key = getStateKey(
        activeConversation.type as 'channel' | 'contact',
        activeConversation.id
      );

      // Update local state immediately for responsive UI
      setUnreadCounts((prev) => {
        if (prev[key]) {
          const next = { ...prev };
          delete next[key];
          return next;
        }
        return prev;
      });

      // Also clear mentions for this conversation
      setMentions((prev) => {
        if (prev[key]) {
          const next = { ...prev };
          delete next[key];
          return next;
        }
        return prev;
      });

      // Persist to server (fire-and-forget, errors logged but not blocking)
      if (activeConversation.type === 'channel') {
        api.markChannelRead(activeConversation.id).catch((err) => {
          console.error('Failed to mark channel as read on server:', err);
        });
      } else if (activeConversation.type === 'contact') {
        api.markContactRead(activeConversation.id).catch((err) => {
          console.error('Failed to mark contact as read on server:', err);
        });
      }
    }
  }, [activeConversation]);

  // Increment unread count for a conversation
  const incrementUnread = useCallback((stateKey: string, hasMention?: boolean) => {
    setUnreadCounts((prev) => ({
      ...prev,
      [stateKey]: (prev[stateKey] || 0) + 1,
    }));
    if (hasMention) {
      setMentions((prev) => ({
        ...prev,
        [stateKey]: true,
      }));
    }
  }, []);

  const renameConversationState = useCallback((oldStateKey: string, newStateKey: string) => {
    if (oldStateKey === newStateKey) return;

    setUnreadCounts((prev) => {
      if (!(oldStateKey in prev)) return prev;
      const next = { ...prev };
      next[newStateKey] = (next[newStateKey] || 0) + next[oldStateKey];
      delete next[oldStateKey];
      return next;
    });

    setMentions((prev) => {
      if (!(oldStateKey in prev)) return prev;
      const next = { ...prev };
      next[newStateKey] = next[newStateKey] || next[oldStateKey];
      delete next[oldStateKey];
      return next;
    });

    setLastMessageTimes(renameConversationTimeKey(oldStateKey, newStateKey));
  }, []);

  // Mark all conversations as read
  // Calls single bulk API endpoint to persist read state
  const markAllRead = useCallback(() => {
    // Update local state immediately
    setUnreadCounts({});
    setMentions({});
    setUnreadLastReadAts({});

    // Persist to server with single bulk request
    api.markAllRead().catch((err) => {
      console.error('Failed to mark all as read on server:', err);
    });
  }, []);

  // Track a new incoming message for unread counts
  const trackNewMessage = useCallback((msg: Message) => {
    let conversationKey: string | null = null;
    if (msg.type === 'CHAN' && msg.conversation_key) {
      conversationKey = getStateKey('channel', msg.conversation_key);
    } else if (msg.type === 'PRIV' && msg.conversation_key) {
      conversationKey = getStateKey('contact', msg.conversation_key);
    }

    if (conversationKey) {
      const timestamp = msg.received_at || Math.floor(Date.now() / 1000);
      const updated = setLastMessageTime(conversationKey, timestamp);
      setLastMessageTimes(updated);
    }
  }, []);

  return {
    unreadCounts,
    mentions,
    lastMessageTimes,
    unreadLastReadAts,
    incrementUnread,
    renameConversationState,
    markAllRead,
    trackNewMessage,
    refreshUnreads: fetchUnreads,
  };
}
