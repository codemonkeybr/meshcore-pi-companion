import { MeshCoreDecoder, PayloadType, Utils } from '@michaelhart/meshcore-decoder';

import type { RawPacket } from '../types';
import { getRawPacketObservationKey } from './rawPacketIdentity';

export const RAW_PACKET_STATS_WINDOWS = ['1m', '5m', '10m', '30m', 'session'] as const;
export type RawPacketStatsWindow = (typeof RAW_PACKET_STATS_WINDOWS)[number];

const RAW_PACKET_STATS_WINDOW_SECONDS: Record<Exclude<RawPacketStatsWindow, 'session'>, number> = {
  '1m': 60,
  '5m': 5 * 60,
  '10m': 10 * 60,
  '30m': 30 * 60,
};

export const MAX_RAW_PACKET_STATS_OBSERVATIONS = 20000;

export const KNOWN_PAYLOAD_TYPES = [
  'Advert',
  'GroupText',
  'TextMessage',
  'Ack',
  'Request',
  'Response',
  'Trace',
  'Path',
  'Control',
  'Unknown',
] as const;

const KNOWN_ROUTE_TYPES = [
  'Flood',
  'Direct',
  'TransportFlood',
  'TransportDirect',
  'Unknown',
] as const;

export interface RawPacketStatsObservation {
  observationKey: string;
  timestamp: number;
  payloadType: string;
  routeType: string;
  decrypted: boolean;
  rssi: number | null;
  snr: number | null;
  sourceKey: string | null;
  sourceLabel: string | null;
  pathTokenCount: number;
  pathSignature: string | null;
  hopByteWidth?: number | null;
}

export interface RawPacketStatsSessionState {
  sessionStartedAt: number;
  totalObservedPackets: number;
  trimmedObservationCount: number;
  observations: RawPacketStatsObservation[];
}

export interface RankedPacketStat {
  label: string;
  count: number;
  share: number;
}

export interface NeighborStat {
  key: string;
  label: string;
  count: number;
  bestRssi: number | null;
  lastSeen: number;
}

export interface PacketTimelineBin {
  label: string;
  total: number;
  countsByType: Record<string, number>;
}

export interface RawPacketStatsSnapshot {
  window: RawPacketStatsWindow;
  nowSec: number;
  packets: RawPacketStatsObservation[];
  packetCount: number;
  packetsPerMinute: number;
  uniqueSources: number;
  decryptedCount: number;
  undecryptedCount: number;
  decryptRate: number;
  pathBearingCount: number;
  pathBearingRate: number;
  distinctPaths: number;
  payloadBreakdown: RankedPacketStat[];
  routeBreakdown: RankedPacketStat[];
  topPacketTypes: RankedPacketStat[];
  hopProfile: RankedPacketStat[];
  hopByteWidthProfile: RankedPacketStat[];
  strongestNeighbors: NeighborStat[];
  mostActiveNeighbors: NeighborStat[];
  newestNeighbors: NeighborStat[];
  averageRssi: number | null;
  medianRssi: number | null;
  bestRssi: number | null;
  rssiBuckets: RankedPacketStat[];
  coverageSeconds: number;
  windowFullyCovered: boolean;
  oldestStoredTimestamp: number | null;
  timeline: PacketTimelineBin[];
}

function toSourceLabel(sourceKey: string): string {
  if (sourceKey.startsWith('name:')) {
    return sourceKey.slice(5);
  }
  return sourceKey.slice(0, 12).toUpperCase();
}

function getPathTokens(decoded: ReturnType<typeof MeshCoreDecoder.decode>): string[] {
  const tracePayload =
    decoded.payloadType === PayloadType.Trace && decoded.payload.decoded
      ? (decoded.payload.decoded as { pathHashes?: string[] })
      : null;
  return tracePayload?.pathHashes || decoded.path || [];
}

