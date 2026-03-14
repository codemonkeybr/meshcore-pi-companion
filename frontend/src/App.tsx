import { useEffect, useCallback, useRef, useState } from 'react';
import { api } from './api';
import { takePrefetchOrFetch } from './prefetch';
import { useWebSocket } from './useWebSocket';
import {
  useAppShell,
  useUnreadCounts,
  useConversationMessages,
  useRadioControl,
  useAppSettings,
  useConversationRouter,
  useContactsAndChannels,
  useConversationActions,
  useConversationNavigation,
  useRealtimeAppState,
  useBrowserNotifications,
} from './hooks';
import { AppShell } from './components/AppShell';
import type { MessageInputHandle } from './components/MessageInput';
import { messageContainsMention } from './utils/messageParser';
import { getStateKey } from './utils/conversationState';
import type { Conversation, Message, RawPacket } from './types';

interface ChannelUnreadMarker {
  channelId: string;
  lastReadAt: number | null;
}

interface UnreadBoundaryBackfillParams {
  activeConversation: Conversation | null;
  unreadMarker: ChannelUnreadMarker | null;
  messages: Message[];
  messagesLoading: boolean;
  loadingOlder: boolean;
  hasOlderMessages: boolean;
}

export function getUnreadBoundaryBackfillKey({
  activeConversation,
  unreadMarker,
  messages,
  messagesLoading,
  loadingOlder,
  hasOlderMessages,
}: UnreadBoundaryBackfillParams): string | null {
  if (activeConversation?.type !== 'channel') return null;
  if (!unreadMarker || unreadMarker.channelId !== activeConversation.id) return null;
  if (unreadMarker.lastReadAt === null) return null;
  if (messagesLoading || loadingOlder || !hasOlderMessages || messages.length === 0) return null;

  const oldestLoadedMessage = messages.reduce(
    (oldest, msg) => {
      if (!oldest) return msg;
      if (msg.received_at < oldest.received_at) return msg;
      if (msg.received_at === oldest.received_at && msg.id < oldest.id) return msg;
      return oldest;
    },
    null as Message | null
  );

  if (!oldestLoadedMessage) return null;
  if (oldestLoadedMessage.received_at <= unreadMarker.lastReadAt) return null;

  return `${activeConversation.id}:${unreadMarker.lastReadAt}:${oldestLoadedMessage.id}`;
}

