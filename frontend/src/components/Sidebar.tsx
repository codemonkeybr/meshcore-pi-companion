import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Bell,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  LockOpen,
  Logs,
  Map,
  Search as SearchIcon,
  SquarePen,
  Waypoints,
  X,
} from 'lucide-react';
import {
  CONTACT_TYPE_REPEATER,
  type Contact,
  type Channel,
  type Conversation,
  type Favorite,
} from '../types';
import { getStateKey, type ConversationTimes, type SortOrder } from '../utils/conversationState';
import { getContactDisplayName } from '../utils/pubkey';
import { handleKeyboardActivate } from '../utils/a11y';
import { ContactAvatar } from './ContactAvatar';
import { isFavorite } from '../utils/favorites';
import { Input } from './ui/input';
import { Button } from './ui/button';
import { cn } from '@/lib/utils';

type FavoriteItem = { type: 'channel'; channel: Channel } | { type: 'contact'; contact: Contact };

type ConversationRow = {
  key: string;
  type: 'channel' | 'contact';
  id: string;
  name: string;
  unreadCount: number;
  isMention: boolean;
  notificationsEnabled: boolean;
  contact?: Contact;
};

type CollapseState = {
  tools: boolean;
  favorites: boolean;
  channels: boolean;
  contacts: boolean;
  repeaters: boolean;
};

const SIDEBAR_COLLAPSE_STATE_KEY = 'remoteterm-sidebar-collapse-state';

const DEFAULT_COLLAPSE_STATE: CollapseState = {
  tools: false,
  favorites: false,
  channels: false,
  contacts: false,
  repeaters: false,
};

function loadCollapsedState(): CollapseState {
  try {
    const raw = localStorage.getItem(SIDEBAR_COLLAPSE_STATE_KEY);
    if (!raw) return DEFAULT_COLLAPSE_STATE;
    const parsed = JSON.parse(raw) as Partial<CollapseState>;
    return {
      tools: parsed.tools ?? DEFAULT_COLLAPSE_STATE.tools,
      favorites: parsed.favorites ?? DEFAULT_COLLAPSE_STATE.favorites,
      channels: parsed.channels ?? DEFAULT_COLLAPSE_STATE.channels,
      contacts: parsed.contacts ?? DEFAULT_COLLAPSE_STATE.contacts,
      repeaters: parsed.repeaters ?? DEFAULT_COLLAPSE_STATE.repeaters,
    };
  } catch {
    return DEFAULT_COLLAPSE_STATE;
  }
}

interface SidebarProps {
  contacts: Contact[];
  channels: Channel[];
  activeConversation: Conversation | null;
  onSelectConversation: (conversation: Conversation) => void;
  onNewMessage: () => void;
  lastMessageTimes: ConversationTimes;
  unreadCounts: Record<string, number>;
  /** Tracks which conversations have unread messages that mention the user */
  mentions: Record<string, boolean>;
  showCracker: boolean;
  crackerRunning: boolean;
  onToggleCracker: () => void;
  onMarkAllRead: () => void;
  favorites: Favorite[];
  /** Sort order from server settings */
  sortOrder?: SortOrder;
  /** Callback when sort order changes */
  onSortOrderChange?: (order: SortOrder) => void;
  isConversationNotificationsEnabled?: (type: 'channel' | 'contact', id: string) => boolean;
}