function getSourceInfo(
  packet: RawPacket,
  decoded: ReturnType<typeof MeshCoreDecoder.decode>
): Pick<RawPacketStatsObservation, 'sourceKey' | 'sourceLabel'> {
  if (!decoded.isValid || !decoded.payload.decoded) {
    const fallbackContactKey = packet.decrypted_info?.contact_key?.toUpperCase() ?? null;
    if (fallbackContactKey) {
      return {
        sourceKey: fallbackContactKey,
        sourceLabel: packet.decrypted_info?.sender || toSourceLabel(fallbackContactKey),
      };
    }
    if (packet.decrypted_info?.sender) {
      return {
        sourceKey: `name:${packet.decrypted_info.sender.toLowerCase()}`,
        sourceLabel: packet.decrypted_info.sender,
      };
    }
    return { sourceKey: null, sourceLabel: null };
  }

  switch (decoded.payloadType) {
    case PayloadType.Advert: {
      const publicKey = (decoded.payload.decoded as { publicKey?: string }).publicKey;
      if (!publicKey) return { sourceKey: null, sourceLabel: null };
      return {
        sourceKey: publicKey.toUpperCase(),
        sourceLabel: publicKey.slice(0, 12).toUpperCase(),
      };
    }
    case PayloadType.TextMessage:
    case PayloadType.Request:
    case PayloadType.Response: {
      const contactKey = packet.decrypted_info?.contact_key?.toUpperCase() ?? null;
      if (contactKey) {
        return {
          sourceKey: contactKey,
          sourceLabel: packet.decrypted_info?.sender || toSourceLabel(contactKey),
        };
      }
      const sourceHash = (decoded.payload.decoded as { sourceHash?: string }).sourceHash;
      if (!sourceHash) return { sourceKey: null, sourceLabel: null };
      return {
        sourceKey: `hash1:${sourceHash.toUpperCase()}`,
        sourceLabel: sourceHash.toUpperCase(),
      };
    }
    case PayloadType.GroupText: {
      const contactKey = packet.decrypted_info?.contact_key?.toUpperCase() ?? null;
      if (contactKey) {
        return {
          sourceKey: contactKey,
          sourceLabel: packet.decrypted_info?.sender || toSourceLabel(contactKey),
        };
      }
      if (packet.decrypted_info?.sender) {
        return {
          sourceKey: `name:${packet.decrypted_info.sender.toLowerCase()}`,
          sourceLabel: packet.decrypted_info.sender,
        };
      }
      return { sourceKey: null, sourceLabel: null };
    }
    case PayloadType.AnonRequest: {
      const senderPublicKey = (decoded.payload.decoded as { senderPublicKey?: string })
        .senderPublicKey;
      if (!senderPublicKey) return { sourceKey: null, sourceLabel: null };
      return {
        sourceKey: senderPublicKey.toUpperCase(),
        sourceLabel: senderPublicKey.slice(0, 12).toUpperCase(),
      };
    }
    default: {
      const fallbackContactKey = packet.decrypted_info?.contact_key?.toUpperCase() ?? null;
      if (fallbackContactKey) {
        return {
          sourceKey: fallbackContactKey,
          sourceLabel: packet.decrypted_info?.sender || toSourceLabel(fallbackContactKey),
        };
      }
      return { sourceKey: null, sourceLabel: null };
    }
  }
}

