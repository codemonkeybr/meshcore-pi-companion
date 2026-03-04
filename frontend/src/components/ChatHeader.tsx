import { useEffect, useState } from 'react';
import { toast } from './ui/sonner';
import { isFavorite } from '../utils/favorites';
import { handleKeyboardActivate } from '../utils/a11y';
import { ContactAvatar } from './ContactAvatar';
import { ContactStatusInfo } from './ContactStatusInfo';
import type { Channel, Contact, Conversation, Favorite, RadioConfig } from '../types';

interface ChatHeaderProps {
  conversation: Conversation;
  contacts: Contact[];
  channels: Channel[];
  config: RadioConfig | null;
  favorites: Favorite[];
  onTrace: () => void;
  onToggleFavorite: (type: 'channel' | 'contact', id: string) => void;
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
  onTrace,
  onToggleFavorite,
  onDeleteChannel,
  onDeleteContact,
  onOpenContactInfo,
  onOpenChannelInfo,
}: ChatHeaderProps) {
  const [showKey, setShowKey] = useState(false);

  useEffect(() => {
    setShowKey(false);
  }, [conversation.id]);

  const isPrivateChannel =
    conversation.type === 'channel' && !channels.find((c) => c.key === conversation.id)?.is_hashtag;

  const titleClickable =
    (conversation.type === 'contact' && onOpenContactInfo) ||
    (conversation.type === 'channel' && onOpenChannelInfo);
  return (
    <header className="flex justify-between items-center px-4 py-2.5 border-b border-border gap-2">
      <span className="flex flex-wrap items-baseline gap-x-2 min-w-0 flex-1">
        {conversation.type === 'contact' && onOpenContactInfo && (
          <span
            className="flex-shrink-0 cursor-pointer"
            role="button"
            tabIndex={0}
            onKeyDown={handleKeyboardActivate}
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
          </span>
        )}
        <h2
          className={`flex-shrink-0 font-semibold text-base ${titleClickable ? 'cursor-pointer hover:text-primary transition-colors' : ''}`}
          role={titleClickable ? 'button' : undefined}
          tabIndex={titleClickable ? 0 : undefined}
          aria-label={titleClickable ? `View info for ${conversation.name}` : undefined}
          onKeyDown={titleClickable ? handleKeyboardActivate : undefined}
          onClick={
            titleClickable
              ? () => {
                  if (conversation.type === 'contact' && onOpenContactInfo) {
                    onOpenContactInfo(conversation.id);
                  } else if (conversation.type === 'channel' && onOpenChannelInfo) {
                    onOpenChannelInfo(conversation.id);
                  }
                }
              : undefined
          }
        >
          {conversation.type === 'channel' &&
          !conversation.name.startsWith('#') &&
          channels.find((c) => c.key === conversation.id)?.is_hashtag
            ? '#'
            : ''}
          {conversation.name}
        </h2>
        {isPrivateChannel && !showKey ? (
          <button
            className="font-normal text-[11px] text-muted-foreground font-mono hover:text-primary transition-colors"
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
            className="font-normal text-[11px] text-muted-foreground font-mono truncate cursor-pointer hover:text-primary transition-colors"
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
          >
            {conversation.type === 'channel' ? conversation.id.toLowerCase() : conversation.id}
          </span>
        )}
        {conversation.type === 'contact' &&
          (() => {
            const contact = contacts.find((c) => c.public_key === conversation.id);
            if (!contact) return null;
            return (
              <ContactStatusInfo
                contact={contact}
                ourLat={config?.lat ?? null}
                ourLon={config?.lon ?? null}
              />
            );
          })()}
      </span>
      <div className="flex items-center gap-0.5 flex-shrink-0">
        {/* Direct trace button (contacts only) */}
        {conversation.type === 'contact' && (
          <button
            className="p-1.5 rounded hover:bg-accent text-lg leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={onTrace}
            title="Direct Trace"
            aria-label="Direct Trace"
          >
            <span aria-hidden="true">&#x1F6CE;</span>
          </button>
        )}
        {/* Favorite button */}
        {(conversation.type === 'channel' || conversation.type === 'contact') && (
          <button
            className="p-1.5 rounded hover:bg-accent text-lg leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() =>
              onToggleFavorite(conversation.type as 'channel' | 'contact', conversation.id)
            }
            title={
              isFavorite(favorites, conversation.type as 'channel' | 'contact', conversation.id)
                ? 'Remove from favorites'
                : 'Add to favorites'
            }
            aria-label={
              isFavorite(favorites, conversation.type as 'channel' | 'contact', conversation.id)
                ? 'Remove from favorites'
                : 'Add to favorites'
            }
          >
            {isFavorite(favorites, conversation.type as 'channel' | 'contact', conversation.id) ? (
              <span className="text-favorite">&#9733;</span>
            ) : (
              <span className="text-muted-foreground">&#9734;</span>
            )}
          </button>
        )}
        {/* Delete button */}
        {!(conversation.type === 'channel' && conversation.name === 'Public') && (
          <button
            className="p-1.5 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive text-lg leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
            <span aria-hidden="true">&#128465;</span>
          </button>
        )}
      </div>
    </header>
  );
}
