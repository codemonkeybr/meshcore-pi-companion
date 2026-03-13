import {
  useCallback,
  useMemo,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from 'react';
import { api } from '../api';
import * as messageCache from '../messageCache';
import type { UseWebSocketOptions } from '../useWebSocket';
import { toast } from '../components/ui/sonner';
import { getStateKey } from '../utils/conversationState';
import { mergeContactIntoList } from '../utils/contactMerge';
import { getContactDisplayName } from '../utils/pubkey';
import { appendRawPacketUnique } from '../utils/rawPacketIdentity';
import { getMessageContentKey } from './useConversationMessages';
import type {
  Channel,
  Contact,
  Conversation,
  HealthStatus,
  Message,
  MessagePath,
  RawPacket,
} from '../types';

interface UseRealtimeAppStateArgs {
  prevHealthRef: MutableRefObject<HealthStatus | null>;
  setHealth: Dispatch<SetStateAction<HealthStatus | null>>;
  fetchConfig: () => void | Promise<void>;
  setRawPackets: Dispatch<SetStateAction<RawPacket[]>>;
  triggerReconcile: () => void;
  refreshUnreads: () => Promise<void>;
  setChannels: Dispatch<SetStateAction<Channel[]>>;
  fetchAllContacts: () => Promise<Contact[]>;
  setContacts: Dispatch<SetStateAction<Contact[]>>;
  blockedKeysRef: MutableRefObject<string[]>;
  blockedNamesRef: MutableRefObject<string[]>;
  activeConversationRef: MutableRefObject<Conversation | null>;
  hasNewerMessagesRef: MutableRefObject<boolean>;
  addMessageIfNew: (msg: Message) => boolean;
  trackNewMessage: (msg: Message) => void;
  incrementUnread: (stateKey: string, hasMention?: boolean) => void;
  renameConversationState: (oldStateKey: string, newStateKey: string) => void;
  checkMention: (text: string) => boolean;
  pendingDeleteFallbackRef: MutableRefObject<boolean>;
  setActiveConversation: (conv: Conversation | null) => void;
  updateMessageAck: (messageId: number, ackCount: number, paths?: MessagePath[]) => void;
  notifyIncomingMessage?: (msg: Message) => void;
  maxRawPackets?: number;
}

function isMessageBlocked(msg: Message, blockedKeys: string[], blockedNames: string[]): boolean {
  if (msg.outgoing) {
    return false;
  }

  if (blockedKeys.length > 0) {
    if (msg.type === 'PRIV' && blockedKeys.includes(msg.conversation_key.toLowerCase())) {
      return true;
    }
    if (
      msg.type === 'CHAN' &&
      msg.sender_key &&
      blockedKeys.includes(msg.sender_key.toLowerCase())
    ) {
      return true;
    }
  }

  return blockedNames.length > 0 && !!msg.sender_name && blockedNames.includes(msg.sender_name);
}

function isActiveConversationMessage(
  activeConversation: Conversation | null,
  msg: Message
): boolean {
  if (!activeConversation) return false;
  if (msg.type === 'CHAN' && activeConversation.type === 'channel') {
    return msg.conversation_key === activeConversation.id;
  }
  if (msg.type === 'PRIV' && activeConversation.type === 'contact') {
    return msg.conversation_key === activeConversation.id;
  }
  return false;
}

export function useRealtimeAppState({
  prevHealthRef,
  setHealth,
  fetchConfig,
  setRawPackets,
  triggerReconcile,
  refreshUnreads,
  setChannels,
  fetchAllContacts,
  setContacts,
  blockedKeysRef,
  blockedNamesRef,
  activeConversationRef,
  hasNewerMessagesRef,
  addMessageIfNew,
  trackNewMessage,
  incrementUnread,
  renameConversationState,
  checkMention,
  pendingDeleteFallbackRef,
  setActiveConversation,
  updateMessageAck,
  notifyIncomingMessage,
  maxRawPackets = 500,
}: UseRealtimeAppStateArgs): UseWebSocketOptions {
  const mergeChannelIntoList = useCallback(
    (updated: Channel) => {
      setChannels((prev) => {
        const existingIndex = prev.findIndex((channel) => channel.key === updated.key);
        if (existingIndex === -1) {
          return [...prev, updated].sort((a, b) => a.name.localeCompare(b.name));
        }
        const next = [...prev];
        next[existingIndex] = updated;
        return next;
      });
    },
    [setChannels]
  );

  return useMemo(
    () => ({
      onHealth: (data: HealthStatus) => {
        const prev = prevHealthRef.current;
        prevHealthRef.current = data;
        setHealth(data);
        const nextRadioState =
          data.radio_state ??
          (data.radio_initializing
            ? 'initializing'
            : data.radio_connected
              ? 'connected'
              : 'disconnected');
        const initializationCompleted =
          prev !== null &&
          prev.radio_connected &&
          prev.radio_initializing &&
          data.radio_connected &&
          !data.radio_initializing;

        if (prev !== null && prev.radio_connected !== data.radio_connected) {
          if (data.radio_connected) {
            toast.success('Radio connected', {
              description: data.connection_info
                ? `Connected via ${data.connection_info}`
                : undefined,
            });
            fetchConfig();
          } else {
            if (nextRadioState === 'paused') {
              toast.success('Radio connection paused');
            } else {
              toast.error('Radio disconnected', {
                description: 'Check radio connection and power',
              });
            }
          }
        }

        if (initializationCompleted) {
          fetchConfig();
        }
      },
      onError: (error: { message: string; details?: string }) => {
        toast.error(error.message, {
          description: error.details,
        });
      },
      onSuccess: (success: { message: string; details?: string }) => {
        toast.success(success.message, {
          description: success.details,
        });
      },
      onReconnect: () => {
        setRawPackets([]);
        triggerReconcile();
        refreshUnreads();
        api.getChannels().then(setChannels).catch(console.error);
        fetchAllContacts()
          .then((data) => setContacts(data))
          .catch(console.error);
      },
      onMessage: (msg: Message) => {
        if (isMessageBlocked(msg, blockedKeysRef.current, blockedNamesRef.current)) {
          return;
        }

        const isForActiveConversation = isActiveConversationMessage(
          activeConversationRef.current,
          msg
        );
        let isNewMessage = false;

        if (isForActiveConversation && !hasNewerMessagesRef.current) {
          isNewMessage = addMessageIfNew(msg);
        }

        trackNewMessage(msg);

        const contentKey = getMessageContentKey(msg);
        if (!isForActiveConversation) {
          isNewMessage = messageCache.addMessage(msg.conversation_key, msg, contentKey);

          if (!msg.outgoing && isNewMessage) {
            let stateKey: string | null = null;
            if (msg.type === 'CHAN' && msg.conversation_key) {
              stateKey = getStateKey('channel', msg.conversation_key);
            } else if (msg.type === 'PRIV' && msg.conversation_key) {
              stateKey = getStateKey('contact', msg.conversation_key);
            }
            if (stateKey) {
              incrementUnread(stateKey, checkMention(msg.text));
            }
          }
        }

        if (!msg.outgoing && isNewMessage) {
          notifyIncomingMessage?.(msg);
        }
      },
      onContact: (contact: Contact) => {
        setContacts((prev) => mergeContactIntoList(prev, contact));
      },
      onContactResolved: (previousPublicKey: string, contact: Contact) => {
        setContacts((prev) =>
          mergeContactIntoList(
            prev.filter((candidate) => candidate.public_key !== previousPublicKey),
            contact
          )
        );
        messageCache.rename(previousPublicKey, contact.public_key);
        renameConversationState(
          getStateKey('contact', previousPublicKey),
          getStateKey('contact', contact.public_key)
        );

        const active = activeConversationRef.current;
        if (active?.type === 'contact' && active.id === previousPublicKey) {
          setActiveConversation({
            type: 'contact',
            id: contact.public_key,
            name: getContactDisplayName(contact.name, contact.public_key, contact.last_advert),
          });
        }
      },
      onChannel: (channel: Channel) => {
        mergeChannelIntoList(channel);
      },
      onContactDeleted: (publicKey: string) => {
        setContacts((prev) => prev.filter((c) => c.public_key !== publicKey));
        messageCache.remove(publicKey);
        const active = activeConversationRef.current;
        if (active?.type === 'contact' && active.id === publicKey) {
          pendingDeleteFallbackRef.current = true;
          setActiveConversation(null);
        }
      },
      onChannelDeleted: (key: string) => {
        setChannels((prev) => prev.filter((c) => c.key !== key));
        messageCache.remove(key);
        const active = activeConversationRef.current;
        if (active?.type === 'channel' && active.id === key) {
          pendingDeleteFallbackRef.current = true;
          setActiveConversation(null);
        }
      },
      onRawPacket: (packet: RawPacket) => {
        setRawPackets((prev) => appendRawPacketUnique(prev, packet, maxRawPackets));
      },
      onMessageAcked: (messageId: number, ackCount: number, paths?: MessagePath[]) => {
        updateMessageAck(messageId, ackCount, paths);
        messageCache.updateAck(messageId, ackCount, paths);
      },
    }),
    [
      activeConversationRef,
      addMessageIfNew,
      blockedKeysRef,
      blockedNamesRef,
      checkMention,
      fetchAllContacts,
      fetchConfig,
      hasNewerMessagesRef,
      incrementUnread,
      renameConversationState,
      maxRawPackets,
      mergeChannelIntoList,
      pendingDeleteFallbackRef,
      prevHealthRef,
      refreshUnreads,
      setActiveConversation,
      setChannels,
      setContacts,
      setHealth,
      setRawPackets,
      trackNewMessage,
      triggerReconcile,
      updateMessageAck,
      notifyIncomingMessage,
    ]
  );
}
