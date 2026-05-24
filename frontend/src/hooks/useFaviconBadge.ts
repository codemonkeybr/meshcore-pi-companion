import { useEffect, useMemo, useRef } from 'react';

import type { Channel, Contact } from '../types';
import { getStateKey } from '../utils/conversationState';

const APP_TITLE = 'RemoteTerm for MeshCore';
const UNREAD_APP_TITLE = 'RemoteTerm';
const BASE_FAVICON_PATH = './favicon.svg';
const GREEN_BADGE_FILL = '#16a34a';
const RED_BADGE_FILL = '#dc2626';
const BADGE_CENTER = 750;
const BADGE_OUTER_RADIUS = 220;
const BADGE_INNER_RADIUS = 180;

let baseFaviconSvgPromise: Promise<string> | null = null;

export type FaviconBadgeState = 'none' | 'green' | 'red';

function getUnreadDirectMessageCount(unreadCounts: Record<string, number>): number {
  return Object.entries(unreadCounts).reduce(
    (sum, [stateKey, count]) => sum + (stateKey.startsWith('contact-') ? count : 0),
    0
  );
}

function getUnreadFavoriteChannelCount(
  unreadCounts: Record<string, number>,
  channels: Channel[]
): number {
  return channels.reduce(
    (sum, channel) =>
      sum + (channel.favorite ? unreadCounts[getStateKey('channel', channel.key)] || 0 : 0),
    0
  );
}

export function getTotalUnreadCount(unreadCounts: Record<string, number>): number {
  return Object.values(unreadCounts).reduce((sum, count) => sum + count, 0);
}

export function getFavoriteUnreadCount(
  unreadCounts: Record<string, number>,
  contacts: Contact[],
  channels: Channel[]
): number {
  let sum = 0;
  for (const contact of contacts) {
    if (contact.favorite) {
      sum += unreadCounts[getStateKey('contact', contact.public_key)] || 0;
    }
  }
  for (const channel of channels) {
    if (channel.favorite) {
      sum += unreadCounts[getStateKey('channel', channel.key)] || 0;
    }
  }
  return sum;
}

export function getUnreadTitle(
  unreadCounts: Record<string, number>,
  contacts: Contact[],
  channels: Channel[]
): string {
  const unreadCount = getFavoriteUnreadCount(unreadCounts, contacts, channels);
  if (unreadCount <= 0) {
    return APP_TITLE;
  }

  const label = unreadCount > 99 ? '99+' : String(unreadCount);
  return `(${label}) ${UNREAD_APP_TITLE}`;
}

export function deriveFaviconBadgeState(
  unreadCounts: Record<string, number>,
  mentions: Record<string, boolean>,
  channels: Channel[]
): FaviconBadgeState {
  if (Object.values(mentions).some(Boolean) || getUnreadDirectMessageCount(unreadCounts) > 0) {
    return 'red';
  }

  if (getUnreadFavoriteChannelCount(unreadCounts, channels) > 0) {
    return 'green';
  }

  return 'none';
}

export function buildBadgedFaviconSvg(baseSvg: string, badgeFill: string): string {
  const closingTagIndex = baseSvg.lastIndexOf('</svg>');
  if (closingTagIndex === -1) {
    return baseSvg;
  }

  const badge = `
    <circle cx="${BADGE_CENTER}" cy="${BADGE_CENTER}" r="${BADGE_OUTER_RADIUS}" fill="#ffffff"/>
    <circle cx="${BADGE_CENTER}" cy="${BADGE_CENTER}" r="${BADGE_INNER_RADIUS}" fill="${badgeFill}"/>
  `;
  return `${baseSvg.slice(0, closingTagIndex)}${badge}</svg>`;
}

async function loadBaseFaviconSvg(): Promise<string> {
  if (!baseFaviconSvgPromise) {
    baseFaviconSvgPromise = fetch(BASE_FAVICON_PATH, { cache: 'force-cache' })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Failed to load favicon SVG: ${response.status}`);
        }
        return response.text();
      })
      .catch((error) => {
        baseFaviconSvgPromise = null;
        throw error;
      });
  }

  return baseFaviconSvgPromise;
}

function upsertFaviconLinks(rel: 'icon' | 'shortcut icon', href: string): void {
  const links = Array.from(document.head.querySelectorAll<HTMLLinkElement>(`link[rel="${rel}"]`));
  const targets = links.length > 0 ? links : [document.createElement('link')];

  for (const link of targets) {
    if (!link.parentNode) {
      link.rel = rel;
      document.head.appendChild(link);
    }
    link.type = 'image/svg+xml';
    link.href = href;
  }
}

function applyFaviconHref(href: string): void {
  upsertFaviconLinks('icon', href);
  upsertFaviconLinks('shortcut icon', href);
}

export function useUnreadTitle(
  unreadCounts: Record<string, number>,
  contacts: Contact[],
  channels: Channel[]
): void {
  const title = useMemo(
    () => getUnreadTitle(unreadCounts, contacts, channels),
    [contacts, channels, unreadCounts]
  );

  useEffect(() => {
    document.title = title;

    return () => {
      document.title = APP_TITLE;
    };
  }, [title]);
}

export function useFaviconBadge(
  unreadCounts: Record<string, number>,
  mentions: Record<string, boolean>,
  channels: Channel[]
): void {
  const objectUrlRef = useRef<string | null>(null);
  const badgeState = useMemo(
    () => deriveFaviconBadgeState(unreadCounts, mentions, channels),
    [channels, mentions, unreadCounts]
  );

  useEffect(() => {
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }

    if (badgeState === 'none') {
      applyFaviconHref(BASE_FAVICON_PATH);
      return;
    }

    const badgeFill = badgeState === 'red' ? RED_BADGE_FILL : GREEN_BADGE_FILL;
    let cancelled = false;

    void loadBaseFaviconSvg()
      .then((baseSvg) => {
        if (cancelled) {
          return;
        }

        const objectUrl = URL.createObjectURL(
          new Blob([buildBadgedFaviconSvg(baseSvg, badgeFill)], {
            type: 'image/svg+xml',
          })
        );
        objectUrlRef.current = objectUrl;
        applyFaviconHref(objectUrl);
      })
      .catch(() => {
        if (!cancelled) {
          applyFaviconHref(BASE_FAVICON_PATH);
        }
      });

    return () => {
      cancelled = true;
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
    };
  }, [badgeState]);
}
