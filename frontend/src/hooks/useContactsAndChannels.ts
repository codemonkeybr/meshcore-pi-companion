import { useState, useCallback, type MutableRefObject } from 'react';
import { api } from '../api';
import { takePrefetchOrFetch } from '../prefetch';
import { toast } from '../components/ui/sonner';
import { getContactDisplayName } from '../utils/pubkey';
import { findPublicChannel, PUBLIC_CHANNEL_KEY, PUBLIC_CHANNEL_NAME } from '../utils/publicChannel';
import type { BulkCreateHashtagChannelsResult, Channel, Contact, Conversation } from '../types';

interface UseContactsAndChannelsArgs {
  setActiveConversation: (conv: Conversation | null) => void;
  pendingDeleteFallbackRef: MutableRefObject<boolean>;
  hasSetDefaultConversation: MutableRefObject<boolean>;
  removeConversationMessages: (conversationId: string) => void;
}

export function useContactsAndChannels({
  setActiveConversation,
  pendingDeleteFallbackRef,
  hasSetDefaultConversation,
  removeConversationMessages,
}: UseContactsAndChannelsArgs) {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [contactsLoaded, setContactsLoaded] = useState(false);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [undecryptedCount, setUndecryptedCount] = useState(0);

  const fetchUndecryptedCountInternal = useCallback(async () => {
    try {
      const data = await takePrefetchOrFetch('undecryptedCount', api.getUndecryptedPacketCount);
      setUndecryptedCount(data.count);
    } catch (err) {
      console.error('Failed to fetch undecrypted count:', err);
    }
  }, []);

  // Fetch all contacts, paginating if >1000
  const fetchAllContacts = useCallback(async (): Promise<Contact[]> => {
    const pageSize = 1000;
    const first = await takePrefetchOrFetch('contacts', () => api.getContacts(pageSize, 0));
    if (first.length < pageSize) return first;
    let all = [...first];
    let offset = pageSize;
    while (true) {
      const page = await api.getContacts(pageSize, offset);
      all = all.concat(page);
      if (page.length < pageSize) break;
      offset += pageSize;
    }
    return all;
  }, []);

  const handleCreateContact = useCallback(
    async (name: string, publicKey: string, tryHistorical: boolean, type?: number) => {
      const created = await api.createContact(publicKey, name || undefined, tryHistorical, type);
      const data = await fetchAllContacts();
      setContacts(data);

      setActiveConversation({
        type: 'contact',
        id: created.public_key,
        name: getContactDisplayName(created.name, created.public_key, created.last_advert),
      });
    },
    [fetchAllContacts, setActiveConversation]
  );

  const handleCreateChannel = useCallback(
    async (name: string, key: string, tryHistorical: boolean) => {
      const created = await api.createChannel(name, key);
      const data = await api.getChannels();
      setChannels(data);

      setActiveConversation({
        type: 'channel',
        id: created.key,
        name,
      });

      if (tryHistorical) {
        await api.decryptHistoricalPackets({
          key_type: 'channel',
          channel_key: created.key,
        });
        fetchUndecryptedCountInternal();
      }
    },
    [fetchUndecryptedCountInternal, setActiveConversation]
  );

  const handleCreateHashtagChannel = useCallback(
    async (name: string, tryHistorical: boolean) => {
      const channelName = name.startsWith('#') ? name : `#${name}`;

      const created = await api.createChannel(channelName);
      const data = await api.getChannels();
      setChannels(data);

      setActiveConversation({
        type: 'channel',
        id: created.key,
        name: channelName,
      });

      if (tryHistorical) {
        await api.decryptHistoricalPackets({
          key_type: 'channel',
          channel_name: channelName,
        });
        fetchUndecryptedCountInternal();
      }
    },
    [fetchUndecryptedCountInternal, setActiveConversation]
  );

  const handleBulkCreateHashtagChannels = useCallback(
    async (
      channelNames: string[],
      tryHistorical: boolean
    ): Promise<BulkCreateHashtagChannelsResult> => {
      const result = await api.bulkCreateHashtagChannels(channelNames, tryHistorical);
      const data = await api.getChannels();
      setChannels(data);

      if (tryHistorical && result.decrypt_started) {
        fetchUndecryptedCountInternal();
      }

      return result;
    },
    [fetchUndecryptedCountInternal]
  );

  const handleDeleteChannel = useCallback(
    async (key: string) => {
      if (!confirm('Delete this channel? Message history will be preserved.')) return;
      try {
        pendingDeleteFallbackRef.current = true;
        await api.deleteChannel(key);
        removeConversationMessages(key);
        const refreshedChannels = await api.getChannels();
        setChannels(refreshedChannels);
        const publicChannel = findPublicChannel(refreshedChannels);
        hasSetDefaultConversation.current = true;
        setActiveConversation({
          type: 'channel',
          id: publicChannel?.key || PUBLIC_CHANNEL_KEY,
          name: publicChannel?.name || PUBLIC_CHANNEL_NAME,
        });
        toast.success('Channel deleted');
      } catch (err) {
        console.error('Failed to delete channel:', err);
        toast.error('Failed to delete channel', {
          description: err instanceof Error ? err.message : undefined,
        });
      }
    },
    [
      hasSetDefaultConversation,
      pendingDeleteFallbackRef,
      removeConversationMessages,
      setActiveConversation,
    ]
  );

  const handleDeleteContact = useCallback(
    async (publicKey: string) => {
      if (!confirm('Delete this contact? Message history will be preserved.')) return;
      try {
        pendingDeleteFallbackRef.current = true;
        await api.deleteContact(publicKey);
        removeConversationMessages(publicKey);
        setContacts((prev) => prev.filter((c) => c.public_key !== publicKey));
        const refreshedChannels = await api.getChannels();
        setChannels(refreshedChannels);
        const publicChannel = findPublicChannel(refreshedChannels);
        hasSetDefaultConversation.current = true;
        setActiveConversation({
          type: 'channel',
          id: publicChannel?.key || PUBLIC_CHANNEL_KEY,
          name: publicChannel?.name || PUBLIC_CHANNEL_NAME,
        });
        toast.success('Contact deleted');
      } catch (err) {
        console.error('Failed to delete contact:', err);
        toast.error('Failed to delete contact', {
          description: err instanceof Error ? err.message : undefined,
        });
      }
    },
    [
      hasSetDefaultConversation,
      pendingDeleteFallbackRef,
      removeConversationMessages,
      setActiveConversation,
    ]
  );

  return {
    contacts,
    contactsLoaded,
    channels,
    undecryptedCount,
    setContacts,
    setContactsLoaded,
    setChannels,
    fetchAllContacts,
    fetchUndecryptedCount: fetchUndecryptedCountInternal,
    handleCreateContact,
    handleCreateChannel,
    handleCreateHashtagChannel,
    handleBulkCreateHashtagChannels,
    handleDeleteChannel,
    handleDeleteContact,
  };
}
