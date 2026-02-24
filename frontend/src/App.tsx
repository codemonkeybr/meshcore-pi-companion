import {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
  startTransition,
  lazy,
  Suspense,
} from 'react';
import { api } from './api';
import { takePrefetch } from './prefetch';
import { useWebSocket } from './useWebSocket';
import {
  useRepeaterMode,
  useUnreadCounts,
  useConversationMessages,
  getMessageContentKey,
  useRadioControl,
  useAppSettings,
  useConversationRouter,
  useContactsAndChannels,
} from './hooks';
import * as messageCache from './messageCache';
import { StatusBar } from './components/StatusBar';
import { Sidebar } from './components/Sidebar';
import { ChatHeader } from './components/ChatHeader';
import { MessageList } from './components/MessageList';
import { MessageInput, type MessageInputHandle } from './components/MessageInput';
import { NewMessageModal } from './components/NewMessageModal';
import {
  SETTINGS_SECTION_LABELS,
  SETTINGS_SECTION_ORDER,
  type SettingsSection,
} from './components/settingsConstants';
import { RawPacketList } from './components/RawPacketList';

// Lazy-load heavy components to reduce initial bundle
const MapView = lazy(() => import('./components/MapView').then((m) => ({ default: m.MapView })));
const VisualizerView = lazy(() =>
  import('./components/VisualizerView').then((m) => ({ default: m.VisualizerView }))
);
const SettingsModal = lazy(() =>
  import('./components/SettingsModal').then((m) => ({ default: m.SettingsModal }))
);
const CrackerPanel = lazy(() =>
  import('./components/CrackerPanel').then((m) => ({ default: m.CrackerPanel }))
);
import { Sheet, SheetContent, SheetHeader, SheetTitle } from './components/ui/sheet';
import { Toaster, toast } from './components/ui/sonner';
import { getStateKey } from './utils/conversationState';
import { appendRawPacketUnique } from './utils/rawPacketIdentity';
import { cn } from '@/lib/utils';
import type { Contact, Conversation, HealthStatus, Message, MessagePath, RawPacket } from './types';

const MAX_RAW_PACKETS = 500;

