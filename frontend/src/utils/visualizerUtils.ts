import { MeshCoreDecoder, PayloadType } from '@michaelhart/meshcore-decoder';
import { CONTACT_TYPE_REPEATER, type Contact, type RawPacket } from '../types';
import { hashString } from './contactAvatar';

// =============================================================================
// TYPES
// =============================================================================

export type NodeType = 'self' | 'repeater' | 'client';
type PacketLabel = 'AD' | 'GT' | 'DM' | 'ACK' | 'TR' | 'RQ' | 'RS' | '?';

export interface Particle {
  linkKey: string;
  progress: number;
  speed: number;
  color: string;
  label: PacketLabel;
  fromNodeId: string;
  toNodeId: string;
}

interface ObservedPath {
  nodes: string[];
  snr: number | null;
  timestamp: number;
}

export interface PendingPacket {
  key: string;
  label: PacketLabel;
  paths: ObservedPath[];
  firstSeen: number;
  expiresAt: number;
}

export interface ParsedPacket {
  payloadType: number;
  messageHash: string | null;
  pathBytes: string[];
  srcHash: string | null;
  dstHash: string | null;
  advertPubkey: string | null;
  groupTextSender: string | null;
  anonRequestPubkey: string | null;
}

// Traffic pattern tracking for smarter repeater disambiguation
interface TrafficObservation {
  source: string; // Node that originated traffic (could be resolved node ID or ambiguous)
  nextHop: string | null; // Next hop after this repeater (null if final hop before self)
  timestamp: number;
}

export interface RepeaterTrafficData {
  hopKey: string; // The observed hop token (e.g. "32", "aa11", or "bbccdd")
  observations: TrafficObservation[];
}

// Analysis result for whether to split an ambiguous repeater
interface RepeaterSplitAnalysis {
  shouldSplit: boolean;
  // If shouldSplit, maps nextHop -> the sources that exclusively route through it
  disjointGroups: Map<string, Set<string>> | null;
}

// =============================================================================
// CONSTANTS
// =============================================================================

export const COLORS = {
  background: '#0a0a0a',
  link: '#4b5563',
  ambiguous: '#9ca3af',
  particleAD: '#f59e0b', // amber - advertisements
  particleGT: '#06b6d4', // cyan - group text
  particleDM: '#8b5cf6', // purple - direct messages
  particleACK: '#22c55e', // green - acknowledgments
  particleTR: '#f97316', // orange - trace packets
  particleRQ: '#ec4899', // pink - requests
  particleRS: '#14b8a6', // teal - responses
  particleUnknown: '#6b7280', // gray - unknown
} as const;

export const PARTICLE_COLOR_MAP: Record<PacketLabel, string> = {
  AD: COLORS.particleAD,
  GT: COLORS.particleGT,
  DM: COLORS.particleDM,
  ACK: COLORS.particleACK,
  TR: COLORS.particleTR,
  RQ: COLORS.particleRQ,
  RS: COLORS.particleRS,
  '?': COLORS.particleUnknown,
};

export const PARTICLE_SPEED = 0.008;
// Traffic pattern analysis thresholds
// Be conservative - once split, we can't unsplit, so require strong evidence
const MIN_OBSERVATIONS_TO_SPLIT = 20; // Need at least this many unique sources per next-hop group
const MAX_TRAFFIC_OBSERVATIONS = 200; // Per ambiguous prefix, to limit memory
const TRAFFIC_OBSERVATION_MAX_AGE_MS = 30 * 60 * 1000; // 30 minutes - old observations are pruned

export const PACKET_LEGEND_ITEMS = [
  { label: 'AD', color: COLORS.particleAD, description: 'Advertisement' },
  { label: 'GT', color: COLORS.particleGT, description: 'Group Text' },
  { label: 'DM', color: COLORS.particleDM, description: 'Direct Message' },
  { label: 'ACK', color: COLORS.particleACK, description: 'Acknowledgment' },
  { label: 'TR', color: COLORS.particleTR, description: 'Trace' },
  { label: 'RQ', color: COLORS.particleRQ, description: 'Request' },
  { label: 'RS', color: COLORS.particleRS, description: 'Response' },
  { label: '?', color: COLORS.particleUnknown, description: 'Other' },
] as const;

export interface PathStep {
  nodeId: string | null;
  markHiddenLinkWhenOmitted?: boolean;
  hiddenLabel?: string | null;
}

function normalizeHopToken(hop: string | null | undefined): string | null {
  const normalized = hop?.trim().toLowerCase() ?? '';
  return normalized.length > 0 ? normalized : null;
}

export function buildAmbiguousRepeaterNodeId(hop: string, nextHop?: string | null): string {
  const hopKey = normalizeHopToken(hop);
  if (!hopKey) {
    return '?';
  }

  const nextHopKey = normalizeHopToken(nextHop);
  return nextHopKey ? `?${hopKey}:>${nextHopKey}` : `?${hopKey}`;
}