export function summarizeRawPacketForStats(packet: RawPacket): RawPacketStatsObservation {
  try {
    const decoded = MeshCoreDecoder.decode(packet.data);
    const pathTokens = decoded.isValid ? getPathTokens(decoded) : [];
    const payloadType = decoded.isValid
      ? Utils.getPayloadTypeName(decoded.payloadType)
      : packet.payload_type;
    const routeType = decoded.isValid ? Utils.getRouteTypeName(decoded.routeType) : 'Unknown';
    const sourceInfo = getSourceInfo(packet, decoded);

    return {
      observationKey: getRawPacketObservationKey(packet),
      timestamp: packet.timestamp,
      payloadType,
      routeType,
      decrypted: packet.decrypted,
      rssi: packet.rssi,
      snr: packet.snr,
      sourceKey: sourceInfo.sourceKey,
      sourceLabel: sourceInfo.sourceLabel,
      pathTokenCount: pathTokens.length,
      pathSignature: pathTokens.length > 0 ? pathTokens.join('>') : null,
      hopByteWidth: pathTokens.length > 0 ? (decoded.pathHashSize ?? 1) : null,
    };
  } catch {
    return {
      observationKey: getRawPacketObservationKey(packet),
      timestamp: packet.timestamp,
      payloadType: packet.payload_type,
      routeType: 'Unknown',
      decrypted: packet.decrypted,
      rssi: packet.rssi,
      snr: packet.snr,
      sourceKey: null,
      sourceLabel: null,
      pathTokenCount: 0,
      pathSignature: null,
      hopByteWidth: null,
    };
  }
}

function inferHopByteWidth(packet: RawPacketStatsObservation): number | null {
  if (packet.pathTokenCount <= 0) {
    return null;
  }
  if (packet.hopByteWidth && packet.hopByteWidth > 0) {
    return packet.hopByteWidth;
  }
  const firstToken = packet.pathSignature?.split('>')[0] ?? null;
  if (!firstToken || firstToken.length % 2 !== 0) {
    return null;
  }
  const inferred = firstToken.length / 2;
  return inferred >= 1 && inferred <= 3 ? inferred : null;
}

function share(count: number, total: number): number {
  if (total <= 0) return 0;
  return count / total;
}

function createCountsMap(labels: readonly string[]): Map<string, number> {
  return new Map(labels.map((label) => [label, 0]));
}

function rankedBreakdown(counts: Map<string, number>, total: number): RankedPacketStat[] {
  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([label, count]) => ({ label, count, share: share(count, total) }));
}

function orderedBreakdown(counts: Map<string, number>, total: number): RankedPacketStat[] {
  return Array.from(counts.entries()).map(([label, count]) => ({
    label,
    count,
    share: share(count, total),
  }));
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) {
    return sorted[mid];
  }
  return (sorted[mid - 1] + sorted[mid]) / 2;
}

function formatTimelineLabel(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });
}

function getHopProfileBucket(pathTokenCount: number): string {
  if (pathTokenCount <= 0) {
    return '0';
  }
  if (pathTokenCount === 1) {
    return '1';
  }
  if (pathTokenCount <= 5) {
    return '2-5';
  }
  if (pathTokenCount <= 10) {
    return '6-10';
  }
  if (pathTokenCount <= 15) {
    return '11-15';
  }
  if (pathTokenCount <= 20) {
    return '16-20';
  }
  if (pathTokenCount <= 31) {
    return '21-31';
  }
  return '32+';
}

