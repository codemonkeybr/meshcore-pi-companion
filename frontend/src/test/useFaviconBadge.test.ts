import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  buildBadgedFaviconSvg,
  deriveFaviconBadgeState,
  getFavoriteUnreadCount,
  getUnreadTitle,
  getTotalUnreadCount,
  useFaviconBadge,
  useUnreadTitle,
} from '../hooks/useFaviconBadge';
import type { Channel, Contact } from '../types';
import { getStateKey } from '../utils/conversationState';

function makeChannel(key: string, favorite = false): Channel {
  return {
    key,
    name: key,
    is_hashtag: false,
    on_radio: false,
    last_read_at: null,
    favorite,
    muted: false,
  };
}

function makeContact(publicKey: string, favorite = false): Contact {
  return {
    public_key: publicKey,
    name: publicKey,
    type: 1,
    flags: 0,
    direct_path: null,
    direct_path_len: -1,
    direct_path_hash_mode: -1,
    last_advert: null,
    lat: null,
    lon: null,
    last_seen: null,
    on_radio: false,
    favorite,
    last_contacted: null,
    last_read_at: null,
    first_seen: null,
  };
}

function getIconHref(rel: 'icon' | 'shortcut icon'): string | null {
  return (
    document.head.querySelector<HTMLLinkElement>(`link[rel="${rel}"]`)?.getAttribute('href') ?? null
  );
}

