import { PayloadType } from '@michaelhart/meshcore-decoder';

import {
  CONTACT_TYPE_REPEATER,
  type Contact,
  type ContactAdvertPathSummary,
  type RadioConfig,
  type RawPacket,
} from '../types';
import {
  analyzeRepeaterTraffic,
  buildAmbiguousRepeaterLabel,
  buildAmbiguousRepeaterNodeId,
  buildLinkKey,
  compactPathSteps,
  dedupeConsecutive,
  getNodeType,
  getPacketLabel,
  parsePacket,
  recordTrafficObservation,
  type NodeType,
  type ParsedPacket,
  type RepeaterTrafficData,
} from '../utils/visualizerUtils';
import { normalizePacketTimestampMs } from '../components/visualizer/shared';

interface ContactIndex {
  byPrefix12: Map<string, Contact>;
  byName: Map<string, Contact>;
  byPrefix: Map<string, Contact[]>;
}

interface AdvertPathIndex {
  byRepeater: Map<string, ContactAdvertPathSummary['paths']>;
}

export interface PacketNetworkContext {
  advertPathIndex: AdvertPathIndex;
  contactIndex: ContactIndex;
  myPrefix: string | null;
  splitAmbiguousByTraffic: boolean;
  useAdvertPathHints: boolean;
}

export interface PacketNetworkVisibilityOptions {
  showAmbiguousNodes: boolean;
  showAmbiguousPaths: boolean;
  collapseLikelyKnownSiblingRepeaters: boolean;
}

export interface PacketNetworkNode {
  id: string;
  name: string | null;
  type: NodeType;
  isAmbiguous: boolean;
  lastActivity: number;
  lastActivityReason?: string;
  lastSeen?: number | null;
  probableIdentity?: string | null;
  probableIdentityNodeId?: string | null;
  ambiguousNames?: string[];
}

export interface PacketNetworkLink {
  lastActivity: number;
  sourceId: string;
  targetId: string;
}

export interface ProjectedPacketNetworkLink extends PacketNetworkLink {
  hasDirectObservation: boolean;
  hasHiddenIntermediate: boolean;
  hiddenHopLabels: string[];
}

export interface PacketNetworkObservation {
  activityAtMs: number;
  nodes: string[];
}

export interface PacketNetworkState {
  links: Map<string, PacketNetworkLink>;
  neighborIds: Map<string, Set<string>>;
  nodes: Map<string, PacketNetworkNode>;
  observations: PacketNetworkObservation[];
  trafficPatterns: Map<string, RepeaterTrafficData>;
}

export interface PacketNetworkIngestResult {
  activityAtMs: number;
  canonicalPath: string[];
  label: ReturnType<typeof getPacketLabel>;
  parsed: ParsedPacket;
}

export interface ProjectedPacketNetworkPath {
  dashedLinkDetails: Map<string, string[]>;
  nodes: string[];
}

export interface PacketNetworkProjection {
  links: Map<string, ProjectedPacketNetworkLink>;
  nodes: Map<string, PacketNetworkNode>;
  renderedNodeIds: Set<string>;
}

export function buildPacketNetworkContext({
  config,
  contacts,
  repeaterAdvertPaths,
  splitAmbiguousByTraffic,
  useAdvertPathHints,
}: {
  config: RadioConfig | null;
  contacts: Contact[];
  repeaterAdvertPaths: ContactAdvertPathSummary[];
  splitAmbiguousByTraffic: boolean;
  useAdvertPathHints: boolean;
}): PacketNetworkContext {
  const byPrefix12 = new Map<string, Contact>();
  const byName = new Map<string, Contact>();
  const byPrefix = new Map<string, Contact[]>();

  for (const contact of contacts) {
    const prefix12 = contact.public_key.slice(0, 12).toLowerCase();
    byPrefix12.set(prefix12, contact);

    if (contact.name && !byName.has(contact.name)) {
      byName.set(contact.name, contact);
    }

    for (let len = 1; len <= 12; len++) {
      const prefix = prefix12.slice(0, len);
      const matches = byPrefix.get(prefix);
      if (matches) {
        matches.push(contact);
      } else {
        byPrefix.set(prefix, [contact]);
      }
    }
  }

  const byRepeater = new Map<string, ContactAdvertPathSummary['paths']>();
  for (const summary of repeaterAdvertPaths) {
    const key = summary.public_key.slice(0, 12).toLowerCase();
    byRepeater.set(key, summary.paths);
  }

  return {
    contactIndex: { byPrefix12, byName, byPrefix },
    advertPathIndex: { byRepeater },
    myPrefix: config?.public_key?.slice(0, 12).toLowerCase() || null,
    splitAmbiguousByTraffic,
    useAdvertPathHints,
  };
}