export function buildAmbiguousRepeaterLabel(hop: string, nextHop?: string | null): string {
  const hopKey = normalizeHopToken(hop)?.toUpperCase();
  if (!hopKey) {
    return '?';
  }

  const nextHopKey = normalizeHopToken(nextHop)?.toUpperCase();
  return nextHopKey ? `${hopKey}:>${nextHopKey}` : hopKey;
}

// =============================================================================
// UTILITY FUNCTIONS (Data Layer)
// =============================================================================

export function parsePacket(hexData: string): ParsedPacket | null {
  try {
    const decoded = MeshCoreDecoder.decode(hexData);
    if (!decoded.isValid) return null;
    const tracePayload =
      decoded.payloadType === PayloadType.Trace && decoded.payload.decoded
        ? (decoded.payload.decoded as { pathHashes?: string[] })
        : null;

    const result: ParsedPacket = {
      payloadType: decoded.payloadType,
      messageHash: decoded.messageHash || null,
      // TRACE reuses the outer packet path field for SNR samples, not hop identities.
      // For visualization, use the trace payload's actual node hashes instead.
      pathBytes: tracePayload?.pathHashes || decoded.path || [],
      srcHash: null,
      dstHash: null,
      advertPubkey: null,
      groupTextSender: null,
      anonRequestPubkey: null,
    };

    if (decoded.payloadType === PayloadType.TextMessage && decoded.payload.decoded) {
      const payload = decoded.payload.decoded as { sourceHash?: string; destinationHash?: string };
      result.srcHash = payload.sourceHash || null;
      result.dstHash = payload.destinationHash || null;
    } else if (decoded.payloadType === PayloadType.Advert && decoded.payload.decoded) {
      result.advertPubkey = (decoded.payload.decoded as { publicKey?: string }).publicKey || null;
    } else if (decoded.payloadType === PayloadType.GroupText && decoded.payload.decoded) {
      const payload = decoded.payload.decoded as { decrypted?: { sender?: string } };
      result.groupTextSender = payload.decrypted?.sender || null;
    } else if (decoded.payloadType === PayloadType.AnonRequest && decoded.payload.decoded) {
      const payload = decoded.payload.decoded as { senderPublicKey?: string };
      result.anonRequestPubkey = payload.senderPublicKey || null;
    }

    return result;
  } catch {
    return null;
  }
}

export function getPacketLabel(payloadType: number): PacketLabel {
  switch (payloadType) {
    case PayloadType.Advert:
      return 'AD';
    case PayloadType.GroupText:
      return 'GT';
    case PayloadType.TextMessage:
      return 'DM';
    case PayloadType.Ack:
      return 'ACK';
    case PayloadType.Trace:
      return 'TR';
    case PayloadType.Request:
    case PayloadType.AnonRequest:
      return 'RQ';
    case PayloadType.Response:
      return 'RS';
    default:
      return '?';
  }
}

export function generatePacketKey(parsed: ParsedPacket, rawPacket: RawPacket): string {
  const contentHash = (
    parsed.messageHash || hashString(rawPacket.data).toString(16).padStart(8, '0')
  ).slice(0, 8);

  if (parsed.payloadType === PayloadType.Advert && parsed.advertPubkey) {
    return `ad:${parsed.advertPubkey.slice(0, 12)}`;
  }
  if (parsed.payloadType === PayloadType.GroupText) {
    const sender = parsed.groupTextSender || rawPacket.decrypted_info?.sender || '?';
    const channel = rawPacket.decrypted_info?.channel_name || '?';
    return `gt:${channel}:${sender}:${contentHash}`;
  }
  if (parsed.payloadType === PayloadType.TextMessage) {
    return `dm:${parsed.srcHash || '?'}:${parsed.dstHash || '?'}:${contentHash}`;
  }
  if (parsed.payloadType === PayloadType.AnonRequest && parsed.anonRequestPubkey) {
    return `rq:${parsed.anonRequestPubkey.slice(0, 12)}:${contentHash}`;
  }
  return `other:${contentHash}`;
}

export function getLinkId<
  T extends { source: string | { id: string }; target: string | { id: string } },
>(link: T): { sourceId: string; targetId: string } {
  return {
    sourceId: typeof link.source === 'string' ? link.source : link.source.id,
    targetId: typeof link.target === 'string' ? link.target : link.target.id,
  };
}

export function buildLinkKey(sourceId: string, targetId: string): string {
  return [sourceId, targetId].sort().join('->');
}

export function getNodeType(contact: Contact | null | undefined): NodeType {
  return contact?.type === CONTACT_TYPE_REPEATER ? 'repeater' : 'client';
}