export function Sidebar({
  contacts,
  channels,
  activeConversation,
  onSelectConversation,
  onNewMessage,
  lastMessageTimes,
  unreadCounts,
  mentions,
  showCracker,
  crackerRunning,
  onToggleCracker,
  onMarkAllRead,
  favorites,
  sortOrder: sortOrderProp = 'recent',
  onSortOrderChange,
  isConversationNotificationsEnabled,
}: SidebarProps) {
  const sortOrder = sortOrderProp;
  const [searchQuery, setSearchQuery] = useState('');
  const initialCollapsedState = useMemo(loadCollapsedState, []);
  const [toolsCollapsed, setToolsCollapsed] = useState(initialCollapsedState.tools);
  const [favoritesCollapsed, setFavoritesCollapsed] = useState(initialCollapsedState.favorites);
  const [channelsCollapsed, setChannelsCollapsed] = useState(initialCollapsedState.channels);
  const [contactsCollapsed, setContactsCollapsed] = useState(initialCollapsedState.contacts);
  const [repeatersCollapsed, setRepeatersCollapsed] = useState(initialCollapsedState.repeaters);
  const collapseSnapshotRef = useRef<CollapseState | null>(null);

  const handleSortToggle = () => {
    const newOrder = sortOrder === 'alpha' ? 'recent' : 'alpha';
    onSortOrderChange?.(newOrder);
  };

  const handleSelectConversation = (conversation: Conversation) => {
    setSearchQuery('');
    onSelectConversation(conversation);
  };

  const isActive = (
    type: 'contact' | 'channel' | 'raw' | 'map' | 'visualizer' | 'search',
    id: string
  ) => activeConversation?.type === type && activeConversation?.id === id;

  // Get unread count for a conversation
  const getUnreadCount = (type: 'channel' | 'contact', id: string): number => {
    const key = getStateKey(type, id);
    return unreadCounts[key] || 0;
  };

  // Check if a conversation has a mention
  const hasMention = (type: 'channel' | 'contact', id: string): boolean => {
    const key = getStateKey(type, id);
    return mentions[key] || false;
  };

  const getLastMessageTime = useCallback(
    (type: 'channel' | 'contact', id: string) => {
      const key = getStateKey(type, id);
      return lastMessageTimes[key] || 0;
    },
    [lastMessageTimes]
  );

  // Deduplicate channels by key only.
  // Channel names are not unique; distinct keys must remain visible.
  const uniqueChannels = useMemo(
    () =>
      channels.reduce<Channel[]>((acc, channel) => {
        if (!acc.some((c) => c.key === channel.key)) {
          acc.push(channel);
        }
        return acc;
      }, []),
    [channels]
  );

  // Deduplicate contacts by public key, preferring ones with names
  // Also filter out any contacts with empty public keys
  const uniqueContacts = useMemo(
    () =>
      contacts
        .filter((c) => c.public_key && c.public_key.length > 0)
        .sort((a, b) => {
          // Sort contacts with names first
          if (a.name && !b.name) return -1;
          if (!a.name && b.name) return 1;
          return (a.name || '').localeCompare(b.name || '');
        })
        .reduce<Contact[]>((acc, contact) => {
          if (!acc.some((c) => c.public_key === contact.public_key)) {
            acc.push(contact);
          }
          return acc;
        }, []),
    [contacts]
  );

  // Sort channels based on sort order, with Public always first
  const sortedChannels = useMemo(
    () =>
      [...uniqueChannels].sort((a, b) => {
        // Public channel always sorts to the top
        if (a.name === 'Public') return -1;
        if (b.name === 'Public') return 1;

        if (sortOrder === 'recent') {
          const timeA = getLastMessageTime('channel', a.key);
          const timeB = getLastMessageTime('channel', b.key);
          if (timeA && timeB) return timeB - timeA;
          if (timeA && !timeB) return -1;
          if (!timeA && timeB) return 1;
        }
        return a.name.localeCompare(b.name);
      }),
    [uniqueChannels, sortOrder, getLastMessageTime]
  );

  const sortContactsByOrder = useCallback(
    (items: Contact[]) =>
      [...items].sort((a, b) => {
        if (sortOrder === 'recent') {
          const timeA = getLastMessageTime('contact', a.public_key);
          const timeB = getLastMessageTime('contact', b.public_key);
          if (timeA && timeB) return timeB - timeA;
          if (timeA && !timeB) return -1;
          if (!timeA && timeB) return 1;
        }
        return (a.name || a.public_key).localeCompare(b.name || b.public_key);
      }),
    [sortOrder, getLastMessageTime]
  );

  // Split non-repeater contacts and repeater contacts into separate sorted lists
  const sortedNonRepeaterContacts = useMemo(
    () => sortContactsByOrder(uniqueContacts.filter((c) => c.type !== CONTACT_TYPE_REPEATER)),
    [uniqueContacts, sortContactsByOrder]
  );

  const sortedRepeaters = useMemo(
    () => sortContactsByOrder(uniqueContacts.filter((c) => c.type === CONTACT_TYPE_REPEATER)),
    [uniqueContacts, sortContactsByOrder]
  );

  // Filter by search query
  const query = searchQuery.toLowerCase().trim();
  const isSearching = query.length > 0;

  const filteredChannels = useMemo(
    () =>
      query
        ? sortedChannels.filter(
            (c) => c.name.toLowerCase().includes(query) || c.key.toLowerCase().includes(query)
          )
        : sortedChannels,
    [sortedChannels, query]
  );

  const filteredNonRepeaterContacts = useMemo(
    () =>
      query
        ? sortedNonRepeaterContacts.filter(
            (c) =>
              c.name?.toLowerCase().includes(query) || c.public_key.toLowerCase().includes(query)
          )
        : sortedNonRepeaterContacts,
    [sortedNonRepeaterContacts, query]
  );

  const filteredRepeaters = useMemo(
    () =>
      query
        ? sortedRepeaters.filter(
            (c) =>
              c.name?.toLowerCase().includes(query) || c.public_key.toLowerCase().includes(query)
          )
        : sortedRepeaters,
    [sortedRepeaters, query]
  );

  // Expand sections while searching; restore prior collapse state when search ends.
  useEffect(() => {
    if (isSearching) {
      if (!collapseSnapshotRef.current) {
        collapseSnapshotRef.current = {
          tools: toolsCollapsed,
          favorites: favoritesCollapsed,
          channels: channelsCollapsed,
          contacts: contactsCollapsed,
          repeaters: repeatersCollapsed,
        };
      }

      if (
        toolsCollapsed ||
        favoritesCollapsed ||
        channelsCollapsed ||
        contactsCollapsed ||
        repeatersCollapsed
      ) {
        setToolsCollapsed(false);
        setFavoritesCollapsed(false);
        setChannelsCollapsed(false);
        setContactsCollapsed(false);
        setRepeatersCollapsed(false);
      }
      return;
    }

    if (collapseSnapshotRef.current) {
      const prev = collapseSnapshotRef.current;
      collapseSnapshotRef.current = null;
      setToolsCollapsed(prev.tools);
      setFavoritesCollapsed(prev.favorites);
      setChannelsCollapsed(prev.channels);
      setContactsCollapsed(prev.contacts);
      setRepeatersCollapsed(prev.repeaters);
    }
  }, [
    isSearching,
    toolsCollapsed,
    favoritesCollapsed,
    channelsCollapsed,
    contactsCollapsed,
    repeatersCollapsed,
  ]);

  useEffect(() => {
    if (isSearching) return;

    const state: CollapseState = {
      tools: toolsCollapsed,
      favorites: favoritesCollapsed,
      channels: channelsCollapsed,
      contacts: contactsCollapsed,
      repeaters: repeatersCollapsed,
    };

    try {
      localStorage.setItem(SIDEBAR_COLLAPSE_STATE_KEY, JSON.stringify(state));
    } catch {
      // Ignore localStorage write failures (e.g., disabled storage)
    }
  }, [
    isSearching,
    toolsCollapsed,
    favoritesCollapsed,
    channelsCollapsed,
    contactsCollapsed,
    repeatersCollapsed,
  ]);

  // Separate favorites from regular items, and build combined favorites list
  const { favoriteItems, nonFavoriteChannels, nonFavoriteContacts, nonFavoriteRepeaters } =
    useMemo(() => {
      const favChannels = filteredChannels.filter((c) => isFavorite(favorites, 'channel', c.key));
      const favContacts = [...filteredNonRepeaterContacts, ...filteredRepeaters].filter((c) =>
        isFavorite(favorites, 'contact', c.public_key)
      );
      const nonFavChannels = filteredChannels.filter(
        (c) => !isFavorite(favorites, 'channel', c.key)
      );
      const nonFavContacts = filteredNonRepeaterContacts.filter(
        (c) => !isFavorite(favorites, 'contact', c.public_key)
      );
      const nonFavRepeaters = filteredRepeaters.filter(
        (c) => !isFavorite(favorites, 'contact', c.public_key)
      );

      const items: FavoriteItem[] = [
        ...favChannels.map((channel) => ({ type: 'channel' as const, channel })),
        ...favContacts.map((contact) => ({ type: 'contact' as const, contact })),
      ].sort((a, b) => {
        const timeA =
          a.type === 'channel'
            ? getLastMessageTime('channel', a.channel.key)
            : getLastMessageTime('contact', a.contact.public_key);
        const timeB =
          b.type === 'channel'
            ? getLastMessageTime('channel', b.channel.key)
            : getLastMessageTime('contact', b.contact.public_key);
        if (timeA && timeB) return timeB - timeA;
        if (timeA && !timeB) return -1;
        if (!timeA && timeB) return 1;
        const nameA =
          a.type === 'channel' ? a.channel.name : a.contact.name || a.contact.public_key;
        const nameB =
          b.type === 'channel' ? b.channel.name : b.contact.name || b.contact.public_key;
        return nameA.localeCompare(nameB);
      });

      return {
        favoriteItems: items,
        nonFavoriteChannels: nonFavChannels,
        nonFavoriteContacts: nonFavContacts,
        nonFavoriteRepeaters: nonFavRepeaters,
      };
    }, [
      filteredChannels,
      filteredNonRepeaterContacts,
      filteredRepeaters,
      favorites,
      getLastMessageTime,
    ]);

  const buildChannelRow = (channel: Channel, keyPrefix: string): ConversationRow => ({
    key: `${keyPrefix}-${channel.key}`,
    type: 'channel',
    id: channel.key,
    name: channel.name,
    unreadCount: getUnreadCount('channel', channel.key),
    isMention: hasMention('channel', channel.key),
    notificationsEnabled: isConversationNotificationsEnabled?.('channel', channel.key) ?? false,
  });

  const buildContactRow = (contact: Contact, keyPrefix: string): ConversationRow => ({
    key: `${keyPrefix}-${contact.public_key}`,
    type: 'contact',
    id: contact.public_key,
    name: getContactDisplayName(contact.name, contact.public_key, contact.last_advert),
    unreadCount: getUnreadCount('contact', contact.public_key),
    isMention: hasMention('contact', contact.public_key),
    notificationsEnabled:
      isConversationNotificationsEnabled?.('contact', contact.public_key) ?? false,
    contact,
  });

  const renderConversationRow = (row: ConversationRow) => (
    <div
      key={row.key}
      className={cn(
        'px-3 py-2 cursor-pointer flex items-center gap-2 border-l-2 border-transparent hover:bg-accent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        isActive(row.type, row.id) && 'bg-accent border-l-primary',
        row.unreadCount > 0 && '[&_.name]:font-semibold [&_.name]:text-foreground'
      )}
      role="button"
      tabIndex={0}
      aria-current={isActive(row.type, row.id) ? 'page' : undefined}
      onKeyDown={handleKeyboardActivate}
      onClick={() =>
        handleSelectConversation({
          type: row.type,
          id: row.id,
          name: row.name,
        })
      }
    >
      {row.type === 'contact' && row.contact && (
        <ContactAvatar
          name={row.contact.name}
          publicKey={row.contact.public_key}
          size={24}
          contactType={row.contact.type}
        />
      )}
      <span className="name flex-1 truncate text-[13px]">{row.name}</span>
      <span className="ml-auto flex items-center gap-1">
        {row.notificationsEnabled && (
          <span aria-label="Notifications enabled" title="Notifications enabled">
            <Bell className="h-3.5 w-3.5 text-muted-foreground" />
          </span>
        )}
        {row.unreadCount > 0 && (
          <span
            className={cn(
              'text-[10px] font-semibold px-1.5 py-0.5 rounded-full min-w-[18px] text-center',
              row.isMention
                ? 'bg-badge-mention text-badge-mention-foreground'
                : 'bg-badge-unread/90 text-badge-unread-foreground'
            )}
            aria-label={`${row.unreadCount} unread message${row.unreadCount !== 1 ? 's' : ''}`}
          >
            {row.unreadCount}
          </span>
        )}
      </span>
    </div>
  );

  const renderSidebarActionRow = ({
    key,
    active = false,
    icon,
    label,
    onClick,
  }: {
    key: string;
    active?: boolean;
    icon: React.ReactNode;
    label: React.ReactNode;
    onClick: () => void;
  }) => (
    <div
      key={key}
      className={cn(
        'px-3 py-2 cursor-pointer flex items-center gap-2 border-l-2 border-transparent hover:bg-accent transition-colors text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        active && 'bg-accent border-l-primary'
      )}
      role="button"
      tabIndex={0}
      aria-current={active ? 'page' : undefined}
      onKeyDown={handleKeyboardActivate}
      onClick={onClick}
    >
      <span className="sidebar-tool-icon text-muted-foreground" aria-hidden="true">
        {icon}
      </span>
      <span className="sidebar-tool-label flex-1 truncate text-muted-foreground">{label}</span>
    </div>
  );

  const getSectionUnreadCount = (rows: ConversationRow[]): number =>
    rows.reduce((total, row) => total + row.unreadCount, 0);

  const sectionHasMention = (rows: ConversationRow[]): boolean => rows.some((row) => row.isMention);

  const favoriteRows = favoriteItems.map((item) =>
    item.type === 'channel'
      ? buildChannelRow(item.channel, 'fav-chan')
      : buildContactRow(item.contact, 'fav-contact')
  );
  const channelRows = nonFavoriteChannels.map((channel) => buildChannelRow(channel, 'chan'));
  const contactRows = nonFavoriteContacts.map((contact) => buildContactRow(contact, 'contact'));
  const repeaterRows = nonFavoriteRepeaters.map((contact) => buildContactRow(contact, 'repeater'));

  const favoritesUnreadCount = getSectionUnreadCount(favoriteRows);
  const channelsUnreadCount = getSectionUnreadCount(channelRows);
  const contactsUnreadCount = getSectionUnreadCount(contactRows);
  const repeatersUnreadCount = getSectionUnreadCount(repeaterRows);
  const favoritesHasMention = sectionHasMention(favoriteRows);
  const channelsHasMention = sectionHasMention(channelRows);
  const toolRows = !query
    ? [
        renderSidebarActionRow({
          key: 'tool-raw',
          active: isActive('raw', 'raw'),
          icon: <Logs className="h-4 w-4" />,
          label: 'Packet Feed',
          onClick: () =>
            handleSelectConversation({
              type: 'raw',
              id: 'raw',
              name: 'Raw Packet Feed',
            }),
        }),
        renderSidebarActionRow({
          key: 'tool-map',
          active: isActive('map', 'map'),
          icon: <Map className="h-4 w-4" />,
          label: 'Node Map',
          onClick: () =>
            handleSelectConversation({
              type: 'map',
              id: 'map',
              name: 'Node Map',
            }),
        }),
        renderSidebarActionRow({
          key: 'tool-visualizer',
          active: isActive('visualizer', 'visualizer'),
          icon: <Waypoints className="h-4 w-4" />,
          label: 'Mesh Visualizer',
          onClick: () =>
            handleSelectConversation({
              type: 'visualizer',
              id: 'visualizer',
              name: 'Mesh Visualizer',
            }),
        }),
        renderSidebarActionRow({
          key: 'tool-search',
          active: isActive('search', 'search'),
          icon: <SearchIcon className="h-4 w-4" />,
          label: 'Message Search',
          onClick: () =>
            handleSelectConversation({
              type: 'search',
              id: 'search',
              name: 'Message Search',
            }),
        }),
        renderSidebarActionRow({
          key: 'tool-cracker',
          active: showCracker,
          icon: <LockOpen className="h-4 w-4" />,
          label: (
            <>
              {showCracker ? 'Hide' : 'Show'} Room Finder
              <span
                className={cn(
                  'ml-1 text-[11px]',
                  crackerRunning ? 'text-primary' : 'text-muted-foreground'
                )}
              >
                ({crackerRunning ? 'running' : 'idle'})
              </span>
            </>
          ),
          onClick: onToggleCracker,
        }),
      ]
    : [];

  const renderSectionHeader = (
    title: string,
    collapsed: boolean,
    onToggle: () => void,
    showSortToggle = false,
    unreadCount = 0,
    highlightUnread = false
  ) => {
    const effectiveCollapsed = isSearching ? false : collapsed;

    return (
      <div className="flex justify-between items-center px-3 py-2 pt-3.5">
        <button
          className={cn(
            'flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded',
            isSearching && 'cursor-default'
          )}
          aria-expanded={!effectiveCollapsed}
          onClick={() => {
            if (!isSearching) onToggle();
          }}
          title={effectiveCollapsed ? `Expand ${title}` : `Collapse ${title}`}
        >
          {effectiveCollapsed ? (
            <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          <span>{title}</span>
        </button>
        {(showSortToggle || unreadCount > 0) && (
          <div className="ml-auto flex items-center gap-1.5">
            {showSortToggle && (
              <button
                className="bg-transparent text-muted-foreground/60 px-1 py-0.5 text-[10px] rounded hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={handleSortToggle}
                aria-label={sortOrder === 'alpha' ? 'Sort by recent' : 'Sort alphabetically'}
                title={sortOrder === 'alpha' ? 'Sort by recent' : 'Sort alphabetically'}
              >
                {sortOrder === 'alpha' ? 'A-Z' : '⏱'}
              </button>
            )}
            {unreadCount > 0 && (
              <span
                className={cn(
                  'text-[10px] font-medium px-1.5 py-0.5 rounded-full',
                  highlightUnread
                    ? 'bg-badge-mention text-badge-mention-foreground'
                    : 'bg-secondary text-muted-foreground'
                )}
                aria-label={`${unreadCount} unread`}
              >
                {unreadCount}
              </span>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <nav
      className="sidebar w-60 h-full min-h-0 overflow-hidden bg-card border-r border-border flex flex-col"
      aria-label="Conversations"
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
        <div className="relative min-w-0 flex-1">
          <Input
            type="text"
            placeholder="Search rooms/contacts..."
            aria-label="Search conversations"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className={cn('h-7 text-[13px] bg-background/50', searchQuery ? 'pr-8' : 'pr-3')}
          />
          {searchQuery && (
            <button
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground text-lg leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
              onClick={() => setSearchQuery('')}
              title="Clear search"
              aria-label="Clear search"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={onNewMessage}
          title="New Message"
          aria-label="New message"
          className="h-7 w-7 shrink-0 p-0 text-muted-foreground hover:text-foreground transition-colors"
        >
          <SquarePen className="h-4 w-4" />
        </Button>
      </div>

      {/* List */}
      <div className="flex-1 min-h-0 overflow-y-auto [contain:layout_paint]">
        {/* Tools */}
        {toolRows.length > 0 && (
          <>
            {renderSectionHeader('Tools', toolsCollapsed, () => setToolsCollapsed((prev) => !prev))}
            {(isSearching || !toolsCollapsed) && toolRows}
          </>
        )}

        {/* Mark All Read */}
        {!query && Object.values(unreadCounts).some((c) => c > 0) && (
          <div
            className="px-3 py-2 cursor-pointer flex items-center gap-2 border-l-2 border-transparent hover:bg-accent transition-colors text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            role="button"
            tabIndex={0}
            onKeyDown={handleKeyboardActivate}
            onClick={onMarkAllRead}
          >
            <CheckCheck className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            <span className="flex-1 truncate text-muted-foreground">Mark all as read</span>
          </div>
        )}

        {/* Favorites */}
        {favoriteItems.length > 0 && (
          <>
            {renderSectionHeader(
              'Favorites',
              favoritesCollapsed,
              () => setFavoritesCollapsed((prev) => !prev),
              false,
              favoritesUnreadCount,
              favoritesHasMention
            )}
            {(isSearching || !favoritesCollapsed) &&
              favoriteRows.map((row) => renderConversationRow(row))}
          </>
        )}

        {/* Channels */}
        {nonFavoriteChannels.length > 0 && (
          <>
            {renderSectionHeader(
              'Channels',
              channelsCollapsed,
              () => setChannelsCollapsed((prev) => !prev),
              true,
              channelsUnreadCount,
              channelsHasMention
            )}
            {(isSearching || !channelsCollapsed) &&
              channelRows.map((row) => renderConversationRow(row))}
          </>
        )}

        {/* Contacts */}
        {nonFavoriteContacts.length > 0 && (
          <>
            {renderSectionHeader(
              'Contacts',
              contactsCollapsed,
              () => setContactsCollapsed((prev) => !prev),
              true,
              contactsUnreadCount,
              contactsUnreadCount > 0
            )}
            {(isSearching || !contactsCollapsed) &&
              contactRows.map((row) => renderConversationRow(row))}
          </>
        )}

        {/* Repeaters */}
        {nonFavoriteRepeaters.length > 0 && (
          <>
            {renderSectionHeader(
              'Repeaters',
              repeatersCollapsed,
              () => setRepeatersCollapsed((prev) => !prev),
              true,
              repeatersUnreadCount
            )}
            {(isSearching || !repeatersCollapsed) &&
              repeaterRows.map((row) => renderConversationRow(row))}
          </>
        )}

        {/* Empty state */}
        {nonFavoriteContacts.length === 0 &&
          nonFavoriteChannels.length === 0 &&
          nonFavoriteRepeaters.length === 0 &&
          favoriteItems.length === 0 && (
            <div className="p-5 text-center text-muted-foreground">
              {query ? 'No matches found' : 'No conversations yet'}
            </div>
          )}
      </div>
    </nav>
  );
}