export function createPacketNetworkState(selfName: string = 'Me'): PacketNetworkState {
  const now = Date.now();
  return {
    nodes: new Map([
      [
        'self',
        {
          id: 'self',
          name: selfName,
          type: 'self',
          isAmbiguous: false,
          lastActivity: now,
        },
      ],
    ]),
    links: new Map(),
    neighborIds: new Map(),
    observations: [],
    trafficPatterns: new Map(),
  };
}

export function ensureSelfNode(state: PacketNetworkState, selfName: string = 'Me'): void {
  const existing = state.nodes.get('self');
  if (existing) {
    existing.name = selfName;
    return;
  }
  state.nodes.set('self', {
    id: 'self',
    name: selfName,
    type: 'self',
    isAmbiguous: false,
    lastActivity: Date.now(),
  });
}

export function clearPacketNetworkState(
  state: PacketNetworkState,
  { selfName = 'Me' }: { selfName?: string } = {}
): void {
  state.links.clear();
  state.neighborIds.clear();
  state.observations = [];
  state.trafficPatterns.clear();

  const selfNode = state.nodes.get('self');
  state.nodes.clear();
  state.nodes.set('self', {
    id: 'self',
    name: selfName,
    type: 'self',
    isAmbiguous: false,
    lastActivity: Date.now(),
    lastActivityReason: undefined,
    lastSeen: null,
    probableIdentity: undefined,
    probableIdentityNodeId: undefined,
    ambiguousNames: undefined,
  });

  if (selfNode?.name && selfNode.name !== selfName) {
    state.nodes.get('self')!.name = selfName;
  }
}

function addOrUpdateNode(
  state: PacketNetworkState,
  {
    activityAtMs,
    ambiguousNames,
    id,
    isAmbiguous,
    lastSeen,
    name,
    probableIdentity,
    probableIdentityNodeId,
    type,
  }: {
    activityAtMs: number;
    ambiguousNames?: string[];
    id: string;
    isAmbiguous: boolean;
    lastSeen?: number | null;
    name: string | null;
    probableIdentity?: string | null;
    probableIdentityNodeId?: string | null;
    type: NodeType;
  }
): void {
  const existing = state.nodes.get(id);
  if (existing) {
    existing.lastActivity = Math.max(existing.lastActivity, activityAtMs);
    if (name) existing.name = name;
    if (probableIdentity !== undefined) existing.probableIdentity = probableIdentity;
    if (probableIdentityNodeId !== undefined) {
      existing.probableIdentityNodeId = probableIdentityNodeId;
    }
    if (ambiguousNames) existing.ambiguousNames = ambiguousNames;
    if (lastSeen !== undefined) existing.lastSeen = lastSeen;
    return;
  }

  state.nodes.set(id, {
    id,
    name,
    type,
    isAmbiguous,
    lastActivity: activityAtMs,
    probableIdentity,
    probableIdentityNodeId,
    ambiguousNames,
    lastSeen,
  });
}

function addCanonicalLink(
  state: PacketNetworkState,
  sourceId: string,
  targetId: string,
  activityAtMs: number
): void {
  const key = buildLinkKey(sourceId, targetId);
  const existing = state.links.get(key);
  if (existing) {
    existing.lastActivity = Math.max(existing.lastActivity, activityAtMs);
  } else {
    state.links.set(key, { sourceId, targetId, lastActivity: activityAtMs });
  }
}

function upsertNeighbor(state: PacketNetworkState, sourceId: string, targetId: string): void {
  const ensureSet = (id: string) => {
    const existing = state.neighborIds.get(id);
    if (existing) return existing;
    const created = new Set<string>();
    state.neighborIds.set(id, created);
    return created;
  };

  ensureSet(sourceId).add(targetId);
  ensureSet(targetId).add(sourceId);
}