export function App() {
  const quoteSearchOperatorValue = useCallback((value: string) => {
    return `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
  }, []);

  const messageInputRef = useRef<MessageInputHandle>(null);
  const [rawPackets, setRawPackets] = useState<RawPacket[]>([]);
  const [channelUnreadMarker, setChannelUnreadMarker] = useState<ChannelUnreadMarker | null>(null);
  const lastUnreadBackfillAttemptRef = useRef<string | null>(null);
  const {
    notificationsSupported,
    notificationsPermission,
    isConversationNotificationsEnabled,
    toggleConversationNotifications,
    notifyIncomingMessage,
  } = useBrowserNotifications();
  const {
    showNewMessage,
    showSettings,
    settingsSection,
    sidebarOpen,
    showCracker,
    crackerRunning,
    localLabel,
    setSettingsSection,
    setSidebarOpen,
    setCrackerRunning,
    setLocalLabel,
    handleCloseSettingsView,
    handleToggleSettingsView,
    handleOpenNewMessage,
    handleCloseNewMessage,
    handleToggleCracker,
  } = useAppShell();

  // Shared refs between useConversationRouter and useContactsAndChannels
  const pendingDeleteFallbackRef = useRef(false);
  const hasSetDefaultConversation = useRef(false);

  // Stable ref bridge: useContactsAndChannels needs setActiveConversation from
  // useConversationRouter, but useConversationRouter needs channels/contacts from
  // useContactsAndChannels. We break the cycle with a ref-based indirection.
  const setActiveConversationRef = useRef<(conv: Conversation | null) => void>(() => {});

  // --- Extracted hooks ---

  const {
    health,
    setHealth,
    config,
    prevHealthRef,
    fetchConfig,
    handleSaveConfig,
    handleSetPrivateKey,
    handleReboot,
    handleDisconnect,
    handleReconnect,
    handleAdvertise,
    handleHealthRefresh,
  } = useRadioControl();

  const {
    appSettings,
    favorites,
    fetchAppSettings,
    handleSaveAppSettings,
    handleSortOrderChange,
    handleToggleFavorite,
    handleToggleBlockedKey,
    handleToggleBlockedName,
  } = useAppSettings();

  // Keep user's name in ref for mention detection in WebSocket callback
  const myNameRef = useRef<string | null>(null);
  useEffect(() => {
    myNameRef.current = config?.name ?? null;
  }, [config?.name]);

  // Keep block lists in refs for WS callback filtering
  const blockedKeysRef = useRef<string[]>([]);
  const blockedNamesRef = useRef<string[]>([]);
  useEffect(() => {
    blockedKeysRef.current = appSettings?.blocked_keys ?? [];
    blockedNamesRef.current = appSettings?.blocked_names ?? [];
  }, [appSettings?.blocked_keys, appSettings?.blocked_names]);

  // Check if a message mentions the user
  const checkMention = useCallback(
    (text: string): boolean => messageContainsMention(text, myNameRef.current),
    []
  );

  // useContactsAndChannels is called first — it uses the ref bridge for setActiveConversation
  const {
    contacts,
    contactsLoaded,
    channels,
    undecryptedCount,
    setContacts,
    setContactsLoaded,
    setChannels,
    fetchAllContacts,
    fetchUndecryptedCount,
    handleCreateContact,
    handleCreateChannel,
    handleCreateHashtagChannel,
    handleDeleteChannel,
    handleDeleteContact,
  } = useContactsAndChannels({
    setActiveConversation: (conv) => setActiveConversationRef.current(conv),
    pendingDeleteFallbackRef,
    hasSetDefaultConversation,
  });

  // useConversationRouter is called second — it receives channels/contacts as inputs
  const {
    activeConversation,
    setActiveConversation,
    activeConversationRef,
    handleSelectConversation,
  } = useConversationRouter({
    channels,
    contacts,
    contactsLoaded,
    setSidebarOpen,
    pendingDeleteFallbackRef,
    hasSetDefaultConversation,
  });

  // Wire up the ref bridge so useContactsAndChannels handlers reach the real setter
  setActiveConversationRef.current = setActiveConversation;

  const {
    targetMessageId,
    setTargetMessageId,
    infoPaneContactKey,
    infoPaneFromChannel,
    infoPaneChannelKey,
    searchPrefillRequest,
    handleOpenContactInfo,
    handleCloseContactInfo,
    handleOpenChannelInfo,
    handleCloseChannelInfo,
    handleSelectConversationWithTargetReset,
    handleNavigateToChannel,
    handleNavigateToMessage,
    handleOpenSearchWithQuery,
  } = useConversationNavigation({
    channels,
    handleSelectConversation,
  });

  // Custom hooks for conversation-specific functionality
  const {
    messages,
    messagesLoading,
    loadingOlder,
    hasOlderMessages,
    hasNewerMessages,
    loadingNewer,
    hasNewerMessagesRef,
    fetchOlderMessages,
    fetchNewerMessages,
    jumpToBottom,
    addMessageIfNew,
    updateMessageAck,
    triggerReconcile,
  } = useConversationMessages(activeConversation, targetMessageId);

  const {
    unreadCounts,
    mentions,
    lastMessageTimes,
    unreadLastReadAts,
    incrementUnread,
    renameConversationState,
    markAllRead,
    trackNewMessage,
    refreshUnreads,
  } = useUnreadCounts(channels, contacts, activeConversation);

  useEffect(() => {
    if (activeConversation?.type !== 'channel') {
      setChannelUnreadMarker(null);
      return;
    }

    const activeChannelId = activeConversation.id;
    const activeChannelUnreadCount = unreadCounts[getStateKey('channel', activeChannelId)] ?? 0;

    setChannelUnreadMarker((prev) => {
      if (prev?.channelId === activeChannelId) {
        return prev;
      }
      if (activeChannelUnreadCount <= 0) {
        return null;
      }
      return {
        channelId: activeChannelId,
        lastReadAt: unreadLastReadAts[getStateKey('channel', activeChannelId)] ?? null,
      };
    });
  }, [activeConversation, unreadCounts, unreadLastReadAts]);

  useEffect(() => {
    lastUnreadBackfillAttemptRef.current = null;
  }, [activeConversation?.id, channelUnreadMarker?.channelId, channelUnreadMarker?.lastReadAt]);

  useEffect(() => {
    const backfillKey = getUnreadBoundaryBackfillKey({
      activeConversation,
      unreadMarker: channelUnreadMarker,
      messages,
      messagesLoading,
      loadingOlder,
      hasOlderMessages,
    });

    if (!backfillKey || lastUnreadBackfillAttemptRef.current === backfillKey) {
      return;
    }

    lastUnreadBackfillAttemptRef.current = backfillKey;
    void fetchOlderMessages();
  }, [
    activeConversation,
    channelUnreadMarker,
    messages,
    messagesLoading,
    loadingOlder,
    hasOlderMessages,
    fetchOlderMessages,
  ]);

  const wsHandlers = useRealtimeAppState({
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
  });
  const {
    handleSendMessage,
    handleResendChannelMessage,
    handleSetChannelFloodScopeOverride,
    handleSenderClick,
    handleTrace,
    handleBlockKey,
    handleBlockName,
  } = useConversationActions({
    activeConversation,
    activeConversationRef,
    setChannels,
    addMessageIfNew,
    jumpToBottom,
    handleToggleBlockedKey,
    handleToggleBlockedName,
    messageInputRef,
  });
  const handleCreateCrackedChannel = useCallback(
    async (name: string, key: string) => {
      const created = await api.createChannel(name, key);
      const updatedChannels = await api.getChannels();
      setChannels(updatedChannels);
      await api.decryptHistoricalPackets({
        key_type: 'channel',
        channel_key: created.key,
      });
      void fetchUndecryptedCount().catch((error) => {
        console.error('Failed to refresh undecrypted count after cracked channel create:', error);
      });
    },
    [fetchUndecryptedCount, setChannels]
  );

  const statusProps = {
    health,
    config,
  };
  const sidebarProps = {
    contacts,
    channels,
    activeConversation,
    onSelectConversation: handleSelectConversationWithTargetReset,
    onNewMessage: handleOpenNewMessage,
    lastMessageTimes,
    unreadCounts,
    mentions,
    showCracker,
    crackerRunning,
    onToggleCracker: handleToggleCracker,
    onMarkAllRead: () => {
      void markAllRead();
    },
    favorites,
    sortOrder: appSettings?.sidebar_sort_order ?? 'recent',
    onSortOrderChange: (sortOrder: 'recent' | 'alpha') => {
      void handleSortOrderChange(sortOrder);
    },
    isConversationNotificationsEnabled,
  };
  const conversationPaneProps = {
    activeConversation,
    contacts,
    channels,
    rawPackets,
    config,
    health,
    favorites,
    messages,
    messagesLoading,
    loadingOlder,
    hasOlderMessages,
    unreadMarkerLastReadAt:
      activeConversation?.type === 'channel' &&
      channelUnreadMarker?.channelId === activeConversation.id
        ? channelUnreadMarker.lastReadAt
        : undefined,
    targetMessageId,
    hasNewerMessages,
    loadingNewer,
    messageInputRef,
    onTrace: handleTrace,
    onToggleFavorite: handleToggleFavorite,
    onDeleteContact: handleDeleteContact,
    onDeleteChannel: handleDeleteChannel,
    onSetChannelFloodScopeOverride: handleSetChannelFloodScopeOverride,
    onOpenContactInfo: handleOpenContactInfo,
    onOpenChannelInfo: handleOpenChannelInfo,
    onSenderClick: handleSenderClick,
    onLoadOlder: fetchOlderMessages,
    onResendChannelMessage: handleResendChannelMessage,
    onTargetReached: () => setTargetMessageId(null),
    onLoadNewer: fetchNewerMessages,
    onJumpToBottom: jumpToBottom,
    onSendMessage: handleSendMessage,
    onDismissUnreadMarker: () => setChannelUnreadMarker(null),
    notificationsSupported,
    notificationsPermission,
    notificationsEnabled:
      activeConversation?.type === 'contact' || activeConversation?.type === 'channel'
        ? isConversationNotificationsEnabled(activeConversation.type, activeConversation.id)
        : false,
    onToggleNotifications: () => {
      if (activeConversation?.type === 'contact' || activeConversation?.type === 'channel') {
        void toggleConversationNotifications(
          activeConversation.type,
          activeConversation.id,
          activeConversation.name
        );
      }
    },
  };
  const searchProps = {
    contacts,
    channels,
    onNavigateToMessage: handleNavigateToMessage,
    prefillRequest: searchPrefillRequest,
  };
  const settingsProps = {
    config,
    health,
    appSettings,
    onSave: handleSaveConfig,
    onSaveAppSettings: handleSaveAppSettings,
    onSetPrivateKey: handleSetPrivateKey,
    onReboot: handleReboot,
    onDisconnect: handleDisconnect,
    onReconnect: handleReconnect,
    onAdvertise: handleAdvertise,
    onHealthRefresh: handleHealthRefresh,
    onRefreshAppSettings: fetchAppSettings,
    blockedKeys: appSettings?.blocked_keys,
    blockedNames: appSettings?.blocked_names,
    onToggleBlockedKey: handleBlockKey,
    onToggleBlockedName: handleBlockName,
  };
  const crackerProps = {
    packets: rawPackets,
    channels,
    onChannelCreate: handleCreateCrackedChannel,
  };
  const newMessageModalProps = {
    contacts,
    undecryptedCount,
    onSelectConversation: handleSelectConversationWithTargetReset,
    onCreateContact: handleCreateContact,
    onCreateChannel: handleCreateChannel,
    onCreateHashtagChannel: handleCreateHashtagChannel,
  };
  const contactInfoPaneProps = {
    contactKey: infoPaneContactKey,
    fromChannel: infoPaneFromChannel,
    onClose: handleCloseContactInfo,
    contacts,
    config,
    favorites,
    onToggleFavorite: handleToggleFavorite,
    onNavigateToChannel: handleNavigateToChannel,
    onSearchMessagesByKey: (publicKey: string) => {
      handleOpenSearchWithQuery(`user:${publicKey}`);
    },
    onSearchMessagesByName: (name: string) => {
      handleOpenSearchWithQuery(`user:${quoteSearchOperatorValue(name)}`);
    },
    onToggleBlockedKey: handleBlockKey,
    onToggleBlockedName: handleBlockName,
    blockedKeys: appSettings?.blocked_keys ?? [],
    blockedNames: appSettings?.blocked_names ?? [],
  };
  const channelInfoPaneProps = {
    channelKey: infoPaneChannelKey,
    onClose: handleCloseChannelInfo,
    channels,
    favorites,
    onToggleFavorite: handleToggleFavorite,
  };

  // Connect to WebSocket
  useWebSocket(wsHandlers);

  // Initial fetch for config, settings, and data
  useEffect(() => {
    fetchConfig();
    fetchAppSettings();
    fetchUndecryptedCount();

    // Fetch contacts and channels via REST (parallel, faster than WS serial push)
    takePrefetchOrFetch('channels', api.getChannels).then(setChannels).catch(console.error);
    fetchAllContacts()
      .then((data) => {
        setContacts(data);
        setContactsLoaded(true);
      })
      .catch((err) => {
        console.error(err);
        setContactsLoaded(true);
      });
  }, [
    fetchConfig,
    fetchAppSettings,
    fetchUndecryptedCount,
    fetchAllContacts,
    setChannels,
    setContacts,
    setContactsLoaded,
  ]);
  return (
    <AppShell
      localLabel={localLabel}
      showNewMessage={showNewMessage}
      showSettings={showSettings}
      settingsSection={settingsSection}
      sidebarOpen={sidebarOpen}
      showCracker={showCracker}
      onSettingsSectionChange={setSettingsSection}
      onSidebarOpenChange={setSidebarOpen}
      onCrackerRunningChange={setCrackerRunning}
      onToggleSettingsView={handleToggleSettingsView}
      onCloseSettingsView={handleCloseSettingsView}
      onCloseNewMessage={handleCloseNewMessage}
      onLocalLabelChange={setLocalLabel}
      statusProps={statusProps}
      sidebarProps={sidebarProps}
      conversationPaneProps={conversationPaneProps}
      searchProps={searchProps}
      settingsProps={settingsProps}
      crackerProps={crackerProps}
      newMessageModalProps={newMessageModalProps}
      contactInfoPaneProps={contactInfoPaneProps}
      channelInfoPaneProps={channelInfoPaneProps}
    />
  );
}
