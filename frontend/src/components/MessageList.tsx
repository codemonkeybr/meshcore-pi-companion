import {
  useEffect,
  useLayoutEffect,
  useRef,
  useCallback,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import type { Contact, Message, MessagePath, RadioConfig } from '../types';
import { CONTACT_TYPE_REPEATER } from '../types';
import { formatTime, parseSenderFromText } from '../utils/messageParser';
import { formatHopCounts, type SenderInfo } from '../utils/pathUtils';
import { ContactAvatar } from './ContactAvatar';
import { PathModal } from './PathModal';
import { handleKeyboardActivate } from '../utils/a11y';
import { cn } from '@/lib/utils';

interface MessageListProps {
  messages: Message[];
  contacts: Contact[];
  loading: boolean;
  loadingOlder?: boolean;
  hasOlderMessages?: boolean;
  onSenderClick?: (sender: string) => void;
  onLoadOlder?: () => void;
  onResendChannelMessage?: (messageId: number, newTimestamp?: boolean) => void;
  radioName?: string;
  config?: RadioConfig | null;
  onOpenContactInfo?: (publicKey: string, fromChannel?: boolean) => void;
  targetMessageId?: number | null;
  onTargetReached?: () => void;
  hasNewerMessages?: boolean;
  loadingNewer?: boolean;
  onLoadNewer?: () => void;
  onJumpToBottom?: () => void;
}

// URL regex for linkifying plain text
const URL_PATTERN =
  /https?:\/\/(www\.)?[-a-zA-Z0-9@:%._+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_+.~#?&//=]*)/g;

// Helper to convert URLs in a plain text string into clickable links
function linkifyText(text: string, keyPrefix: string): ReactNode[] {
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let keyIndex = 0;

  URL_PATTERN.lastIndex = 0;
  while ((match = URL_PATTERN.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    parts.push(
      <a
        key={`${keyPrefix}-link-${keyIndex++}`}
        href={match[0]}
        target="_blank"
        rel="noopener noreferrer"
        className="text-primary underline hover:text-primary/80"
      >
        {match[0]}
      </a>
    );
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex === 0) return [text];
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts;
}

// Helper to render text with highlighted @[Name] mentions and clickable URLs
function renderTextWithMentions(text: string, radioName?: string): ReactNode {
  const mentionPattern = /@\[([^\]]+)\]/g;
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let keyIndex = 0;

  while ((match = mentionPattern.exec(text)) !== null) {
    // Add text before the match (with linkification)
    if (match.index > lastIndex) {
      parts.push(...linkifyText(text.slice(lastIndex, match.index), `pre-${keyIndex}`));
    }

    const mentionedName = match[1];
    const isOwnMention = radioName ? mentionedName === radioName : false;

    parts.push(
      <span
        key={`mention-${keyIndex++}`}
        className={cn(
          'rounded px-0.5',
          isOwnMention ? 'bg-primary/30 text-primary font-medium' : 'bg-muted-foreground/20'
        )}
      >
        @[{mentionedName}]
      </span>
    );

    lastIndex = match.index + match[0].length;
  }

  // Add remaining text after last match (with linkification)
  if (lastIndex < text.length) {
    parts.push(...linkifyText(text.slice(lastIndex), `post-${keyIndex}`));
  }

  return parts.length > 0 ? parts : text;
}

// Clickable hop count badge that opens the path modal
interface HopCountBadgeProps {
  paths: MessagePath[];
  onClick: () => void;
  variant: 'header' | 'inline';
}

function HopCountBadge({ paths, onClick, variant }: HopCountBadgeProps) {
  const hopInfo = formatHopCounts(paths);
  const label = `(${hopInfo.display})`;

  const className =
    variant === 'header'
      ? 'font-normal text-muted-foreground ml-1 text-[11px] cursor-pointer hover:text-primary hover:underline'
      : 'text-[10px] text-muted-foreground ml-1 cursor-pointer hover:text-primary hover:underline';

  return (
    <span
      className={className}
      role="button"
      tabIndex={0}
      onKeyDown={handleKeyboardActivate}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title="View message path"
      aria-label={`${hopInfo.display}, view path`}
    >
      {label}
    </span>
  );
}

const RESEND_WINDOW_SECONDS = 30;

export function MessageList({
  messages,
  contacts,
  loading,
  loadingOlder = false,
  hasOlderMessages = false,
  onSenderClick,
  onLoadOlder,
  onResendChannelMessage,
  radioName,
  config,
  onOpenContactInfo,
  targetMessageId,
  onTargetReached,
  hasNewerMessages = false,
  loadingNewer = false,
  onLoadNewer,
  onJumpToBottom,
}: MessageListProps) {
  const listRef = useRef<HTMLDivElement>(null);
  const prevMessagesLengthRef = useRef<number>(0);
  const isInitialLoadRef = useRef<boolean>(true);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const [selectedPath, setSelectedPath] = useState<{
    paths: MessagePath[];
    senderInfo: SenderInfo;
    messageId?: number;
    isOutgoingChan?: boolean;
  } | null>(null);
  const [resendableIds, setResendableIds] = useState<Set<number>>(new Set());
  const resendTimersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());
  const [highlightedMessageId, setHighlightedMessageId] = useState<number | null>(null);
  const targetScrolledRef = useRef(false);

  // Capture scroll state in the scroll handler BEFORE any state updates
  const scrollStateRef = useRef({
    scrollTop: 0,
    scrollHeight: 0,
    clientHeight: 0,
    wasNearTop: false,
    wasNearBottom: true, // Default to true so initial messages scroll to bottom
  });

  // Track conversation key to detect when entire message set changes
  const prevConvKeyRef = useRef<string | null>(null);

  // Handle scroll position AFTER render
  useLayoutEffect(() => {
    if (!listRef.current) return;

    const list = listRef.current;
    const messagesAdded = messages.length - prevMessagesLengthRef.current;

    // Detect if messages are from a different conversation (handles the case where
    // the key prop remount consumes isInitialLoadRef on stale data from the previous
    // conversation before the cache restore effect sets the correct messages)
    const convKey = messages.length > 0 ? messages[0].conversation_key : null;
    const conversationChanged = convKey !== null && convKey !== prevConvKeyRef.current;
    if (convKey !== null) prevConvKeyRef.current = convKey;

    if ((isInitialLoadRef.current || conversationChanged) && messages.length > 0) {
      // Initial load or conversation switch - scroll to bottom
      list.scrollTop = list.scrollHeight;
      isInitialLoadRef.current = false;
    } else if (messagesAdded > 0 && prevMessagesLengthRef.current > 0) {
      // Messages were added - use scroll state captured before the update
      const scrollHeightDiff = list.scrollHeight - scrollStateRef.current.scrollHeight;

      if (scrollStateRef.current.wasNearTop && scrollHeightDiff > 0) {
        // User was near top (loading older) - preserve position by adding the height diff
        list.scrollTop = scrollStateRef.current.scrollTop + scrollHeightDiff;
      } else if (scrollStateRef.current.wasNearBottom && !hasNewerMessagesRef.current) {
        // User was near bottom - scroll to bottom for new messages (including sent).
        // Skip when browsing mid-history (hasNewerMessages) so that forward-pagination
        // appends in place instead of chasing the bottom in an infinite load loop.
        list.scrollTop = list.scrollHeight;
      }
    }

    prevMessagesLengthRef.current = messages.length;
  }, [messages]);

  // Scroll to target message and highlight it
  useLayoutEffect(() => {
    if (!targetMessageId || targetScrolledRef.current || messages.length === 0) return;
    const el = listRef.current?.querySelector(`[data-message-id="${targetMessageId}"]`);
    if (!el) return;

    // Prevent the initial-load layout effect from overriding our scroll
    isInitialLoadRef.current = false;
    el.scrollIntoView({ block: 'center' });
    setHighlightedMessageId(targetMessageId);
    targetScrolledRef.current = true;
    onTargetReached?.();
  }, [messages, targetMessageId, onTargetReached]);

  // Reset target scroll tracking when targetMessageId changes
  useEffect(() => {
    targetScrolledRef.current = false;
  }, [targetMessageId]);

  // Reset initial load flag when conversation changes (messages becomes empty then filled)
  useEffect(() => {
    if (messages.length === 0) {
      isInitialLoadRef.current = true;
      prevMessagesLengthRef.current = 0;
      prevConvKeyRef.current = null;
      scrollStateRef.current = {
        scrollTop: 0,
        scrollHeight: 0,
        clientHeight: 0,
        wasNearTop: false,
        wasNearBottom: true,
      };
    }
  }, [messages.length]);

  // Track resendable outgoing CHAN messages (within 30s window)
  useEffect(() => {
    if (!onResendChannelMessage) return;

    const now = Math.floor(Date.now() / 1000);
    const newResendable = new Set<number>();
    const timers = resendTimersRef.current;

    for (const msg of messages) {
      if (!msg.outgoing || msg.type !== 'CHAN' || msg.sender_timestamp === null) continue;
      const remaining = RESEND_WINDOW_SECONDS - (now - msg.sender_timestamp);
      if (remaining <= 0) continue;

      newResendable.add(msg.id);

      // Schedule removal if not already tracked
      if (!timers.has(msg.id)) {
        const timer = setTimeout(() => {
          setResendableIds((prev) => {
            const next = new Set(prev);
            next.delete(msg.id);
            return next;
          });
          timers.delete(msg.id);
        }, remaining * 1000);
        timers.set(msg.id, timer);
      }
    }

    setResendableIds(newResendable);

    return () => {
      for (const timer of timers.values()) clearTimeout(timer);
      timers.clear();
    };
  }, [messages, onResendChannelMessage]);

  // Refs for scroll handler to read without causing callback recreation
  const onLoadOlderRef = useRef(onLoadOlder);
  const loadingOlderRef = useRef(loadingOlder);
  const hasOlderMessagesRef = useRef(hasOlderMessages);
  const onLoadNewerRef = useRef(onLoadNewer);
  const loadingNewerRef = useRef(loadingNewer);
  const hasNewerMessagesRef = useRef(hasNewerMessages);
  onLoadOlderRef.current = onLoadOlder;
  loadingOlderRef.current = loadingOlder;
  hasOlderMessagesRef.current = hasOlderMessages;
  onLoadNewerRef.current = onLoadNewer;
  loadingNewerRef.current = loadingNewer;
  hasNewerMessagesRef.current = hasNewerMessages;

  // Handle scroll - capture state and detect when user is near top/bottom
  // Stable callback: reads changing values from refs, never recreated.
  const handleScroll = useCallback(() => {
    if (!listRef.current) return;

    const { scrollTop, scrollHeight, clientHeight } = listRef.current;
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;

    // Always capture current scroll state (needed for scroll preservation)
    scrollStateRef.current = {
      scrollTop,
      scrollHeight,
      clientHeight,
      wasNearTop: scrollTop < 150,
      wasNearBottom: distanceFromBottom < 100,
    };

    // Show scroll-to-bottom button when not near the bottom (more than 100px away)
    setShowScrollToBottom(distanceFromBottom > 100);

    if (!onLoadOlderRef.current || loadingOlderRef.current || !hasOlderMessagesRef.current) {
      // skip older load
    } else if (scrollTop < 100) {
      onLoadOlderRef.current();
    }

    // Trigger load newer when within 100px of bottom
    if (
      onLoadNewerRef.current &&
      !loadingNewerRef.current &&
      hasNewerMessagesRef.current &&
      distanceFromBottom < 100
    ) {
      onLoadNewerRef.current();
    }
  }, []);

  // Scroll to bottom handler (or jump to bottom if viewing historical messages)
  const scrollToBottom = useCallback(() => {
    if (hasNewerMessages && onJumpToBottom) {
      onJumpToBottom();
      return;
    }
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [hasNewerMessages, onJumpToBottom]);

  // Sort messages by received_at ascending (oldest first)
  // Note: Deduplication is handled by useConversationMessages.addMessageIfNew()
  // and the database UNIQUE constraint on (type, conversation_key, text, sender_timestamp)
  const sortedMessages = useMemo(
    () => [...messages].sort((a, b) => a.received_at - b.received_at || a.id - b.id),
    [messages]
  );

  // Sender info for outgoing messages (used by path modal on own messages)
  const selfSenderInfo = useMemo<SenderInfo>(
    () => ({
      name: config?.name || 'Unknown',
      publicKeyOrPrefix: config?.public_key || '',
      lat: config?.lat ?? null,
      lon: config?.lon ?? null,
      pathHashMode: config?.path_hash_mode ?? null,
    }),
    [config?.name, config?.public_key, config?.lat, config?.lon, config?.path_hash_mode]
  );

  // Derive live so the byte-perfect button disables if the 30s window expires while modal is open
  const isSelectedMessageResendable =
    selectedPath?.messageId !== undefined && resendableIds.has(selectedPath.messageId);

  // Look up contact by public key
  const getContact = (conversationKey: string | null): Contact | null => {
    if (!conversationKey) return null;
    return contacts.find((c) => c.public_key === conversationKey) || null;
  };

  // Look up contact by name (for channel messages where we parse sender from text)
  const getContactByName = (name: string): Contact | null => {
    return contacts.find((c) => c.name === name) || null;
  };

  // Build sender info for path modal
  const getSenderInfo = (
    msg: Message,
    contact: Contact | null,
    parsedSender: string | null
  ): SenderInfo => {
    if (msg.type === 'PRIV' && contact) {
      return {
        name: contact.name || contact.public_key.slice(0, 12),
        publicKeyOrPrefix: contact.public_key,
        lat: contact.lat,
        lon: contact.lon,
        pathHashMode: contact.out_path_hash_mode,
      };
    }
    // For channel messages, try to find contact by parsed sender name
    if (parsedSender) {
      const senderContact = getContactByName(parsedSender);
      if (senderContact) {
        return {
          name: parsedSender,
          publicKeyOrPrefix: senderContact.public_key,
          lat: senderContact.lat,
          lon: senderContact.lon,
          pathHashMode: senderContact.out_path_hash_mode,
        };
      }
    }
    // Fallback: unknown sender
    return {
      name: parsedSender || 'Unknown',
      publicKeyOrPrefix: msg.conversation_key || '',
      lat: null,
      lon: null,
      pathHashMode: null,
    };
  };

  if (loading) {
    return (
      <div className="flex-1 overflow-y-auto p-5 text-center text-muted-foreground" role="status">
        Loading messages...
      </div>
    );
  }

  if (messages.length === 0) {
    return (
      <div className="flex-1 overflow-y-auto p-5 text-center text-muted-foreground">
        No messages yet
      </div>
    );
  }

  // Helper to get a unique sender key for grouping messages
  const getSenderKey = (msg: Message, sender: string | null): string => {
    if (msg.outgoing) return '__outgoing__';
    if (msg.type === 'PRIV' && msg.conversation_key) return msg.conversation_key;
    return sender || '__unknown__';
  };

  return (
    <div className="flex-1 overflow-hidden relative">
      <div
        className="h-full overflow-y-auto p-4 flex flex-col gap-0.5"
        ref={listRef}
        onScroll={handleScroll}
      >
        {loadingOlder && (
          <div className="text-center py-2 text-muted-foreground text-sm" role="status">
            Loading older messages...
          </div>
        )}
        {!loadingOlder && hasOlderMessages && (
          <div className="text-center py-2 text-muted-foreground text-xs">
            Scroll up for older messages
          </div>
        )}
        {sortedMessages.map((msg, index) => {
          // For DMs, look up contact; for channel messages, use parsed sender
          const contact = msg.type === 'PRIV' ? getContact(msg.conversation_key) : null;
          const isRepeater = contact?.type === CONTACT_TYPE_REPEATER;

          // Skip sender parsing for repeater messages (CLI responses often have colons)
          const { sender, content } = isRepeater
            ? { sender: null, content: msg.text }
            : parseSenderFromText(msg.text);
          const displaySender = msg.outgoing
            ? 'You'
            : contact?.name || sender || msg.conversation_key?.slice(0, 8) || 'Unknown';

          const canClickSender = !msg.outgoing && onSenderClick && displaySender !== 'Unknown';

          // Determine if we should show avatar (first message in a chunk from same sender)
          const currentSenderKey = getSenderKey(msg, sender);
          const prevMsg = sortedMessages[index - 1];
          const prevSenderKey = prevMsg
            ? getSenderKey(prevMsg, parseSenderFromText(prevMsg.text).sender)
            : null;
          const isFirstInGroup = currentSenderKey !== prevSenderKey;
          const showAvatar = !msg.outgoing && isFirstInGroup;
          const isFirstMessage = index === 0;

          // Get avatar info for incoming messages
          let avatarName: string | null = null;
          let avatarKey: string = '';
          if (!msg.outgoing) {
            if (msg.type === 'PRIV' && msg.conversation_key) {
              // DM: use conversation_key (sender's public key)
              avatarName = contact?.name || null;
              avatarKey = msg.conversation_key;
            } else if (sender) {
              // Channel message: try to find contact by name, or use sender name as pseudo-key
              const senderContact = getContactByName(sender);
              avatarName = sender;
              avatarKey = senderContact?.public_key || `name:${sender}`;
            }
          }

          return (
            <div
              key={msg.id}
              data-message-id={msg.id}
              className={cn(
                'flex items-start max-w-[85%]',
                msg.outgoing && 'flex-row-reverse self-end',
                isFirstInGroup && !isFirstMessage && 'mt-3'
              )}
            >
              {!msg.outgoing && (
                <div className="w-10 flex-shrink-0 flex items-start pt-0.5">
                  {showAvatar && avatarKey && (
                    <span
                      role={onOpenContactInfo ? 'button' : undefined}
                      tabIndex={onOpenContactInfo ? 0 : undefined}
                      onKeyDown={onOpenContactInfo ? handleKeyboardActivate : undefined}
                      onClick={
                        onOpenContactInfo
                          ? () => onOpenContactInfo(avatarKey, msg.type === 'CHAN')
                          : undefined
                      }
                    >
                      <ContactAvatar
                        name={avatarName}
                        publicKey={avatarKey}
                        size={32}
                        clickable={!!onOpenContactInfo}
                      />
                    </span>
                  )}
                </div>
              )}
              <div
                className={cn(
                  'py-1.5 px-3 rounded-lg min-w-0',
                  msg.outgoing ? 'bg-msg-outgoing' : 'bg-msg-incoming',
                  highlightedMessageId === msg.id && 'message-highlight'
                )}
              >
                {showAvatar && (
                  <div className="text-[13px] font-semibold text-foreground mb-0.5">
                    {canClickSender ? (
                      <span
                        className="cursor-pointer hover:text-primary transition-colors"
                        role="button"
                        tabIndex={0}
                        onKeyDown={handleKeyboardActivate}
                        onClick={() => onSenderClick(displaySender)}
                        title={`Mention ${displaySender}`}
                      >
                        {displaySender}
                      </span>
                    ) : (
                      displaySender
                    )}
                    <span className="font-normal text-muted-foreground ml-2 text-[11px]">
                      {formatTime(msg.sender_timestamp || msg.received_at)}
                    </span>
                    {!msg.outgoing && msg.paths && msg.paths.length > 0 && (
                      <HopCountBadge
                        paths={msg.paths}
                        variant="header"
                        onClick={() =>
                          setSelectedPath({
                            paths: msg.paths!,
                            senderInfo: getSenderInfo(msg, contact, sender),
                          })
                        }
                      />
                    )}
                  </div>
                )}
                <div className="break-words whitespace-pre-wrap">
                  {content.split('\n').map((line, i, arr) => (
                    <span key={i}>
                      {renderTextWithMentions(line, radioName)}
                      {i < arr.length - 1 && <br />}
                    </span>
                  ))}
                  {!showAvatar && (
                    <>
                      <span className="text-[10px] text-muted-foreground ml-2">
                        {formatTime(msg.sender_timestamp || msg.received_at)}
                      </span>
                      {!msg.outgoing && msg.paths && msg.paths.length > 0 && (
                        <HopCountBadge
                          paths={msg.paths}
                          variant="inline"
                          onClick={() =>
                            setSelectedPath({
                              paths: msg.paths!,
                              senderInfo: getSenderInfo(msg, contact, sender),
                            })
                          }
                        />
                      )}
                    </>
                  )}
                  {msg.outgoing &&
                    (msg.acked > 0 ? (
                      msg.paths && msg.paths.length > 0 ? (
                        <span
                          className="text-muted-foreground cursor-pointer hover:text-primary"
                          role="button"
                          tabIndex={0}
                          onKeyDown={handleKeyboardActivate}
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelectedPath({
                              paths: msg.paths!,
                              senderInfo: selfSenderInfo,
                              messageId: msg.id,
                              isOutgoingChan: msg.type === 'CHAN' && !!onResendChannelMessage,
                            });
                          }}
                          title="View echo paths"
                          aria-label={`Acknowledged, ${msg.acked} echo${msg.acked !== 1 ? 's' : ''} — view paths`}
                        >{` ✓${msg.acked > 1 ? msg.acked : ''}`}</span>
                      ) : (
                        <span className="text-muted-foreground">{` ✓${msg.acked > 1 ? msg.acked : ''}`}</span>
                      )
                    ) : onResendChannelMessage && msg.type === 'CHAN' ? (
                      <span
                        className="text-muted-foreground cursor-pointer hover:text-primary"
                        role="button"
                        tabIndex={0}
                        onKeyDown={handleKeyboardActivate}
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedPath({
                            paths: [],
                            senderInfo: selfSenderInfo,
                            messageId: msg.id,
                            isOutgoingChan: true,
                          });
                        }}
                        title="Message status"
                        aria-label="No echoes yet — view message status"
                      >
                        {' '}
                        ?
                      </span>
                    ) : (
                      <span className="text-muted-foreground" title="No repeats heard yet">
                        {' '}
                        ?
                      </span>
                    ))}
                </div>
              </div>
            </div>
          );
        })}
        {loadingNewer && (
          <div className="text-center py-2 text-muted-foreground text-sm" role="status">
            Loading newer messages...
          </div>
        )}
        {!loadingNewer && hasNewerMessages && (
          <div className="text-center py-2 text-muted-foreground text-xs">
            Scroll down for newer messages
          </div>
        )}
      </div>

      {/* Scroll to bottom button */}
      {showScrollToBottom && (
        <button
          onClick={scrollToBottom}
          className="absolute bottom-4 right-4 w-9 h-9 rounded-full bg-card hover:bg-accent border border-border flex items-center justify-center shadow-lg transition-all hover:scale-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          title="Scroll to bottom"
          aria-label="Scroll to bottom"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="text-muted-foreground"
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      )}

      {/* Path modal */}
      {selectedPath && (
        <PathModal
          open={true}
          onClose={() => setSelectedPath(null)}
          paths={selectedPath.paths}
          senderInfo={selectedPath.senderInfo}
          contacts={contacts}
          config={config ?? null}
          messageId={selectedPath.messageId}
          isOutgoingChan={selectedPath.isOutgoingChan}
          isResendable={isSelectedMessageResendable}
          onResend={onResendChannelMessage}
        />
      )}
    </div>
  );
}