function pickLikelyRepeaterByAdvertPath(
  context: PacketNetworkContext,
  candidates: Contact[],
  nextPrefix: string | null
): Contact | null {
  const nextHop = nextPrefix?.toLowerCase() ?? null;
  const scored = candidates
    .map((candidate) => {
      const prefix12 = candidate.public_key.slice(0, 12).toLowerCase();
      const paths = context.advertPathIndex.byRepeater.get(prefix12) ?? [];
      let matchScore = 0;
      let totalScore = 0;

      for (const path of paths) {
        totalScore += path.heard_count;
        const pathNextHop = path.next_hop?.toLowerCase() ?? null;
        if (pathNextHop === nextHop) {
          matchScore += path.heard_count;
        }
      }

      return { candidate, matchScore, totalScore };
    })
    .filter((entry) => entry.totalScore > 0)
    .sort(
      (a, b) =>
        b.matchScore - a.matchScore ||
        b.totalScore - a.totalScore ||
        a.candidate.public_key.localeCompare(b.candidate.public_key)
    );

  if (scored.length === 0) return null;

  const top = scored[0];
  const second = scored[1] ?? null;

  if (top.matchScore < 2) return null;
  if (second && top.matchScore < second.matchScore * 2) return null;

  return top.candidate;
}

function resolveNode(
  state: PacketNetworkState,
  context: PacketNetworkContext,
  source: { type: 'prefix' | 'pubkey' | 'name'; value: string },
  isRepeater: boolean,
  showAmbiguous: boolean,
  activityAtMs: number,
  trafficContext?: { packetSource: string | null; nextPrefix: string | null }
): string | null {
  if (source.type === 'pubkey') {
    if (source.value.length < 12) return null;
    const nodeId = source.value.slice(0, 12).toLowerCase();
    if (context.myPrefix && nodeId === context.myPrefix) return 'self';
    const contact = context.contactIndex.byPrefix12.get(nodeId);
    addOrUpdateNode(state, {
      id: nodeId,
      name: contact?.name || null,
      type: getNodeType(contact),
      isAmbiguous: false,
      lastSeen: contact?.last_seen,
      activityAtMs,
    });
    return nodeId;
  }

  if (source.type === 'name') {
    const contact = context.contactIndex.byName.get(source.value) ?? null;
    if (contact) {
      const nodeId = contact.public_key.slice(0, 12).toLowerCase();
      if (context.myPrefix && nodeId === context.myPrefix) return 'self';
      addOrUpdateNode(state, {
        id: nodeId,
        name: contact.name,
        type: getNodeType(contact),
        isAmbiguous: false,
        lastSeen: contact.last_seen,
        activityAtMs,
      });
      return nodeId;
    }

    const nodeId = `name:${source.value}`;
    addOrUpdateNode(state, {
      id: nodeId,
      name: source.value,
      type: 'client',
      isAmbiguous: false,
      activityAtMs,
    });
    return nodeId;
  }

  const lookupValue = source.value.toLowerCase();
  const matches = context.contactIndex.byPrefix.get(lookupValue) ?? [];
  const contact = matches.length === 1 ? matches[0] : null;
  if (contact) {
    const nodeId = contact.public_key.slice(0, 12).toLowerCase();
    if (context.myPrefix && nodeId === context.myPrefix) return 'self';
    addOrUpdateNode(state, {
      id: nodeId,
      name: contact.name,
      type: getNodeType(contact),
      isAmbiguous: false,
      lastSeen: contact.last_seen,
      activityAtMs,
    });
    return nodeId;
  }

  if (!showAmbiguous) {
    return null;
  }

  const filtered = isRepeater
    ? matches.filter((candidate) => candidate.type === CONTACT_TYPE_REPEATER)
    : matches.filter((candidate) => candidate.type !== CONTACT_TYPE_REPEATER);

  if (filtered.length === 1) {
    const only = filtered[0];
    const nodeId = only.public_key.slice(0, 12).toLowerCase();
    addOrUpdateNode(state, {
      id: nodeId,
      name: only.name,
      type: getNodeType(only),
      isAmbiguous: false,
      lastSeen: only.last_seen,
      activityAtMs,
    });
    return nodeId;
  }

  if (filtered.length === 0 && !isRepeater) {
    return null;
  }

  const names = filtered.map((candidate) => candidate.name || candidate.public_key.slice(0, 8));
  const lastSeen = filtered.reduce(
    (max, candidate) =>
      candidate.last_seen && (!max || candidate.last_seen > max) ? candidate.last_seen : max,
    null as number | null
  );

  let nodeId = buildAmbiguousRepeaterNodeId(lookupValue);
  let displayName = buildAmbiguousRepeaterLabel(lookupValue);
  let probableIdentity: string | null = null;
  let probableIdentityNodeId: string | null = null;
  let ambiguousNames = names.length > 0 ? names : undefined;

  if (context.useAdvertPathHints && isRepeater && trafficContext) {
    const likely = pickLikelyRepeaterByAdvertPath(context, filtered, trafficContext.nextPrefix);
    if (likely) {
      const likelyName = likely.name || likely.public_key.slice(0, 12).toUpperCase();
      probableIdentity = likelyName;
      probableIdentityNodeId = likely.public_key.slice(0, 12).toLowerCase();
      displayName = likelyName;
      ambiguousNames = filtered
        .filter((candidate) => candidate.public_key !== likely.public_key)
        .map((candidate) => candidate.name || candidate.public_key.slice(0, 8));
    }
  }

  if (context.splitAmbiguousByTraffic && isRepeater && trafficContext) {
    const normalizedNext = trafficContext.nextPrefix?.toLowerCase() ?? null;

    if (trafficContext.packetSource) {
      recordTrafficObservation(
        state.trafficPatterns,
        lookupValue,
        trafficContext.packetSource,
        normalizedNext
      );
    }

    const trafficData = state.trafficPatterns.get(lookupValue);
    if (trafficData) {
      const analysis = analyzeRepeaterTraffic(trafficData);
      if (analysis.shouldSplit && normalizedNext) {
        nodeId = buildAmbiguousRepeaterNodeId(lookupValue, normalizedNext);
        if (!probableIdentity) {
          displayName = buildAmbiguousRepeaterLabel(lookupValue, normalizedNext);
        }
      }
    }
  }

  addOrUpdateNode(state, {
    id: nodeId,
    name: displayName,
    type: isRepeater ? 'repeater' : 'client',
    isAmbiguous: true,
    probableIdentity,
    probableIdentityNodeId,
    ambiguousNames,
    lastSeen,
    activityAtMs,
  });
  return nodeId;
}