describe('useFaviconBadge', () => {
  const baseSvg =
    '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1000" height="1000"/></svg>';
  const originalCreateObjectURL = URL.createObjectURL;
  const originalRevokeObjectURL = URL.revokeObjectURL;
  let objectUrlCounter = 0;
  let fetchMock: ReturnType<typeof vi.fn>;
  let createObjectURLMock: ReturnType<typeof vi.fn>;
  let revokeObjectURLMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    document.head.innerHTML = `
      <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
      <link rel="shortcut icon" href="/favicon.ico" />
    `;
    document.title = 'RemoteTerm for MeshCore';
    objectUrlCounter = 0;
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => baseSvg,
    });
    createObjectURLMock = vi.fn(() => `blob:generated-${++objectUrlCounter}`);
    revokeObjectURLMock = vi.fn();

    vi.stubGlobal('fetch', fetchMock);
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: createObjectURLMock,
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: revokeObjectURLMock,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: originalCreateObjectURL,
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: originalRevokeObjectURL,
    });
  });

  it('derives badge priority from unread counts, mentions, and favorites', () => {
    const channels = [makeChannel('fav-chan', true)];

    expect(deriveFaviconBadgeState({}, {}, channels)).toBe('none');
    expect(
      deriveFaviconBadgeState(
        {
          [getStateKey('channel', 'fav-chan')]: 3,
        },
        {},
        channels
      )
    ).toBe('green');
    expect(
      deriveFaviconBadgeState(
        {
          [getStateKey('contact', 'abc')]: 12,
        },
        {},
        channels
      )
    ).toBe('red');
    expect(
      deriveFaviconBadgeState(
        {
          [getStateKey('channel', 'fav-chan')]: 1,
        },
        {
          [getStateKey('channel', 'fav-chan')]: true,
        },
        channels
      )
    ).toBe('red');
  });

  it('builds a dot-only badge into the base svg markup', () => {
    const svg = buildBadgedFaviconSvg(baseSvg, '#16a34a');

    expect(svg).toContain('<circle cx="750" cy="750" r="220" fill="#ffffff"/>');
    expect(svg).toContain('<circle cx="750" cy="750" r="180" fill="#16a34a"/>');
    expect(svg).not.toContain('<text');
  });

  it('derives the unread count and page title', () => {
    expect(getTotalUnreadCount({})).toBe(0);
    expect(getTotalUnreadCount({ a: 2, b: 5 })).toBe(7);
    expect(getFavoriteUnreadCount({}, [], [])).toBe(0);
    expect(
      getFavoriteUnreadCount(
        {
          [getStateKey('channel', 'fav-chan')]: 7,
          [getStateKey('contact', 'fav-contact')]: 3,
          [getStateKey('channel', 'other-chan')]: 9,
        },
        [makeContact('fav-contact', true)],
        [makeChannel('fav-chan', true)]
      )
    ).toBe(10);
    expect(getUnreadTitle({}, [], [])).toBe('RemoteTerm for MeshCore');
    expect(
      getUnreadTitle(
        {
          [getStateKey('channel', 'fav-chan')]: 7,
          [getStateKey('channel', 'other-chan')]: 9,
        },
        [],
        [makeChannel('fav-chan', true)]
      )
    ).toBe('(7) RemoteTerm');
    expect(
      getUnreadTitle(
        {
          [getStateKey('channel', 'fav-chan')]: 120,
        },
        [],
        [makeChannel('fav-chan', true)]
      )
    ).toBe('(99+) RemoteTerm');
  });

  it('switches between the base favicon and generated blob badges', async () => {
    const channels = [makeChannel('fav-chan', true)];
    const { rerender } = renderHook(
      ({
        unreadCounts,
        mentions,
        currentChannels,
      }: {
        unreadCounts: Record<string, number>;
        mentions: Record<string, boolean>;
        currentChannels: Channel[];
      }) => useFaviconBadge(unreadCounts, mentions, currentChannels),
      {
        initialProps: {
          unreadCounts: {},
          mentions: {},
          currentChannels: channels,
        },
      }
    );

    await waitFor(() => {
      expect(getIconHref('icon')).toBe('./favicon.svg');
      expect(getIconHref('shortcut icon')).toBe('./favicon.svg');
    });

    rerender({
      unreadCounts: {
        [getStateKey('channel', 'fav-chan')]: 1,
      },
      mentions: {},
      currentChannels: channels,
    });

    await waitFor(() => {
      expect(getIconHref('icon')).toBe('blob:generated-1');
      expect(getIconHref('shortcut icon')).toBe('blob:generated-1');
    });

    rerender({
      unreadCounts: {
        [getStateKey('contact', 'dm-key')]: 12,
      },
      mentions: {},
      currentChannels: channels,
    });

    await waitFor(() => {
      expect(getIconHref('icon')).toBe('blob:generated-2');
      expect(getIconHref('shortcut icon')).toBe('blob:generated-2');
    });

    rerender({
      unreadCounts: {},
      mentions: {},
      currentChannels: channels,
    });

    await waitFor(() => {
      expect(getIconHref('icon')).toBe('./favicon.svg');
      expect(getIconHref('shortcut icon')).toBe('./favicon.svg');
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(createObjectURLMock).toHaveBeenCalledTimes(2);
    expect(revokeObjectURLMock).toHaveBeenCalledWith('blob:generated-1');
    expect(revokeObjectURLMock).toHaveBeenCalledWith('blob:generated-2');
  });

  it('writes unread counts into the page title', () => {
    const channels = [makeChannel('fav-chan', true)];
    const { rerender, unmount } = renderHook(
      ({
        unreadCounts,
        contacts,
        currentChannels,
      }: {
        unreadCounts: Record<string, number>;
        contacts: Contact[];
        currentChannels: Channel[];
      }) => useUnreadTitle(unreadCounts, contacts, currentChannels),
      {
        initialProps: {
          unreadCounts: {},
          contacts: [],
          currentChannels: channels,
        },
      }
    );

    expect(document.title).toBe('RemoteTerm for MeshCore');

    rerender({
      unreadCounts: {
        [getStateKey('channel', 'fav-chan')]: 4,
        [getStateKey('contact', 'dm-key')]: 2,
      },
      contacts: [],
      currentChannels: channels,
    });

    expect(document.title).toBe('(4) RemoteTerm');

    unmount();

    expect(document.title).toBe('RemoteTerm for MeshCore');
  });
});
