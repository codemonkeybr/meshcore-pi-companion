import { useCallback, type MutableRefObject, type RefObject } from 'react';
import { api } from '../api';
import * as messageCache from '../messageCache';
import { toast } from '../components/ui/sonner';
import type { MessageInputHandle } from '../components/MessageInput';
import type { Channel, Conversation, Message } from '../types';

interface UseConversationActionsArgs {
  activeConversation: Conversation | null;
  activeConversationRef: MutableRefObject<Conversation | null>;
  setChannels: React.Dispatch<React.SetStateAction<Channel[]>>;
  addMessageIfNew: (msg: Message) => boolean;
  jumpToBottom: () => void;
  handleToggleBlockedKey: (key: string) => Promise<void>;
  handleToggleBlockedName: (name: string) => Promise<void>;
  messageInputRef: RefObject<MessageInputHandle | null>;
}

interface UseConversationActionsResult {
  handleSendMessage: (text: string) => Promise<void>;
  handleResendChannelMessage: (messageId: number, newTimestamp?: boolean) => Promise<void>;
  handleSetChannelFloodScopeOverride: (
    channelKey: string,
    floodScopeOverride: string
  ) => Promise<void>;
  handleSenderClick: (sender: string) => void;
  handleTrace: () => Promise<void>;
  handleBlockKey: (key: string) => Promise<void>;
  handleBlockName: (name: string) => Promise<void>;
}

export function useConversationActions({
  activeConversation,
  activeConversationRef,
  setChannels,
  addMessageIfNew,
  jumpToBottom,
  handleToggleBlockedKey,
  handleToggleBlockedName,
  messageInputRef,
}: UseConversationActionsArgs): UseConversationActionsResult {
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

  const handleSendMessage = useCallback(
    async (text: string) => {
      if (!activeConversation) return;

      const conversationId = activeConversation.id;
      const sent =
        activeConversation.type === 'channel'
          ? await api.sendChannelMessage(activeConversation.id, text)
          : await api.sendDirectMessage(activeConversation.id, text);

      if (activeConversationRef.current?.id === conversationId) {
        addMessageIfNew(sent);
      }
    },
    [activeConversation, activeConversationRef, addMessageIfNew]
  );

  const handleResendChannelMessage = useCallback(
    async (messageId: number, newTimestamp?: boolean) => {
      try {
        await api.resendChannelMessage(messageId, newTimestamp);
        toast.success(newTimestamp ? 'Message resent with new timestamp' : 'Message resent');
      } catch (err) {
        toast.error('Failed to resend', {
          description: err instanceof Error ? err.message : 'Unknown error',
        });
      }
    },
    []
  );

  const handleSetChannelFloodScopeOverride = useCallback(
    async (channelKey: string, floodScopeOverride: string) => {
      try {
        const updated = await api.setChannelFloodScopeOverride(channelKey, floodScopeOverride);
        mergeChannelIntoList(updated);
        toast.success(
          updated.flood_scope_override ? 'Regional override saved' : 'Regional override cleared'
        );
      } catch (err) {
        toast.error('Failed to update regional override', {
          description: err instanceof Error ? err.message : 'Unknown error',
        });
      }
    },
    [mergeChannelIntoList]
  );

  const handleSenderClick = useCallback(
    (sender: string) => {
      messageInputRef.current?.appendText(`@[${sender}] `);
    },
    [messageInputRef]
  );

  const handleTrace = useCallback(async () => {
    if (!activeConversation || activeConversation.type !== 'contact') return;
    toast('Trace started...');
    try {
      const result = await api.requestTrace(activeConversation.id);
      const parts: string[] = [];
      if (result.remote_snr !== null) parts.push(`Remote SNR: ${result.remote_snr.toFixed(1)} dB`);
      if (result.local_snr !== null) parts.push(`Local SNR: ${result.local_snr.toFixed(1)} dB`);
      const detail = parts.join(', ');
      toast.success(detail ? `Trace complete! ${detail}` : 'Trace complete!');
    } catch (err) {
      toast.error('Trace failed', {
        description: err instanceof Error ? err.message : 'Unknown error',
      });
    }
  }, [activeConversation]);

  const handleBlockKey = useCallback(
    async (key: string) => {
      await handleToggleBlockedKey(key);
      messageCache.clear();
      jumpToBottom();
    },
    [handleToggleBlockedKey, jumpToBottom]
  );

  const handleBlockName = useCallback(
    async (name: string) => {
      await handleToggleBlockedName(name);
      messageCache.clear();
      jumpToBottom();
    },
    [handleToggleBlockedName, jumpToBottom]
  );

  return {
    handleSendMessage,
    handleResendChannelMessage,
    handleSetChannelFloodScopeOverride,
    handleSenderClick,
    handleTrace,
    handleBlockKey,
    handleBlockName,
  };
}
