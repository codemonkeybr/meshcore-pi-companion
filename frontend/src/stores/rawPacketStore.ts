import { useSyncExternalStore } from 'react';

import type { RawPacket } from '../types';
import { appendRawPacketUnique } from '../utils/rawPacketIdentity';
import {
  MAX_RAW_PACKET_STATS_OBSERVATIONS,
  summarizeRawPacketForStats,
  type RawPacketStatsSessionState,
} from '../utils/rawPacketStats';

/**
 * Live radio traffic arrives continuously — every packet the node overhears, not
 * just our own conversations. Holding that stream in App state re-rendered the whole
 * tree (including the message list) on every packet, which made typing crawl in
 * conversations with a lot of history.
 *
 * The stream lives here instead, outside React, so only the views that actually read
 * packets (map, visualizer, raw feed, cracker) re-render when one arrives.
 */

export const MAX_RAW_PACKETS = 500;

function createStatsSession(): RawPacketStatsSessionState {
  return {
    sessionStartedAt: Date.now(),
    totalObservedPackets: 0,
    trimmedObservationCount: 0,
    observations: [],
  };
}

let packets: RawPacket[] = [];
let statsSession: RawPacketStatsSessionState = createStatsSession();
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function emit(): void {
  for (const listener of listeners) {
    listener();
  }
}

function observePacket(
  session: RawPacketStatsSessionState,
  packet: RawPacket
): RawPacketStatsSessionState {
  const observation = summarizeRawPacketForStats(packet);
  if (
    session.observations.some(
      (candidate) => candidate.observationKey === observation.observationKey
    )
  ) {
    return session;
  }

  const observations = [...session.observations, observation];
  if (observations.length <= MAX_RAW_PACKET_STATS_OBSERVATIONS) {
    return {
      ...session,
      totalObservedPackets: session.totalObservedPackets + 1,
      observations,
    };
  }

  const overflow = observations.length - MAX_RAW_PACKET_STATS_OBSERVATIONS;
  return {
    ...session,
    totalObservedPackets: session.totalObservedPackets + 1,
    trimmedObservationCount: session.trimmedObservationCount + overflow,
    observations: observations.slice(overflow),
  };
}

/** Record one observed packet into both the rolling buffer and the session stats. */
export function recordRawPacket(packet: RawPacket, maxPackets: number = MAX_RAW_PACKETS): void {
  const nextPackets = appendRawPacketUnique(packets, packet, maxPackets);
  const nextStats = observePacket(statsSession, packet);
  if (nextPackets === packets && nextStats === statsSession) {
    return;
  }

  packets = nextPackets;
  statsSession = nextStats;
  emit();
}

/**
 * Drop the buffered packets on reconnect — we may have missed traffic while offline.
 * Session stats deliberately survive: they describe the whole observation session.
 */
export function clearRawPackets(): void {
  if (packets.length === 0) {
    return;
  }
  packets = [];
  emit();
}

/** Full reset, including session stats. Used by tests to isolate cases. */
export function resetRawPacketStore(): void {
  packets = [];
  statsSession = createStatsSession();
  emit();
}

/**
 * Install a specific stream/stats snapshot. Exists so tests can drive the views
 * from fixed fixtures — including stats shapes that a live packet feed would take
 * minutes to produce — without reintroducing prop drilling through the app tree.
 */
export function seedRawPacketStore(next: {
  packets?: RawPacket[];
  statsSession?: RawPacketStatsSessionState;
}): void {
  // Copy rather than alias. The whole store rests on "a snapshot is immutable, and its
  // identity changes only when its contents do". Holding the caller's array would let
  // them mutate the live snapshot in place, and because useSyncExternalStore compares
  // snapshots with Object.is, React would then bail out of every subsequent render —
  // leaving the UI permanently disagreeing with getRawPackets() and no way to tell why.
  if (next.packets) {
    packets = [...next.packets];
  }
  if (next.statsSession) {
    statsSession = {
      ...next.statsSession,
      observations: [...next.statsSession.observations],
    };
  }
  emit();
}

export function getRawPackets(): RawPacket[] {
  return packets;
}

export function getRawPacketStatsSession(): RawPacketStatsSessionState {
  return statsSession;
}

export function useRawPackets(): RawPacket[] {
  return useSyncExternalStore(subscribe, getRawPackets);
}

export function useRawPacketStatsSession(): RawPacketStatsSessionState {
  return useSyncExternalStore(subscribe, getRawPacketStatsSession);
}