export function buildCanonicalPathForPacket(
  state: PacketNetworkState,
  context: PacketNetworkContext,
  parsed: ParsedPacket,
  packet: RawPacket,
  activityAtMs: number
): string[] {
  const path: string[] = [];
  let packetSource: string | null = null;
  const isDm = parsed.payloadType === PayloadType.TextMessage;
  const isOutgoingDm =
    isDm && !!context.myPrefix && parsed.srcHash?.toLowerCase() === context.myPrefix;

  if (parsed.payloadType === PayloadType.Advert && parsed.advertPubkey) {
    const nodeId = resolveNode(
      state,
      context,
      { type: 'pubkey', value: parsed.advertPubkey },
      false,
      false,
      activityAtMs
    );
    if (nodeId) {
      path.push(nodeId);
      packetSource = nodeId;
    }
  } else if (parsed.payloadType === PayloadType.AnonRequest && parsed.anonRequestPubkey) {
    const nodeId = resolveNode(
      state,
      context,
      { type: 'pubkey', value: parsed.anonRequestPubkey },
      false,
      false,
      activityAtMs
    );
    if (nodeId) {
      path.push(nodeId);
      packetSource = nodeId;
    }
  } else if (parsed.payloadType === PayloadType.TextMessage && parsed.srcHash) {
    if (context.myPrefix && parsed.srcHash.toLowerCase() === context.myPrefix) {
      path.push('self');
      packetSource = 'self';
    } else {
      const nodeId = resolveNode(
        state,
        context,
        { type: 'prefix', value: parsed.srcHash },
        false,
        true,
        activityAtMs
      );
      if (nodeId) {
        path.push(nodeId);
        packetSource = nodeId;
      }
    }
  } else if (parsed.payloadType === PayloadType.GroupText) {
    const senderName = parsed.groupTextSender || packet.decrypted_info?.sender;
    if (senderName) {
      const nodeId = resolveNode(
        state,
        context,
        { type: 'name', value: senderName },
        false,
        false,
        activityAtMs
      );
      if (nodeId) {
        path.push(nodeId);
        packetSource = nodeId;
      }
    }
  }

  for (let i = 0; i < parsed.pathBytes.length; i++) {
    const nodeId = resolveNode(
      state,
      context,
      { type: 'prefix', value: parsed.pathBytes[i] },
      true,
      true,
      activityAtMs,
      { packetSource, nextPrefix: parsed.pathBytes[i + 1] || null }
    );
    if (nodeId) {
      path.push(nodeId);
    }
  }

  if (parsed.payloadType === PayloadType.TextMessage && parsed.dstHash) {
    if (context.myPrefix && parsed.dstHash.toLowerCase() === context.myPrefix) {
      path.push('self');
    } else if (!isOutgoingDm && path.length > 0) {
      path.push('self');
    }
  } else if (path.length > 0) {
    path.push('self');
  }

  return dedupeConsecutive(path);
}