export function App() {
  const messageInputRef = useRef<MessageInputHandle>(null);
  const [rawPackets, setRawPackets] = useState<RawPacket[]>([]);
  const [showNewMessage, setShowNewMessage] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [settingsSection, setSettingsSection] = useState<SettingsSection>('radio');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showCracker, setShowCracker] = useState(false);
  const [crackerRunning, setCrackerRunning] = useState(false);

  // Defer CrackerPanel mount until first opened (lazy-loaded, but keep mounted after for state)
  const crackerMounted = useRef(false);
  if (showCracker) crackerMounted.current = true;

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
    setConfig,
    prevHealthRef,
    fetchConfig,
    handleSaveConfig,
    handleSetPrivateKey,
    handleReboot,
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
  } = useAppSettings();

  // Keep user's name in ref for mention detection in WebSocket callback
  const myNameRef = useRef<string | null>(null);
  useEffect(() => {
    myNameRef.current = config?.name ?? null;
  }, [config?.name]);

  // Check if a message mentions the user
  const checkMention = useCallback((text: string): boolean => {
    const name = myNameRef.current;
    if (!name) return false;
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const mentionPattern = new RegExp(`@\\[${escaped}\\]`, 'i');
    return mentionPattern.test(text);
  }, []);

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

  // Custom hooks for conversation-specific functionality
  const {
    messages,
    messagesLoading,
    loadingOlder,
    hasOlderMessages,
    setMessages,
    fetchOlderMessages,
    addMessageIfNew,
    updateMessageAck,
  } = useConversationMessages(activeConversation);

  const {
    unreadCounts,
    mentions,
    lastMessageTimes,
    incrementUnread,
    markAllRead,
    trackNewMessage,
  } = useUnreadCounts(channels, contacts, activeConversation);

  const {
    repeaterLoggedIn,
    activeContactIsRepeater,
    handleTelemetryRequest,
    handleRepeaterCommand,
  } = useRepeaterMode(activeConversation, contacts, setMessages, activeConversationRef);

  // WebSocket handlers - memoized to prevent reconnection loops
  const wsHandlers = useMemo(
    () => ({
      onHealth: (data: HealthStatus) => {
        const prev = prevHealthRef.current;
        prevHealthRef.current = data;
        setHealth(data);

        // Show toast on connection status change
        if (prev !== null && prev.radio_connected !== data.radio_connected) {
          if (data.radio_connected) {
            toast.success('Radio connected', {
              description: data.connection_info
                ? `Connected via ${data.connection_info}`
                : undefined,
            });
            // Refresh config after reconnection (may have changed after reboot)
            api.getRadioConfig().then(setConfig).catch(console.error);
          } else {
            toast.error('Radio disconnected', {
              description: 'Check radio connection and power',
            });
          }
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
      onMessage: (msg: Message) => {
        const activeConv = activeConversationRef.current;

        // Check if message belongs to the active conversation
        const isForActiveConversation = (() => {
          if (!activeConv) return false;
          if (msg.type === 'CHAN' && activeConv.type === 'channel') {
            return msg.conversation_key === activeConv.id;
          }
          if (msg.type === 'PRIV' && activeConv.type === 'contact') {
            return msg.conversation_key === activeConv.id;
          }
          return false;
        })();

        // Only add to message list if it's for the active conversation
        if (isForActiveConversation) {
          addMessageIfNew(msg);
        }

        // Track for unread counts and sorting
        trackNewMessage(msg);

        const contentKey = getMessageContentKey(msg);

        // For non-active conversations: update cache and count unreads
        if (!isForActiveConversation) {
          // Update message cache (instant restore on switch) — returns true if new
          const isNew = messageCache.addMessage(msg.conversation_key, msg, contentKey);

          // Count unread for incoming messages (skip duplicates from multiple mesh paths)
          if (!msg.outgoing && isNew) {
            let stateKey: string | null = null;
            if (msg.type === 'CHAN' && msg.conversation_key) {
              stateKey = getStateKey('channel', msg.conversation_key);
            } else if (msg.type === 'PRIV' && msg.conversation_key) {
              stateKey = getStateKey('contact', msg.conversation_key);
            }
            if (stateKey) {
              const hasMention = checkMention(msg.text);
              incrementUnread(stateKey, hasMention);
            }
          }
        }
      },
      onContact: (contact: Contact) => {
        setContacts((prev) => {
          const idx = prev.findIndex((c) => c.public_key === contact.public_key);
          if (idx >= 0) {
            const existing = prev[idx];
            const merged = { ...existing, ...contact };
            const unchanged = (Object.keys(merged) as (keyof Contact)[]).every(
              (k) => existing[k] === merged[k]
            );
            if (unchanged) return prev;
            const updated = [...prev];
            updated[idx] = merged;
            return updated;
          }
          return [...prev, contact as Contact];
        });
      },
      onRawPacket: (packet: RawPacket) => {
        setRawPackets((prev) => appendRawPacketUnique(prev, packet, MAX_RAW_PACKETS));
      },
      onMessageAcked: (messageId: number, ackCount: number, paths?: MessagePath[]) => {
        updateMessageAck(messageId, ackCount, paths);
        messageCache.updateAck(messageId, ackCount, paths);
      },
    }),
    [
      addMessageIfNew,
      trackNewMessage,
      incrementUnread,
      updateMessageAck,
      checkMention,
      prevHealthRef,
      setHealth,
      setConfig,
      activeConversationRef,
      setContacts,
    ]
  );

  // Connect to WebSocket
  useWebSocket(wsHandlers);

  // Initial fetch for config, settings, and data
  useEffect(() => {
    fetchConfig();
    fetchAppSettings();
    fetchUndecryptedCount();

    // Fetch contacts and channels via REST (parallel, faster than WS serial push)
    (takePrefetch('channels') ?? api.getChannels()).then(setChannels).catch(console.error);
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

  // Send message handler
  const handleSendMessage = useCallback(
    async (text: string) => {
      if (!activeConversation) return;

      const conversationId = activeConversation.id;

      let sent: Message;
      if (activeConversation.type === 'channel') {
        sent = await api.sendChannelMessage(activeConversation.id, text);
      } else {
        sent = await api.sendDirectMessage(activeConversation.id, text);
      }

      if (activeConversationRef.current?.id === conversationId) {
        addMessageIfNew(sent);
      }
    },
    [activeConversation, addMessageIfNew, activeConversationRef]
  );

  // Handle resend channel message
  const handleResendChannelMessage = useCallback(
    async (messageId: number, newTimestamp?: boolean) => {
      try {
        // New-timestamp resend creates a new message; the backend broadcast_event
        // will add it to the conversation via WebSocket.
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

  // Handle sender click to add mention
  const handleSenderClick = useCallback((sender: string) => {
    messageInputRef.current?.appendText(`@[${sender}] `);
  }, []);

  // Handle direct trace request
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

  const handleCloseSettingsView = useCallback(() => {
    startTransition(() => setShowSettings(false));
    setSidebarOpen(false);
  }, []);

  const handleToggleSettingsView = useCallback(() => {
    startTransition(() => {
      setShowSettings((prev) => !prev);
    });
    setSidebarOpen(false);
  }, []);

  const handleNewMessage = useCallback(() => {
    setShowNewMessage(true);
    setSidebarOpen(false);
  }, []);

  const handleToggleCracker = useCallback(() => {
    setShowCracker((prev) => !prev);
  }, []);

  // Sidebar content (shared between desktop and mobile)
  const sidebarContent = (
    <Sidebar
      contacts={contacts}
      channels={channels}
      activeConversation={activeConversation}
      onSelectConversation={handleSelectConversation}
      onNewMessage={handleNewMessage}
      lastMessageTimes={lastMessageTimes}
      unreadCounts={unreadCounts}
      mentions={mentions}
      showCracker={showCracker}
      crackerRunning={crackerRunning}
      onToggleCracker={handleToggleCracker}
      onMarkAllRead={markAllRead}
      favorites={favorites}
      sortOrder={appSettings?.sidebar_sort_order ?? 'recent'}
      onSortOrderChange={handleSortOrderChange}
    />
  );

  const settingsSidebarContent = (
    <div className="sidebar w-60 h-full min-h-0 bg-card border-r border-border flex flex-col">
      <div className="flex justify-between items-center px-3 py-2.5 border-b border-border">
        <h2 className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium">
          Settings
        </h2>
        <button
          type="button"
          onClick={handleCloseSettingsView}
          className="h-6 w-6 rounded text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
          title="Back to conversations"
          aria-label="Back to conversations"
        >
          &larr;
        </button>
      </div>
      <div className="flex-1 overflow-y-auto py-1">
        {SETTINGS_SECTION_ORDER.map((section) => (
          <button
            key={section}
            type="button"
            className={cn(
              'w-full px-3 py-2 text-left text-[13px] border-l-2 border-transparent hover:bg-accent transition-colors',
              settingsSection === section && 'bg-accent border-l-primary'
            )}
            onClick={() => setSettingsSection(section)}
          >
            {SETTINGS_SECTION_LABELS[section]}
          </button>
        ))}
      </div>
    </div>
  );

  const activeSidebarContent = showSettings ? settingsSidebarContent : sidebarContent;

  return (
    <div className="flex flex-col h-full">
      <StatusBar
        health={health}
        config={config}
        settingsMode={showSettings}
        onSettingsClick={handleToggleSettingsView}
        onMenuClick={showSettings ? undefined : () => setSidebarOpen(true)}
      />

      <div className="flex flex-1 overflow-hidden">
        {/* Desktop sidebar - hidden on mobile */}
        <div className="hidden md:block">{activeSidebarContent}</div>

        {/* Mobile sidebar - Sheet that slides in */}
        <Sheet open={sidebarOpen} onOpenChange={setSidebarOpen}>
          <SheetContent side="left" className="w-[280px] p-0 flex flex-col" hideCloseButton>
            <SheetHeader className="sr-only">
              <SheetTitle>Navigation</SheetTitle>
            </SheetHeader>
            <div className="flex-1 overflow-hidden">{activeSidebarContent}</div>
          </SheetContent>
        </Sheet>

        <main className="flex-1 flex flex-col bg-background min-w-0">
          <div className={cn('flex-1 flex flex-col min-h-0', showSettings && 'hidden')}>
            {activeConversation ? (
              activeConversation.type === 'map' ? (
                <>
                  <div className="flex justify-between items-center px-4 py-2.5 border-b border-border font-semibold text-base">
                    Node Map
                  </div>
                  <div className="flex-1 overflow-hidden">
                    <Suspense
                      fallback={
                        <div className="flex-1 flex items-center justify-center text-muted-foreground">
                          Loading map...
                        </div>
                      }
                    >
                      <MapView contacts={contacts} focusedKey={activeConversation.mapFocusKey} />
                    </Suspense>
                  </div>
                </>
              ) : activeConversation.type === 'visualizer' ? (
                <Suspense
                  fallback={
                    <div className="flex-1 flex items-center justify-center text-muted-foreground">
                      Loading visualizer...
                    </div>
                  }
                >
                  <VisualizerView packets={rawPackets} contacts={contacts} config={config} />
                </Suspense>
              ) : activeConversation.type === 'raw' ? (
                <>
                  <div className="flex justify-between items-center px-4 py-2.5 border-b border-border font-semibold text-base">
                    Raw Packet Feed
                  </div>
                  <div className="flex-1 overflow-hidden">
                    <RawPacketList packets={rawPackets} />
                  </div>
                </>
              ) : (
                <>
                  <ChatHeader
                    conversation={activeConversation}
                    contacts={contacts}
                    config={config}
                    favorites={favorites}
                    onTrace={handleTrace}
                    onToggleFavorite={handleToggleFavorite}
                    onDeleteChannel={handleDeleteChannel}
                    onDeleteContact={handleDeleteContact}
                  />
                  <MessageList
                    key={activeConversation.id}
                    messages={messages}
                    contacts={contacts}
                    loading={messagesLoading}
                    loadingOlder={loadingOlder}
                    hasOlderMessages={hasOlderMessages}
                    onSenderClick={
                      activeConversation.type === 'channel' ? handleSenderClick : undefined
                    }
                    onLoadOlder={fetchOlderMessages}
                    onResendChannelMessage={
                      activeConversation.type === 'channel' ? handleResendChannelMessage : undefined
                    }
                    radioName={config?.name}
                    config={config}
                  />
                  <MessageInput
                    ref={messageInputRef}
                    onSend={
                      activeContactIsRepeater
                        ? repeaterLoggedIn
                          ? handleRepeaterCommand
                          : handleTelemetryRequest
                        : handleSendMessage
                    }
                    disabled={!health?.radio_connected}
                    isRepeaterMode={activeContactIsRepeater && !repeaterLoggedIn}
                    conversationType={activeConversation.type}
                    senderName={config?.name}
                    placeholder={
                      !health?.radio_connected
                        ? 'Radio not connected'
                        : activeContactIsRepeater
                          ? repeaterLoggedIn
                            ? 'Send CLI command (requires admin login)...'
                            : `Enter password for ${activeConversation.name} (or . for none)...`
                          : `Message ${activeConversation.name}...`
                    }
                  />
                </>
              )
            ) : (
              <div className="flex-1 flex items-center justify-center text-muted-foreground">
                Select a conversation or start a new one
              </div>
            )}
          </div>

          {showSettings && (
            <div className="flex-1 flex flex-col min-h-0">
              <div className="flex justify-between items-center px-4 py-2.5 border-b border-border font-semibold text-base">
                <span>Radio & Settings</span>
                <span className="text-sm text-muted-foreground hidden md:inline">
                  {SETTINGS_SECTION_LABELS[settingsSection]}
                </span>
              </div>
              <div className="flex-1 min-h-0 overflow-hidden">
                <Suspense
                  fallback={
                    <div className="flex-1 flex items-center justify-center p-8 text-muted-foreground">
                      Loading settings...
                    </div>
                  }
                >
                  <SettingsModal
                    open={showSettings}
                    pageMode
                    externalSidebarNav
                    desktopSection={settingsSection}
                    config={config}
                    health={health}
                    appSettings={appSettings}
                    onClose={handleCloseSettingsView}
                    onSave={handleSaveConfig}
                    onSaveAppSettings={handleSaveAppSettings}
                    onSetPrivateKey={handleSetPrivateKey}
                    onReboot={handleReboot}
                    onAdvertise={handleAdvertise}
                    onHealthRefresh={handleHealthRefresh}
                    onRefreshAppSettings={fetchAppSettings}
                  />
                </Suspense>
              </div>
            </div>
          )}
        </main>
      </div>

      {/* Global Cracker Panel - deferred until first opened, then kept mounted for state */}
      <div
        className={cn(
          'border-t border-border bg-background transition-all duration-200 overflow-hidden',
          showCracker ? 'h-[275px]' : 'h-0'
        )}
      >
        {crackerMounted.current && (
          <Suspense
            fallback={
              <div className="flex items-center justify-center h-full text-muted-foreground">
                Loading cracker...
              </div>
            }
          >
            <CrackerPanel
              packets={rawPackets}
              channels={channels}
              visible={showCracker}
              onChannelCreate={async (name, key) => {
                const created = await api.createChannel(name, key);
                const data = await api.getChannels();
                setChannels(data);
                await api.decryptHistoricalPackets({
                  key_type: 'channel',
                  channel_key: created.key,
                });
                fetchUndecryptedCount();
              }}
              onRunningChange={setCrackerRunning}
            />
          </Suspense>
        )}
      </div>

      <NewMessageModal
        open={showNewMessage}
        contacts={contacts}
        undecryptedCount={undecryptedCount}
        onClose={() => setShowNewMessage(false)}
        onSelectConversation={(conv) => {
          setActiveConversation(conv);
          setShowNewMessage(false);
        }}
        onCreateContact={handleCreateContact}
        onCreateChannel={handleCreateChannel}
        onCreateHashtagChannel={handleCreateHashtagChannel}
      />

      <Toaster position="top-right" />
    </div>
  );
}
