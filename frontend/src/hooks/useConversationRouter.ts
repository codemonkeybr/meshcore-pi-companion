import { useState, useCallback, useEffect, useRef, type MutableRefObject } from 'react';
import {
  parseHashConversation,
  parseHashSettingsSection,
  updateUrlHash,
  resolveChannelFromHashToken,
  resolveContactFromHashToken,
} from '../utils/urlHash';
import {
  getLastViewedConversation,
  getReopenLastConversationEnabled,
  saveLastViewedConversation,
} from '../utils/lastViewedConversation';
import { findPublicChannel } from '../utils/publicChannel';
import { getContactDisplayName } from '../utils/pubkey';
import { toast } from '../components/ui/sonner';
import type { Channel, Contact, Conversation } from '../types';

interface UseConversationRouterArgs {
  channels: Channel[];
  contacts: Contact[];
  contactsLoaded: boolean;
  suspendHashSync: boolean;
  setSidebarOpen: (open: boolean) => void;
  pendingDeleteFallbackRef: MutableRefObject<boolean>;
  hasSetDefaultConversation: MutableRefObject<boolean>;
}

export function useConversationRouter({
  channels,
  contacts,
  contactsLoaded,
  suspendHashSync,
  setSidebarOpen,
  pendingDeleteFallbackRef,
  hasSetDefaultConversation,
}: UseConversationRouterArgs) {
  const [activeConversation, setActiveConversationState] = useState<Conversation | null>(null);
  const activeConversationRef = useRef<Conversation | null>(null);
  const hashSyncEnabledRef = useRef(
    typeof window !== 'undefined'
      ? window.location.hash.length > 0 && parseHashSettingsSection() === null
      : false
  );

  const setActiveConversation = useCallback((conv: Conversation | null) => {
    hashSyncEnabledRef.current = true;
    setActiveConversationState(conv);
  }, []);

  const getPublicChannelConversation = useCallback((): Conversation | null => {
    const publicChannel = findPublicChannel(channels);
    if (!publicChannel) return null;
    return {
      type: 'channel',
      id: publicChannel.key,
      name: publicChannel.name,
    };
  }, [channels]);

  // Phase 1: Set initial conversation from URL hash or default to Public channel
  // Only needs channels (fast path) - doesn't wait for contacts
  useEffect(() => {
    if (hasSetDefaultConversation.current || activeConversation) return;

    const hashConv = parseHashSettingsSection() ? null : parseHashConversation();

    // Handle non-data views immediately
    if (hashConv?.type === 'raw') {
      setActiveConversationState({ type: 'raw', id: 'raw', name: 'Raw Packet Feed' });
      hasSetDefaultConversation.current = true;
      return;
    }
    if (hashConv?.type === 'map') {
      setActiveConversationState({
        type: 'map',
        id: 'map',
        name: 'Node Map',
        mapFocusKey: hashConv.mapFocusKey,
      });
      hasSetDefaultConversation.current = true;
      return;
    }
    if (hashConv?.type === 'visualizer') {
      setActiveConversationState({ type: 'visualizer', id: 'visualizer', name: 'Mesh Visualizer' });
      hasSetDefaultConversation.current = true;
      return;
    }
    if (hashConv?.type === 'search') {
      setActiveConversationState({ type: 'search', id: 'search', name: 'Message Search' });
      hasSetDefaultConversation.current = true;
      return;
    }
    if (hashConv?.type === 'trace') {
      setActiveConversationState({ type: 'trace', id: 'trace', name: 'Trace' });
      hasSetDefaultConversation.current = true;
      return;
    }

    // No hash: optionally restore last-viewed non-data conversation if enabled on this device.
    if (!hashConv && getReopenLastConversationEnabled()) {
      const lastViewed = getLastViewedConversation();
      if (
        lastViewed &&
        (lastViewed.type === 'raw' ||
          lastViewed.type === 'map' ||
          lastViewed.type === 'visualizer' ||
          lastViewed.type === 'trace')
      ) {
        setActiveConversationState(lastViewed);
        hasSetDefaultConversation.current = true;
        return;
      }
    }

    if (channels.length === 0) return;

    // Handle channel hash (ID-first with legacy-name fallback)
    if (hashConv?.type === 'channel') {
      const channel = resolveChannelFromHashToken(hashConv.name, channels);
      if (channel) {
        setActiveConversationState({ type: 'channel', id: channel.key, name: channel.name });
        hasSetDefaultConversation.current = true;
        return;
      }
    }

    // Contact hash — wait for phase 2
    if (hashConv?.type === 'contact') return;

    // No hash: optionally restore last-viewed conversation if enabled on this device.
    if (!hashConv && getReopenLastConversationEnabled()) {
      const lastViewed = getLastViewedConversation();
      if (lastViewed?.type === 'channel') {
        const channel =
          channels.find((c) => c.key.toLowerCase() === lastViewed.id.toLowerCase()) ||
          resolveChannelFromHashToken(lastViewed.id, channels);
        if (channel) {
          setActiveConversationState({
            type: 'channel',
            id: channel.key,
            name: channel.name,
          });
          hasSetDefaultConversation.current = true;
          return;
        }
      }
      // Last-viewed contact resolution waits for contacts in phase 2.
      if (lastViewed?.type === 'contact') return;
    }

    // No hash or unresolvable — default to Public
    const publicConversation = getPublicChannelConversation();
    if (publicConversation) {
      if (hashConv?.type === 'channel') {
        const token =
          hashConv.name.length > 16 ? hashConv.name.substring(0, 16) + '…' : hashConv.name;
        toast.error(`Channel not found: ${token}`);
      }
      setActiveConversationState(publicConversation);
      hasSetDefaultConversation.current = true;
    }
  }, [channels, activeConversation, getPublicChannelConversation, hasSetDefaultConversation]);

  // Phase 2: Resolve contact hash (only if phase 1 didn't set a conversation)
  useEffect(() => {
    if (hasSetDefaultConversation.current || activeConversation) return;

    const hashConv = parseHashSettingsSection() ? null : parseHashConversation();
    if (hashConv?.type === 'contact') {
      if (!contactsLoaded) return;

      const contact = resolveContactFromHashToken(hashConv.name, contacts);
      if (contact) {
        setActiveConversationState({
          type: 'contact',
          id: contact.public_key,
          name: getContactDisplayName(contact.name, contact.public_key, contact.last_advert),
        });
        hasSetDefaultConversation.current = true;
        return;
      }

      // Contact hash didn't match — fall back to Public if channels loaded.
      const token =
        hashConv.name.length > 16 ? hashConv.name.substring(0, 16) + '…' : hashConv.name;
      toast.error(`Contact not found: ${token}`);
      const publicConversation = getPublicChannelConversation();
      if (publicConversation) {
        setActiveConversationState(publicConversation);
        hasSetDefaultConversation.current = true;
      }
      return;
    }

    // No hash: optionally restore a last-viewed contact once contacts are loaded.
    if (!hashConv && getReopenLastConversationEnabled()) {
      const lastViewed = getLastViewedConversation();
      if (lastViewed?.type !== 'contact') return;
      if (!contactsLoaded) return;

      const contact =
        contacts.find((item) => item.public_key.toLowerCase() === lastViewed.id.toLowerCase()) ||
        resolveContactFromHashToken(lastViewed.id, contacts);
      if (contact) {
        setActiveConversationState({
          type: 'contact',
          id: contact.public_key,
          name: getContactDisplayName(contact.name, contact.public_key, contact.last_advert),
        });
        hasSetDefaultConversation.current = true;
        return;
      }

      const publicConversation = getPublicChannelConversation();
      if (publicConversation) {
        setActiveConversationState(publicConversation);
        hasSetDefaultConversation.current = true;
      }
    }
  }, [
    contacts,
    channels,
    activeConversation,
    contactsLoaded,
    getPublicChannelConversation,
    hasSetDefaultConversation,
  ]);

  // Keep ref in sync and update URL hash
  useEffect(() => {
    activeConversationRef.current = activeConversation;
    if (activeConversation) {
      if (hashSyncEnabledRef.current && !suspendHashSync) {
        updateUrlHash(activeConversation);
      }
      if (activeConversation.type !== 'search') {
        saveLastViewedConversation(activeConversation);
      }
    }
  }, [activeConversation, suspendHashSync]);

  // If a delete action left us without an active conversation, recover to Public
  useEffect(() => {
    if (!pendingDeleteFallbackRef.current) return;
    if (activeConversation) {
      pendingDeleteFallbackRef.current = false;
      return;
    }

    const publicChannel = findPublicChannel(channels);
    if (!publicChannel) return;

    hasSetDefaultConversation.current = true;
    pendingDeleteFallbackRef.current = false;
    setActiveConversationState({
      type: 'channel',
      id: publicChannel.key,
      name: publicChannel.name,
    });
  }, [activeConversation, channels, hasSetDefaultConversation, pendingDeleteFallbackRef]);

  // Handle conversation selection (closes sidebar on mobile)
  const handleSelectConversation = useCallback(
    (conv: Conversation) => {
      setActiveConversation(conv);
      setSidebarOpen(false);
    },
    [setActiveConversation, setSidebarOpen]
  );

  return {
    activeConversation,
    setActiveConversation,
    activeConversationRef,
    handleSelectConversation,
  };
}