export function ingestPacketIntoPacketNetwork(
  state: PacketNetworkState,
  context: PacketNetworkContext,
  packet: RawPacket
): PacketNetworkIngestResult | null {
  const parsed = parsePacket(packet.data);
  if (!parsed) return null;

  const activityAtMs = normalizePacketTimestampMs(packet.timestamp);
  const canonicalPath = buildCanonicalPathForPacket(state, context, parsed, packet, activityAtMs);
  if (canonicalPath.length < 2) {
    return null;
  }

  const label = getPacketLabel(parsed.payloadType);
  for (let i = 0; i < canonicalPath.length; i++) {
    const node = state.nodes.get(canonicalPath[i]);
    if (node && node.id !== 'self') {
      node.lastActivityReason = i === 0 ? `${label} source` : `Relayed ${label}`;
    }
  }

  state.observations.push({ nodes: canonicalPath, activityAtMs });

  for (let i = 0; i < canonicalPath.length - 1; i++) {
    if (canonicalPath[i] !== canonicalPath[i + 1]) {
      addCanonicalLink(state, canonicalPath[i], canonicalPath[i + 1], activityAtMs);
      upsertNeighbor(state, canonicalPath[i], canonicalPath[i + 1]);
    }
  }

  return { parsed, label, canonicalPath, activityAtMs };
}

export function isPacketNetworkNodeVisible(
  node: PacketNetworkNode | undefined,
  visibility: PacketNetworkVisibilityOptions
): boolean {
  if (!node) return false;
  if (node.id === 'self') return true;
  if (!node.isAmbiguous) return true;
  return node.type === 'repeater' ? visibility.showAmbiguousPaths : visibility.showAmbiguousNodes;
}

function buildKnownSiblingRepeaterAliasMap(
  state: PacketNetworkState,
  visibility: PacketNetworkVisibilityOptions
): Map<string, string> {
  if (!visibility.collapseLikelyKnownSiblingRepeaters || !visibility.showAmbiguousPaths) {
    return new Map();
  }

  const knownRepeaterNextHops = new Map<string, Set<string>>();
  for (const observation of state.observations) {
    for (let i = 0; i < observation.nodes.length - 1; i++) {
      const currentNode = state.nodes.get(observation.nodes[i]);
      if (!currentNode || currentNode.type !== 'repeater' || currentNode.isAmbiguous) {
        continue;
      }

      const nextNodeId = observation.nodes[i + 1];
      const existing = knownRepeaterNextHops.get(currentNode.id);
      if (existing) {
        existing.add(nextNodeId);
      } else {
        knownRepeaterNextHops.set(currentNode.id, new Set([nextNodeId]));
      }
    }
  }

  const aliases = new Map<string, string>();
  for (const observation of state.observations) {
    for (let i = 0; i < observation.nodes.length - 1; i++) {
      const currentNodeId = observation.nodes[i];
      const currentNode = state.nodes.get(currentNodeId);
      if (
        !currentNode ||
        currentNode.type !== 'repeater' ||
        !currentNode.isAmbiguous ||
        !currentNode.probableIdentityNodeId
      ) {
        continue;
      }

      const probableNode = state.nodes.get(currentNode.probableIdentityNodeId);
      if (!probableNode || probableNode.type !== 'repeater' || probableNode.isAmbiguous) {
        continue;
      }

      const nextNodeId = observation.nodes[i + 1];
      const probableNextHops = knownRepeaterNextHops.get(probableNode.id);
      if (probableNextHops?.has(nextNodeId)) {
        aliases.set(currentNodeId, probableNode.id);
      }
    }
  }

  return aliases;
}

