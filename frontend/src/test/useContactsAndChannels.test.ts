/**
 * Tests for useContactsAndChannels hook.
 *
 * Focuses on pagination logic in fetchAllContacts (which fetches 1000 items
 * per page and continues until a page returns fewer than pageSize results).
 */

import { act, renderHook } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { useContactsAndChannels } from '../hooks/useContactsAndChannels';
import type { BulkCreateHashtagChannelsResult, Contact } from '../types';

// Mock api module
vi.mock('../api', () => ({
  api: {
    getContacts: vi.fn(),
    getChannels: vi.fn(),
    createContact: vi.fn(),
    createChannel: vi.fn(),
    bulkCreateHashtagChannels: vi.fn(),
    deleteContact: vi.fn(),
    deleteChannel: vi.fn(),
    decryptHistoricalPackets: vi.fn(),
    getUndecryptedPacketCount: vi.fn(),
  },
}));

// Mock prefetch — takePrefetchOrFetch calls the fetcher directly
vi.mock('../prefetch', () => ({
  takePrefetchOrFetch: vi.fn((_key: string, fetcher: () => Promise<unknown>) => fetcher()),
}));

// Mock sonner
vi.mock('../components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

function makeContact(suffix: string): Contact {
  const key = suffix.padStart(64, '0');
  return {
    public_key: key,
    name: `Contact-${suffix}`,
    type: 1,
    flags: 0,
    direct_path: null,
    direct_path_len: -1,
    direct_path_hash_mode: 0,
    last_advert: null,
    lat: null,
    lon: null,
    last_seen: null,
    on_radio: false,
    favorite: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: null,
  };
}

function makeContacts(count: number, startIndex = 0): Contact[] {
  return Array.from({ length: count }, (_, i) =>
    makeContact(String(startIndex + i).padStart(4, '0'))
  );
}

describe('useContactsAndChannels', () => {
  const setActiveConversation = vi.fn();
  const removeConversationMessages = vi.fn();
  const pendingDeleteFallbackRef = { current: false };
  const hasSetDefaultConversation = { current: false };

  beforeEach(() => {
    vi.clearAllMocks();
    pendingDeleteFallbackRef.current = false;
    hasSetDefaultConversation.current = false;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function renderUseContactsAndChannels() {
    return renderHook(() =>
      useContactsAndChannels({
        setActiveConversation,
        pendingDeleteFallbackRef,
        hasSetDefaultConversation,
        removeConversationMessages,
      })
    );
  }

  describe('fetchAllContacts pagination', () => {
    it('returns contacts directly when fewer than page size', async () => {
      const { api } = await import('../api');
      const contacts = makeContacts(50);
      vi.mocked(api.getContacts).mockResolvedValueOnce(contacts);

      const { result } = renderUseContactsAndChannels();

      let fetched: Contact[] = [];
      await act(async () => {
        fetched = await result.current.fetchAllContacts();
      });

      expect(fetched).toHaveLength(50);
      // Should only call once (no pagination needed)
      expect(api.getContacts).toHaveBeenCalledTimes(1);
      expect(api.getContacts).toHaveBeenCalledWith(1000, 0);
    });

    it('paginates when first page returns exactly page size', async () => {
      const { api } = await import('../api');
      const page1 = makeContacts(1000, 0);
      const page2 = makeContacts(200, 1000);

      vi.mocked(api.getContacts)
        .mockResolvedValueOnce(page1) // First page: full
        .mockResolvedValueOnce(page2); // Second page: partial (done)

      const { result } = renderUseContactsAndChannels();

      let fetched: Contact[] = [];
      await act(async () => {
        fetched = await result.current.fetchAllContacts();
      });

      expect(fetched).toHaveLength(1200);
      expect(api.getContacts).toHaveBeenCalledTimes(2);
      expect(api.getContacts).toHaveBeenNthCalledWith(1, 1000, 0);
      expect(api.getContacts).toHaveBeenNthCalledWith(2, 1000, 1000);
    });

    it('paginates through multiple full pages', async () => {
      const { api } = await import('../api');
      const page1 = makeContacts(1000, 0);
      const page2 = makeContacts(1000, 1000);
      const page3 = makeContacts(500, 2000);

      vi.mocked(api.getContacts)
        .mockResolvedValueOnce(page1)
        .mockResolvedValueOnce(page2)
        .mockResolvedValueOnce(page3);

      const { result } = renderUseContactsAndChannels();

      let fetched: Contact[] = [];
      await act(async () => {
        fetched = await result.current.fetchAllContacts();
      });

      expect(fetched).toHaveLength(2500);
      expect(api.getContacts).toHaveBeenCalledTimes(3);
      expect(api.getContacts).toHaveBeenNthCalledWith(3, 1000, 2000);
    });

    it('handles exactly page size total (boundary case)', async () => {
      const { api } = await import('../api');
      const page1 = makeContacts(1000, 0);
      const page2: Contact[] = []; // Empty second page

      vi.mocked(api.getContacts).mockResolvedValueOnce(page1).mockResolvedValueOnce(page2);

      const { result } = renderUseContactsAndChannels();

      let fetched: Contact[] = [];
      await act(async () => {
        fetched = await result.current.fetchAllContacts();
      });

      expect(fetched).toHaveLength(1000);
      expect(api.getContacts).toHaveBeenCalledTimes(2);
    });
  });

  describe('bulk hashtag creation', () => {
    it('refreshes channels and returns the backend result', async () => {
      const { api } = await import('../api');
      const resultPayload: BulkCreateHashtagChannelsResult = {
        created_channels: [
          {
            key: 'AA'.repeat(16),
            name: '#ops',
            is_hashtag: true,
            on_radio: false,
            last_read_at: null,
            favorite: false,
            muted: false,
          },
        ],
        existing_count: 1,
        invalid_names: [],
        decrypt_started: true,
        decrypt_total_packets: 12,
        message: 'Created 1 room',
      };
      vi.mocked(api.bulkCreateHashtagChannels).mockResolvedValueOnce(resultPayload);
      vi.mocked(api.getChannels).mockResolvedValueOnce(resultPayload.created_channels);
      vi.mocked(api.getUndecryptedPacketCount).mockResolvedValueOnce({ count: 9 });

      const { result } = renderUseContactsAndChannels();

      let response: BulkCreateHashtagChannelsResult | null = null;
      await act(async () => {
        response = await result.current.handleBulkCreateHashtagChannels(['#ops'], true);
      });

      expect(api.bulkCreateHashtagChannels).toHaveBeenCalledWith(['#ops'], true);
      expect(api.getChannels).toHaveBeenCalled();
      expect(api.getUndecryptedPacketCount).toHaveBeenCalled();
      expect(response).toEqual(resultPayload);
    });
  });
});
