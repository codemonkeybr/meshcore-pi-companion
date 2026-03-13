import { useEffect, useRef, useState } from 'react';
import { Bell, Globe2, Info, Star, Trash2 } from 'lucide-react';
import { toast } from './ui/sonner';
import { DirectTraceIcon } from './DirectTraceIcon';
import { isFavorite } from '../utils/favorites';
import { handleKeyboardActivate } from '../utils/a11y';
import { stripRegionScopePrefix } from '../utils/regionScope';
import { isPrefixOnlyContact } from '../utils/pubkey';
import { ContactAvatar } from './ContactAvatar';
import { ContactStatusInfo } from './ContactStatusInfo';
import type { Channel, Contact, Conversation, Favorite, RadioConfig } from '../types';

interface ChatHeaderProps {
  conversation: Conversation;
  contacts: Contact[];
  channels: Channel[];
  config: RadioConfig | null;
  favorites: Favorite[];
  notificationsSupported: boolean;
  notificationsEnabled: boolean;
  notificationsPermission: NotificationPermission | 'unsupported';
  onTrace: () => void;
  onToggleNotifications: () => void;
  onToggleFavorite: (type: 'channel' | 'contact', id: string) => void;
  onSetChannelFloodScopeOverride?: (key: string, floodScopeOverride: string) => void;
  onDeleteChannel: (key: string) => void;
  onDeleteContact: (publicKey: string) => void;
  onOpenContactInfo?: (publicKey: string) => void;
  onOpenChannelInfo?: (channelKey: string) => void;
}