function projectCanonicalPathWithAliases(
  state: PacketNetworkState,
  canonicalPath: string[],
  visibility: PacketNetworkVisibilityOptions,
  repeaterAliases: Map<string, string>
): ProjectedPacketNetworkPath {
  const projected = compactPathSteps(
    canonicalPath.map((nodeId, index) => {
      const node = state.nodes.get(nodeId);
      const visible = isPacketNetworkNodeVisible(node, visibility);
      return {
        nodeId: visible ? (repeaterAliases.get(nodeId) ?? nodeId) : null,
        // Only hidden repeater hops should imply a bridged dashed segment.
        // Hidden sender/recipient endpoints should disappear with their own edge.
        markHiddenLinkWhenOmitted:
          !visible &&
          !!node &&
          node.type === 'repeater' &&
          index > 0 &&
          index < canonicalPath.length - 1,
        hiddenLabel: null,
      };
    })
  );

  return {
    nodes: dedupeConsecutive(projected.nodes),
    dashedLinkDetails: projected.dashedLinkDetails,
  };
}

export function projectCanonicalPath(
  state: PacketNetworkState,
  canonicalPath: string[],
  visibility: PacketNetworkVisibilityOptions
): ProjectedPacketNetworkPath {
  return projectCanonicalPathWithAliases(
    state,
    canonicalPath,
    visibility,
    buildKnownSiblingRepeaterAliasMap(state, visibility)
  );
}

export function projectPacketNetwork(
  state: PacketNetworkState,
  visibility: PacketNetworkVisibilityOptions
): PacketNetworkProjection {
  const repeaterAliases = buildKnownSiblingRepeaterAliasMap(state, visibility);
  const nodes = new Map<string, PacketNetworkNode>();
  const selfNode = state.nodes.get('self');
  if (selfNode) {
    nodes.set('self', selfNode);
  }

  const links = new Map<string, ProjectedPacketNetworkLink>();

  for (const observation of state.observations) {
    const projected = projectCanonicalPathWithAliases(
      state,
      observation.nodes,
      visibility,
      repeaterAliases
    );
    if (projected.nodes.length < 2) continue;

    for (const nodeId of projected.nodes) {
      const node = state.nodes.get(nodeId);
      if (node) {
        nodes.set(nodeId, node);
      }
    }

    for (let i = 0; i < projected.nodes.length - 1; i++) {
      const sourceId = projected.nodes[i];
      const targetId = projected.nodes[i + 1];
      if (sourceId === targetId) continue;

      const key = buildLinkKey(sourceId, targetId);
      const hiddenIntermediate = projected.dashedLinkDetails.has(key);
      const existing = links.get(key);

      if (existing) {
        existing.lastActivity = Math.max(existing.lastActivity, observation.activityAtMs);
        if (hiddenIntermediate) {
          existing.hasHiddenIntermediate = true;
          for (const label of projected.dashedLinkDetails.get(key) ?? []) {
            if (!existing.hiddenHopLabels.includes(label)) {
              existing.hiddenHopLabels.push(label);
            }
          }
        } else {
          existing.hasDirectObservation = true;
        }
        continue;
      }

      links.set(key, {
        sourceId,
        targetId,
        lastActivity: observation.activityAtMs,
        hasDirectObservation: !hiddenIntermediate,
        hasHiddenIntermediate: hiddenIntermediate,
        hiddenHopLabels: [...(projected.dashedLinkDetails.get(key) ?? [])],
      });
    }
  }

  return {
    nodes,
    links,
    renderedNodeIds: new Set(nodes.keys()),
  };
}

export function prunePacketNetworkState(state: PacketNetworkState, cutoff: number): boolean {
  let pruned = false;

  for (const [id, node] of state.nodes) {
    if (id === 'self') continue;
    if (node.lastActivity < cutoff) {
      state.nodes.delete(id);
      pruned = true;
    }
  }

  if (!pruned) {
    return false;
  }

  for (const [key, link] of state.links) {
    if (!state.nodes.has(link.sourceId) || !state.nodes.has(link.targetId)) {
      state.links.delete(key);
    }
  }

  state.observations = state.observations.filter((observation) =>
    observation.nodes.every((nodeId) => state.nodes.has(nodeId))
  );

  state.neighborIds.clear();
  for (const link of state.links.values()) {
    upsertNeighbor(state, link.sourceId, link.targetId);
  }

  return true;
}

export function snapshotNeighborIds(state: PacketNetworkState): Map<string, string[]> {
  return new Map(
    Array.from(state.neighborIds.entries()).map(([nodeId, neighborIds]) => [
      nodeId,
      Array.from(neighborIds).sort(),
    ])
  );
}
