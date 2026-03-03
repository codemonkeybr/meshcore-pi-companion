import { toast } from './ui/sonner';
import { isFavorite } from '../utils/favorites';
import { ContactAvatar } from './ContactAvatar';
import { ContactStatusInfo } from './ContactStatusInfo';
import type { Contact, Conversation, Favorite, RadioConfig } from '../types';

interface ChatHeaderProps {
  conversation: Conversation;
  contacts: Contact[];
  config: RadioConfig | null;
  favorites: Favorite[];
  onTrace: () => void;
  onToggleFavorite: (type: 'channel' | 'contact', id: string) => void;
  onDeleteChannel: (key: string) => void;
  onDeleteContact: (publicKey: string) => void;
  onOpenContactInfo?: (publicKey: string) => void;
}

export function ChatHeader({
  conversation,
  contacts,
  config,
  favorites,
  onTrace,
  onToggleFavorite,
  onDeleteChannel,
  onDeleteContact,
  onOpenContactInfo,
}: ChatHeaderProps) {
  return (
    <div className="flex justify-between items-center px-4 py-2.5 border-b border-border gap-2">
      <span className="flex flex-wrap items-center gap-x-2 min-w-0 flex-1">
        {conversation.type === 'contact' && onOpenContactInfo && (
          <span
            className="flex-shrink-0 cursor-pointer"
            onClick={() => onOpenContactInfo(conversation.id)}
            title="View contact info"
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
        <span
          className={`flex-shrink-0 font-semibold text-base ${conversation.type === 'contact' && onOpenContactInfo ? 'cursor-pointer hover:text-primary transition-colors' : ''}`}
          onClick={
            conversation.type === 'contact' && onOpenContactInfo
              ? () => onOpenContactInfo(conversation.id)
              : undefined
          }
        >
          {conversation.type === 'channel' &&
          !conversation.name.startsWith('#') &&
          conversation.name !== 'Public'
            ? '#'
            : ''}
          {conversation.name}
        </span>
        <span
          className="font-normal text-[11px] text-muted-foreground font-mono truncate cursor-pointer hover:text-primary transition-colors"
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
            className="p-1.5 rounded hover:bg-accent text-lg leading-none transition-colors"
            onClick={onTrace}
            title="Direct Trace"
          >
            &#x1F6CE;
          </button>
        )}
        {/* Favorite button */}
        {(conversation.type === 'channel' || conversation.type === 'contact') && (
          <button
            className="p-1.5 rounded hover:bg-accent text-lg leading-none transition-colors"
            onClick={() =>
              onToggleFavorite(conversation.type as 'channel' | 'contact', conversation.id)
            }
            title={
              isFavorite(favorites, conversation.type as 'channel' | 'contact', conversation.id)
                ? 'Remove from favorites'
                : 'Add to favorites'
            }
          >
            {isFavorite(favorites, conversation.type as 'channel' | 'contact', conversation.id) ? (
              <span className="text-amber-400">&#9733;</span>
            ) : (
              <span className="text-muted-foreground">&#9734;</span>
            )}
          </button>
        )}
        {/* Delete button */}
        {!(conversation.type === 'channel' && conversation.name === 'Public') && (
          <button
            className="p-1.5 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive text-lg leading-none transition-colors"
            onClick={() => {
              if (conversation.type === 'channel') {
                onDeleteChannel(conversation.id);
              } else {
                onDeleteContact(conversation.id);
              }
            }}
            title="Delete"
          >
            &#128465;
          </button>
        )}
      </div>
    </div>
  );
}