export function ChatHeader({
  conversation,
  contacts,
  channels,
  config,
  favorites,
  notificationsSupported,
  notificationsEnabled,
  notificationsPermission,
  onTrace,
  onToggleNotifications,
  onToggleFavorite,
  onSetChannelFloodScopeOverride,
  onDeleteChannel,
  onDeleteContact,
  onOpenContactInfo,
  onOpenChannelInfo,
}: ChatHeaderProps) {
  const [showKey, setShowKey] = useState(false);
  const [contactStatusInline, setContactStatusInline] = useState(true);
  const keyTextRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    setShowKey(false);
  }, [conversation.id]);

  const activeChannel =
    conversation.type === 'channel'
      ? channels.find((channel) => channel.key === conversation.id)
      : undefined;
  const activeFloodScopeOverride =
    conversation.type === 'channel' ? (activeChannel?.flood_scope_override ?? null) : null;
  const activeFloodScopeLabel = activeFloodScopeOverride
    ? stripRegionScopePrefix(activeFloodScopeOverride)
    : null;
  const activeFloodScopeDisplay = activeFloodScopeOverride ? activeFloodScopeOverride : null;
  const isPrivateChannel = conversation.type === 'channel' && !activeChannel?.is_hashtag;
  const activeContact =
    conversation.type === 'contact'
      ? contacts.find((contact) => contact.public_key === conversation.id)
      : null;
  const activeContactIsPrefixOnly = activeContact
    ? isPrefixOnlyContact(activeContact.public_key)
    : false;

  const titleClickable =
    (conversation.type === 'contact' && onOpenContactInfo) ||
    (conversation.type === 'channel' && onOpenChannelInfo);
  const favoriteTitle =
    conversation.type === 'contact'
      ? isFavorite(favorites, 'contact', conversation.id)
        ? 'Remove from favorites. Favorite contacts stay loaded on the radio for ACK support.'
        : 'Add to favorites. Favorite contacts stay loaded on the radio for ACK support.'
      : isFavorite(favorites, conversation.type as 'channel' | 'contact', conversation.id)
        ? 'Remove from favorites'
        : 'Add to favorites';

  const handleEditFloodScopeOverride = () => {
    if (conversation.type !== 'channel' || !onSetChannelFloodScopeOverride) return;
    const nextValue = window.prompt(
      'Enter regional override flood scope for this room. This temporarily changes the radio flood scope before send and restores it after, which significantly slows room sends. Leave blank to clear.',
      activeFloodScopeLabel ?? ''
    );
    if (nextValue === null) return;
    onSetChannelFloodScopeOverride(conversation.id, nextValue);
  };

  const handleOpenConversationInfo = () => {
    if (conversation.type === 'contact' && onOpenContactInfo) {
      onOpenContactInfo(conversation.id);
      return;
    }
    if (conversation.type === 'channel' && onOpenChannelInfo) {
      onOpenChannelInfo(conversation.id);
    }
  };

  useEffect(() => {
    if (conversation.type !== 'contact') {
      setContactStatusInline(true);
      return;
    }

    const measure = () => {
      const keyElement = keyTextRef.current;
      if (!keyElement) return;
      const isTruncated = keyElement.scrollWidth > keyElement.clientWidth + 1;
      setContactStatusInline(!isTruncated);
    };

    measure();

    const onResize = () => {
      window.requestAnimationFrame(measure);
    };

    window.addEventListener('resize', onResize);

    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => {
        window.requestAnimationFrame(measure);
      });
      if (keyTextRef.current?.parentElement) {
        observer.observe(keyTextRef.current.parentElement);
      }
    }

    return () => {
      window.removeEventListener('resize', onResize);
      observer?.disconnect();
    };
  }, [conversation.id, conversation.type, showKey]);

  return (
    <header className="conversation-header flex justify-between items-start px-4 py-2.5 border-b border-border gap-2">
      <span className="flex min-w-0 flex-1 items-start gap-2">
        {conversation.type === 'contact' && onOpenContactInfo && (
          <button
            type="button"
            className="avatar-action-button flex-shrink-0 cursor-pointer rounded-full border-none bg-transparent p-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => onOpenContactInfo(conversation.id)}
            title="View contact info"
            aria-label={`View info for ${conversation.name}`}
          >
            <ContactAvatar
              name={conversation.name}
              publicKey={conversation.id}
              size={28}
              contactType={contacts.find((c) => c.public_key === conversation.id)?.type}
              clickable
            />
          </button>
        )}
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="flex min-w-0 flex-1 items-baseline gap-2 whitespace-nowrap">
              <h2 className="min-w-0 shrink font-semibold text-base">
                {titleClickable ? (
                  <button
                    type="button"
                    className="flex min-w-0 shrink items-center gap-1.5 text-left hover:text-primary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
                    aria-label={`View info for ${conversation.name}`}
                    onClick={handleOpenConversationInfo}
                  >
                    <span className="truncate">
                      {conversation.type === 'channel' &&
                      !conversation.name.startsWith('#') &&
                      activeChannel?.is_hashtag
                        ? '#'
                        : ''}
                      {conversation.name}
                    </span>
                    <Info
                      className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground/80"
                      aria-hidden="true"
                    />
                  </button>
                ) : (
                  <span className="truncate">
                    {conversation.type === 'channel' &&
                    !conversation.name.startsWith('#') &&
                    activeChannel?.is_hashtag
                      ? '#'
                      : ''}
                    {conversation.name}
                  </span>
                )}
              </h2>
              {isPrivateChannel && !showKey ? (
                <button
                  className="min-w-0 flex-shrink text-[11px] font-mono text-muted-foreground transition-colors hover:text-primary"
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowKey(true);
                  }}
                  title="Reveal channel key"
                >
                  Show Key
                </button>
              ) : (
                <span
                  ref={keyTextRef}
                  className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground transition-colors hover:text-primary"
                  role="button"
                  tabIndex={0}
                  onKeyDown={handleKeyboardActivate}
                  onClick={(e) => {
                    e.stopPropagation();
                    navigator.clipboard.writeText(conversation.id);
                    toast.success(
                      conversation.type === 'channel' ? 'Room key copied!' : 'Contact key copied!'
                    );
                  }}
                  title="Click to copy"
                  aria-label={
                    conversation.type === 'channel' ? 'Copy channel key' : 'Copy contact key'
                  }
                >
                  {conversation.type === 'channel'
                    ? conversation.id.toLowerCase()
                    : conversation.id}
                </span>
              )}
            </span>
            {conversation.type === 'contact' && activeContact && contactStatusInline && (
              <span className="min-w-0 flex-none text-[11px] text-muted-foreground">
                <ContactStatusInfo
                  contact={activeContact}
                  ourLat={config?.lat ?? null}
                  ourLon={config?.lon ?? null}
                />
              </span>
            )}
          </span>
          {conversation.type === 'contact' && activeContact && !contactStatusInline && (
            <span className="mt-0.5 min-w-0 text-[11px] text-muted-foreground">
              <ContactStatusInfo
                contact={activeContact}
                ourLat={config?.lat ?? null}
                ourLon={config?.lon ?? null}
              />
            </span>
          )}
          {conversation.type === 'channel' && activeFloodScopeDisplay && (
            <button
              className="mt-0.5 flex items-center gap-1 text-left sm:hidden"
              onClick={handleEditFloodScopeOverride}
              title="Set regional override"
              aria-label="Set regional override"
            >
              <Globe2
                className="h-3.5 w-3.5 flex-shrink-0 text-[hsl(var(--region-override))]"
                aria-hidden="true"
              />
              <span className="min-w-0 truncate text-[11px] font-medium text-[hsl(var(--region-override))]">
                {activeFloodScopeDisplay}
              </span>
            </button>
          )}
        </span>
      </span>
      <div className="flex items-center justify-end gap-0.5 flex-shrink-0">
        {conversation.type === 'contact' && (
          <button
            className="p-1 rounded hover:bg-accent text-lg leading-none transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={onTrace}
            title={
              activeContactIsPrefixOnly
                ? 'Direct Trace unavailable until the full contact key is known'
                : 'Direct Trace'
            }
            aria-label="Direct Trace"
            disabled={activeContactIsPrefixOnly}
          >
            <DirectTraceIcon className="h-4 w-4 text-muted-foreground" />
          </button>
        )}
        {notificationsSupported && (
          <button
            className="flex items-center gap-1 rounded px-1 py-1 hover:bg-accent text-lg leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={onToggleNotifications}
            title={
              notificationsEnabled
                ? 'Disable desktop notifications for this conversation'
                : notificationsPermission === 'denied'
                  ? 'Notifications blocked by the browser'
                  : 'Enable desktop notifications for this conversation'
            }
            aria-label={
              notificationsEnabled
                ? 'Disable notifications for this conversation'
                : 'Enable notifications for this conversation'
            }
          >
            <Bell
              className={`h-4 w-4 ${notificationsEnabled ? 'text-status-connected' : 'text-muted-foreground'}`}
              fill={notificationsEnabled ? 'currentColor' : 'none'}
              aria-hidden="true"
            />
            {notificationsEnabled && (
              <span className="hidden md:inline text-[11px] font-medium text-status-connected">
                Notifications On
              </span>
            )}
          </button>
        )}
        {conversation.type === 'channel' && onSetChannelFloodScopeOverride && (
          <button
            className="flex shrink-0 items-center gap-1 rounded px-1 py-1 text-lg leading-none transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={handleEditFloodScopeOverride}
            title="Set regional override"
            aria-label="Set regional override"
          >
            <Globe2
              className={`h-4 w-4 ${activeFloodScopeLabel ? 'text-[hsl(var(--region-override))]' : 'text-muted-foreground'}`}
              aria-hidden="true"
            />
            {activeFloodScopeDisplay && (
              <span className="hidden text-[11px] font-medium text-[hsl(var(--region-override))] sm:inline">
                {activeFloodScopeDisplay}
              </span>
            )}
          </button>
        )}
        {(conversation.type === 'channel' || conversation.type === 'contact') && (
          <button
            className="p-1 rounded hover:bg-accent text-lg leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() =>
              onToggleFavorite(conversation.type as 'channel' | 'contact', conversation.id)
            }
            title={favoriteTitle}
            aria-label={
              isFavorite(favorites, conversation.type as 'channel' | 'contact', conversation.id)
                ? 'Remove from favorites'
                : 'Add to favorites'
            }
          >
            {isFavorite(favorites, conversation.type as 'channel' | 'contact', conversation.id) ? (
              <Star className="h-4 w-4 fill-current text-favorite" aria-hidden="true" />
            ) : (
              <Star className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            )}
          </button>
        )}
        {!(conversation.type === 'channel' && conversation.name === 'Public') && (
          <button
            className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive text-lg leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => {
              if (conversation.type === 'channel') {
                onDeleteChannel(conversation.id);
              } else {
                onDeleteContact(conversation.id);
              }
            }}
            title="Delete"
            aria-label="Delete"
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
          </button>
        )}
      </div>
    </header>
  );
}