export function buildRawPacketStatsSnapshot(
  session: RawPacketStatsSessionState,
  window: RawPacketStatsWindow,
  nowSec: number = Math.floor(Date.now() / 1000)
): RawPacketStatsSnapshot {
  const sessionStartedSec = Math.floor(session.sessionStartedAt / 1000);
  const windowSeconds = window === 'session' ? null : RAW_PACKET_STATS_WINDOW_SECONDS[window];
  const windowStart = windowSeconds === null ? sessionStartedSec : nowSec - windowSeconds;
  const packets = session.observations.filter((packet) => packet.timestamp >= windowStart);
  const packetCount = packets.length;
  const uniqueSources = new Set(packets.map((packet) => packet.sourceKey).filter(Boolean)).size;
  const decryptedCount = packets.filter((packet) => packet.decrypted).length;
  const undecryptedCount = packetCount - decryptedCount;
  const pathBearingCount = packets.filter((packet) => packet.pathTokenCount > 0).length;
  const distinctPaths = new Set(
    packets.map((packet) => packet.pathSignature).filter((value): value is string => Boolean(value))
  ).size;
  const effectiveCoverageSeconds =
    windowSeconds ?? Math.max(1, nowSec - Math.min(sessionStartedSec, nowSec));
  const packetsPerMinute = packetCount / Math.max(effectiveCoverageSeconds / 60, 1 / 60);

  const payloadCounts = createCountsMap(KNOWN_PAYLOAD_TYPES);
  const routeCounts = createCountsMap(KNOWN_ROUTE_TYPES);
  const hopCounts = new Map<string, number>([
    ['0', 0],
    ['1', 0],
    ['2-5', 0],
    ['6-10', 0],
    ['11-15', 0],
    ['16-20', 0],
    ['21-31', 0],
    ['32+', 0],
  ]);
  const hopByteWidthCounts = new Map<string, number>([
    ['No path', 0],
    ['1 byte / hop', 0],
    ['2 bytes / hop', 0],
    ['3 bytes / hop', 0],
    ['Unknown width', 0],
  ]);
  const neighborMap = new Map<string, NeighborStat>();
  const rssiValues: number[] = [];
  const rssiBucketCounts = new Map<string, number>([
    ['Strong (>-70 dBm)', 0],
    ['Okay (-70 to -85 dBm)', 0],
    ['Weak (<-85 dBm)', 0],
  ]);

  for (const packet of packets) {
    payloadCounts.set(packet.payloadType, (payloadCounts.get(packet.payloadType) ?? 0) + 1);
    routeCounts.set(packet.routeType, (routeCounts.get(packet.routeType) ?? 0) + 1);

    const hopProfileBucket = getHopProfileBucket(packet.pathTokenCount);
    hopCounts.set(hopProfileBucket, (hopCounts.get(hopProfileBucket) ?? 0) + 1);

    const hopByteWidth = inferHopByteWidth(packet);
    if (packet.pathTokenCount <= 0) {
      hopByteWidthCounts.set('No path', (hopByteWidthCounts.get('No path') ?? 0) + 1);
    } else if (hopByteWidth === 1) {
      hopByteWidthCounts.set('1 byte / hop', (hopByteWidthCounts.get('1 byte / hop') ?? 0) + 1);
    } else if (hopByteWidth === 2) {
      hopByteWidthCounts.set('2 bytes / hop', (hopByteWidthCounts.get('2 bytes / hop') ?? 0) + 1);
    } else if (hopByteWidth === 3) {
      hopByteWidthCounts.set('3 bytes / hop', (hopByteWidthCounts.get('3 bytes / hop') ?? 0) + 1);
    } else {
      hopByteWidthCounts.set('Unknown width', (hopByteWidthCounts.get('Unknown width') ?? 0) + 1);
    }

    if (packet.sourceKey && packet.sourceLabel) {
      const existing = neighborMap.get(packet.sourceKey);
      if (!existing) {
        neighborMap.set(packet.sourceKey, {
          key: packet.sourceKey,
          label: packet.sourceLabel,
          count: 1,
          bestRssi: packet.rssi,
          lastSeen: packet.timestamp,
        });
      } else {
        existing.count += 1;
        existing.lastSeen = Math.max(existing.lastSeen, packet.timestamp);
        if (
          packet.rssi !== null &&
          (existing.bestRssi === null || packet.rssi > existing.bestRssi)
        ) {
          existing.bestRssi = packet.rssi;
        }
      }
    }

    if (packet.rssi !== null) {
      rssiValues.push(packet.rssi);
      if (packet.rssi > -70) {
        rssiBucketCounts.set(
          'Strong (>-70 dBm)',
          (rssiBucketCounts.get('Strong (>-70 dBm)') ?? 0) + 1
        );
      } else if (packet.rssi >= -85) {
        rssiBucketCounts.set(
          'Okay (-70 to -85 dBm)',
          (rssiBucketCounts.get('Okay (-70 to -85 dBm)') ?? 0) + 1
        );
      } else {
        rssiBucketCounts.set('Weak (<-85 dBm)', (rssiBucketCounts.get('Weak (<-85 dBm)') ?? 0) + 1);
      }
    }
  }

  const averageRssi =
    rssiValues.length > 0
      ? rssiValues.reduce((sum, value) => sum + value, 0) / rssiValues.length
      : null;
  const bestRssi = rssiValues.length > 0 ? Math.max(...rssiValues) : null;
  const medianRssi = median(rssiValues);
  const neighbors = Array.from(neighborMap.values());
  const strongestNeighbors = [...neighbors]
    .filter((neighbor) => neighbor.bestRssi !== null)
    .sort(
      (a, b) =>
        (b.bestRssi ?? Number.NEGATIVE_INFINITY) - (a.bestRssi ?? Number.NEGATIVE_INFINITY) ||
        b.count - a.count ||
        a.label.localeCompare(b.label)
    )
    .slice(0, 5);
  const mostActiveNeighbors = [...neighbors]
    .sort((a, b) => b.count - a.count || b.lastSeen - a.lastSeen || a.label.localeCompare(b.label))
    .slice(0, 5);
  const newestNeighbors = [...neighbors]
    .sort((a, b) => b.lastSeen - a.lastSeen || b.count - a.count || a.label.localeCompare(b.label))
    .slice(0, 5);

  const oldestStoredTimestamp = session.observations[0]?.timestamp ?? null;
  const detailedCoverageStart =
    session.trimmedObservationCount > 0 ? (oldestStoredTimestamp ?? nowSec) : sessionStartedSec;
  const windowFullyCovered =
    window === 'session'
      ? session.trimmedObservationCount === 0
      : detailedCoverageStart <= windowStart;
  const coverageStart = Math.max(windowStart, detailedCoverageStart);
  const coverageSeconds =
    window === 'session'
      ? Math.max(1, nowSec - detailedCoverageStart)
      : Math.max(1, nowSec - coverageStart);

  const timelineSpanSeconds = Math.max(
    windowSeconds ?? Math.max(60, nowSec - sessionStartedSec),
    60
  );
  const timelineBinCount = 10;
  const binWidth = Math.max(1, timelineSpanSeconds / timelineBinCount);
  const timeline = Array.from({ length: timelineBinCount }, (_, index) => {
    const start = Math.floor(windowStart + index * binWidth);
    return {
      label: formatTimelineLabel(start),
      total: 0,
      countsByType: {} as Record<string, number>,
    };
  });

  for (const packet of packets) {
    const rawIndex = Math.floor((packet.timestamp - windowStart) / binWidth);
    const index = Math.max(0, Math.min(timelineBinCount - 1, rawIndex));
    const bin = timeline[index];
    bin.total += 1;
    bin.countsByType[packet.payloadType] = (bin.countsByType[packet.payloadType] ?? 0) + 1;
  }

  return {
    window,
    nowSec,
    packets,
    packetCount,
    packetsPerMinute,
    uniqueSources,
    decryptedCount,
    undecryptedCount,
    decryptRate: share(decryptedCount, packetCount),
    pathBearingCount,
    pathBearingRate: share(pathBearingCount, packetCount),
    distinctPaths,
    payloadBreakdown: rankedBreakdown(payloadCounts, packetCount),
    routeBreakdown: rankedBreakdown(routeCounts, packetCount),
    topPacketTypes: rankedBreakdown(payloadCounts, packetCount).slice(0, 5),
    hopProfile: orderedBreakdown(hopCounts, packetCount),
    hopByteWidthProfile: rankedBreakdown(hopByteWidthCounts, packetCount),
    strongestNeighbors,
    mostActiveNeighbors,
    newestNeighbors,
    averageRssi,
    medianRssi,
    bestRssi,
    rssiBuckets: rankedBreakdown(rssiBucketCounts, rssiValues.length),
    coverageSeconds,
    windowFullyCovered,
    oldestStoredTimestamp,
    timeline,
  };
}