export function dedupeConsecutive<T>(arr: T[]): T[] {
  return arr.filter((item, i) => i === 0 || item !== arr[i - 1]);
}

export function compactPathSteps(steps: PathStep[]): {
  nodes: string[];
  dashedLinkDetails: Map<string, string[]>;
} {
  const nodes: string[] = [];
  const dashedLinkDetails = new Map<string, string[]>();
  let pendingHiddenLink = false;
  let pendingHiddenLabels: string[] = [];

  for (const step of steps) {
    if (step.nodeId) {
      const previousNodeId = nodes.length > 0 ? nodes[nodes.length - 1] : null;
      if (previousNodeId && pendingHiddenLink && previousNodeId !== step.nodeId) {
        const key = buildLinkKey(previousNodeId, step.nodeId);
        const existing = dashedLinkDetails.get(key) ?? [];
        for (const label of pendingHiddenLabels) {
          if (!existing.includes(label)) {
            existing.push(label);
          }
        }
        dashedLinkDetails.set(key, existing);
      }
      if (previousNodeId !== step.nodeId) {
        nodes.push(step.nodeId);
      }
      pendingHiddenLink = false;
      pendingHiddenLabels = [];
      continue;
    }

    if (step.markHiddenLinkWhenOmitted && nodes.length > 0) {
      pendingHiddenLink = true;
      if (step.hiddenLabel && !pendingHiddenLabels.includes(step.hiddenLabel)) {
        pendingHiddenLabels.push(step.hiddenLabel);
      }
    }
  }

  return { nodes, dashedLinkDetails };
}

/**
 * Analyze traffic patterns for an ambiguous repeater prefix to determine if it
 * should be split into multiple nodes.
 */
export function analyzeRepeaterTraffic(data: RepeaterTrafficData): RepeaterSplitAnalysis {
  const now = Date.now();

  // Filter out old observations
  const recentObservations = data.observations.filter(
    (obs) => now - obs.timestamp < TRAFFIC_OBSERVATION_MAX_AGE_MS
  );

  // Group by nextHop (use "self" for null nextHop - final repeater)
  const byNextHop = new Map<string, Set<string>>();
  for (const obs of recentObservations) {
    const hopKey = obs.nextHop ?? 'self';
    if (!byNextHop.has(hopKey)) {
      byNextHop.set(hopKey, new Set());
    }
    byNextHop.get(hopKey)!.add(obs.source);
  }

  // If only one nextHop group, no need to split
  if (byNextHop.size <= 1) {
    return { shouldSplit: false, disjointGroups: null };
  }

  // Check if any source appears in multiple groups (evidence of hub behavior)
  const allSources = new Map<string, string[]>(); // source -> list of nextHops it uses
  for (const [nextHop, sources] of byNextHop) {
    for (const source of sources) {
      if (!allSources.has(source)) {
        allSources.set(source, []);
      }
      allSources.get(source)!.push(nextHop);
    }
  }

  // If any source routes to multiple nextHops, this is a hub - don't split
  for (const [, nextHops] of allSources) {
    if (nextHops.length > 1) {
      return { shouldSplit: false, disjointGroups: null };
    }
  }

  // Check if we have enough observations in each group to be confident
  for (const [, sources] of byNextHop) {
    if (sources.size < MIN_OBSERVATIONS_TO_SPLIT) {
      // Not enough evidence yet - be conservative, don't split
      return { shouldSplit: false, disjointGroups: null };
    }
  }

  // Source sets are disjoint and we have enough data - split!
  return { shouldSplit: true, disjointGroups: byNextHop };
}

/**
 * Record a traffic observation for an ambiguous repeater prefix.
 * Prunes old observations and limits total count.
 */
export function recordTrafficObservation(
  trafficData: Map<string, RepeaterTrafficData>,
  hopKey: string,
  source: string,
  nextHop: string | null
): void {
  const normalizedHopKey = normalizeHopToken(hopKey);
  if (!normalizedHopKey) {
    return;
  }

  const normalizedNextHop = normalizeHopToken(nextHop);
  const now = Date.now();

  if (!trafficData.has(normalizedHopKey)) {
    trafficData.set(normalizedHopKey, { hopKey: normalizedHopKey, observations: [] });
  }

  const data = trafficData.get(normalizedHopKey)!;

  // Add new observation
  data.observations.push({ source, nextHop: normalizedNextHop, timestamp: now });

  // Prune old observations
  data.observations = data.observations.filter(
    (obs) => now - obs.timestamp < TRAFFIC_OBSERVATION_MAX_AGE_MS
  );

  // Limit total count
  if (data.observations.length > MAX_TRAFFIC_OBSERVATIONS) {
    data.observations = data.observations.slice(-MAX_TRAFFIC_OBSERVATIONS);
  }
}
