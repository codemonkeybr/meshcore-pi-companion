import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js';
import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceX,
  forceY,
  forceZ,
  type Simulation3D,
  type SimulationNodeDatum3D,
  type ForceLink3D,
} from 'd3-force-3d';
import type { SimulationLinkDatum } from 'd3-force';
import { PayloadType } from '@michaelhart/meshcore-decoder';
import { api } from '../api';
import {
  CONTACT_TYPE_REPEATER,
  type Contact,
  type RawPacket,
  type RadioConfig,
  type ContactAdvertPathSummary,
} from '../types';
import { getRawPacketObservationKey } from '../utils/rawPacketIdentity';
import { getVisualizerSettings, saveVisualizerSettings } from '../utils/visualizerSettings';
import { Checkbox } from './ui/checkbox';
import {
  type NodeType,
  type Particle,
  type PendingPacket,
  type RepeaterTrafficData,
  COLORS,
  PARTICLE_COLOR_MAP,
  PARTICLE_SPEED,
  PACKET_LEGEND_ITEMS,
  parsePacket,
  getPacketLabel,
  generatePacketKey,
  getLinkId,
  getNodeType,
  dedupeConsecutive,
  analyzeRepeaterTraffic,
  recordTrafficObservation,
} from '../utils/visualizerUtils';

// =============================================================================
// TYPES (local — extend d3-force-3d simulation datum types)
// =============================================================================

interface GraphNode extends SimulationNodeDatum3D {
  id: string;
  name: string | null;
  type: NodeType;
  isAmbiguous: boolean;
  lastActivity: number;
  lastActivityReason?: string;
  lastSeen?: number | null;
  probableIdentity?: string | null;
  ambiguousNames?: string[];
}

interface GraphLink extends SimulationLinkDatum<GraphNode> {
  source: string | GraphNode;
  target: string | GraphNode;
  lastActivity: number;
}

// =============================================================================
// 3D NODE COLORS
// =============================================================================

const NODE_COLORS = {
  self: 0x22c55e, // green
  repeater: 0x3b82f6, // blue
  client: 0xffffff, // white
  ambiguous: 0x9ca3af, // gray
} as const;

const NODE_LEGEND_ITEMS = [
  { color: '#22c55e', label: 'You', size: 14 },
  { color: '#3b82f6', label: 'Repeater', size: 10 },
  { color: '#ffffff', label: 'Node', size: 10 },
  { color: '#9ca3af', label: 'Ambiguous', size: 10 },
] as const;

function getBaseNodeColor(node: Pick<GraphNode, 'type' | 'isAmbiguous'>): number {
  if (node.type === 'self') return NODE_COLORS.self;
  if (node.type === 'repeater') return NODE_COLORS.repeater;
  return node.isAmbiguous ? NODE_COLORS.ambiguous : NODE_COLORS.client;
}

function growFloat32Buffer(
  current: Float32Array<ArrayBufferLike>,
  requiredLength: number
): Float32Array<ArrayBufferLike> {
  let nextLength = Math.max(12, current.length);
  while (nextLength < requiredLength) {
    nextLength *= 2;
  }
  return new Float32Array(nextLength);
}

function arraysEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

function formatRelativeTime(timestamp: number): string {
  const seconds = Math.floor((Date.now() - timestamp) / 1000);
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return secs > 0 ? `${minutes}m ${secs}s ago` : `${minutes}m ago`;
}

// =============================================================================
// DATA LAYER HOOK (3D variant)
// =============================================================================

interface UseVisualizerData3DOptions {
  packets: RawPacket[];
  contacts: Contact[];
  config: RadioConfig | null;
  repeaterAdvertPaths: ContactAdvertPathSummary[];
  showAmbiguousPaths: boolean;
  showAmbiguousNodes: boolean;
  useAdvertPathHints: boolean;
  splitAmbiguousByTraffic: boolean;
  chargeStrength: number;
  letEmDrift: boolean;
  particleSpeedMultiplier: number;
  observationWindowSec: number;
  pruneStaleNodes: boolean;
}

interface VisualizerData3D {
  nodes: Map<string, GraphNode>;
  links: Map<string, GraphLink>;
  particles: Particle[];
  stats: { processed: number; animated: number; nodes: number; links: number };
  expandContract: () => void;
  clearAndReset: () => void;
}

function useVisualizerData3D({
  packets,
  contacts,
  config,
  repeaterAdvertPaths,
  showAmbiguousPaths,
  showAmbiguousNodes,
  useAdvertPathHints,
  splitAmbiguousByTraffic,
  chargeStrength,
  letEmDrift,
  particleSpeedMultiplier,
  observationWindowSec,
  pruneStaleNodes,
}: UseVisualizerData3DOptions): VisualizerData3D {
  const nodesRef = useRef<Map<string, GraphNode>>(new Map());
  const linksRef = useRef<Map<string, GraphLink>>(new Map());
  const particlesRef = useRef<Particle[]>([]);
  const simulationRef = useRef<Simulation3D<GraphNode, GraphLink> | null>(null);
  const processedRef = useRef<Set<string>>(new Set());
  const pendingRef = useRef<Map<string, PendingPacket>>(new Map());
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const trafficPatternsRef = useRef<Map<string, RepeaterTrafficData>>(new Map());
  const speedMultiplierRef = useRef(particleSpeedMultiplier);
  const observationWindowRef = useRef(observationWindowSec * 1000);
  const stretchRafRef = useRef<number | null>(null);
  const [stats, setStats] = useState({ processed: 0, animated: 0, nodes: 0, links: 0 });

  const contactIndex = useMemo(() => {
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

    return { byPrefix12, byName, byPrefix };
  }, [contacts]);

  const advertPathIndex = useMemo(() => {
    const byRepeater = new Map<string, ContactAdvertPathSummary['paths']>();
    for (const summary of repeaterAdvertPaths) {
      const key = summary.public_key.slice(0, 12).toLowerCase();
      byRepeater.set(key, summary.paths);
    }
    return { byRepeater };
  }, [repeaterAdvertPaths]);

  // Keep refs in sync with props
  useEffect(() => {
    speedMultiplierRef.current = particleSpeedMultiplier;
  }, [particleSpeedMultiplier]);

  useEffect(() => {
    observationWindowRef.current = observationWindowSec * 1000;
  }, [observationWindowSec]);

  // Initialize simulation (3D — centered at origin)
  useEffect(() => {
    const sim = forceSimulation<GraphNode, GraphLink>([])
      .numDimensions(3)
      .force(
        'link',
        forceLink<GraphNode, GraphLink>([])
          .id((d) => d.id)
          .distance(120)
          .strength(0.3)
      )
      .force(
        'charge',
        forceManyBody<GraphNode>()
          .strength((d) => (d.id === 'self' ? -1200 : -200))
          .distanceMax(800)
      )
      .force('center', forceCenter(0, 0, 0))
      .force(
        'selfX',
        forceX<GraphNode>(0).strength((d) => (d.id === 'self' ? 0.1 : 0))
      )
      .force(
        'selfY',
        forceY<GraphNode>(0).strength((d) => (d.id === 'self' ? 0.1 : 0))
      )
      .force(
        'selfZ',
        forceZ<GraphNode>(0).strength((d) => (d.id === 'self' ? 0.1 : 0))
      )
      .alphaDecay(0.02)
      .velocityDecay(0.5)
      .alphaTarget(0.03);

    simulationRef.current = sim;
    return () => {
      sim.stop();
    };
  }, []);

  // Update simulation forces when charge changes
  useEffect(() => {
    const sim = simulationRef.current;
    if (!sim) return;

    sim.force(
      'charge',
      forceManyBody<GraphNode>()
        .strength((d) => (d.id === 'self' ? chargeStrength * 6 : chargeStrength))
        .distanceMax(800)
    );
    sim.alpha(0.3).restart();
  }, [chargeStrength]);

  // Update alphaTarget when drift preference changes
  useEffect(() => {
    const sim = simulationRef.current;
    if (!sim) return;
    sim.alphaTarget(letEmDrift ? 0.05 : 0);
  }, [letEmDrift]);

  // Ensure self node exists
  useEffect(() => {
    if (!nodesRef.current.has('self')) {
      nodesRef.current.set('self', {
        id: 'self',
        name: config?.name || 'Me',
        type: 'self',
        isAmbiguous: false,
        lastActivity: Date.now(),
        x: 0,
        y: 0,
        z: 0,
      });
      syncSimulation();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- syncSimulation is stable
  }, [config]);

  const syncSimulation = useCallback(() => {
    const sim = simulationRef.current;
    if (!sim) return;

    const nodes = Array.from(nodesRef.current.values());
    const links = Array.from(linksRef.current.values());

    sim.nodes(nodes);
    const linkForce = sim.force('link') as ForceLink3D<GraphNode, GraphLink> | undefined;
    linkForce?.links(links);

    sim.alpha(0.15).restart();

    setStats((prev) =>
      prev.nodes === nodes.length && prev.links === links.length
        ? prev
        : { ...prev, nodes: nodes.length, links: links.length }
    );
  }, []);

  // Reset on option changes
  useEffect(() => {
    processedRef.current.clear();
    const selfNode = nodesRef.current.get('self');
    nodesRef.current.clear();
    if (selfNode) nodesRef.current.set('self', selfNode);
    linksRef.current.clear();
    particlesRef.current = [];
    pendingRef.current.clear();
    timersRef.current.forEach((t) => clearTimeout(t));
    timersRef.current.clear();
    trafficPatternsRef.current.clear();
    setStats({ processed: 0, animated: 0, nodes: selfNode ? 1 : 0, links: 0 });
    syncSimulation();
  }, [
    showAmbiguousPaths,
    showAmbiguousNodes,
    useAdvertPathHints,
    splitAmbiguousByTraffic,
    syncSimulation,
  ]);

  const addNode = useCallback(
    (
      id: string,
      name: string | null,
      type: NodeType,
      isAmbiguous: boolean,
      probableIdentity?: string | null,
      ambiguousNames?: string[],
      lastSeen?: number | null
    ) => {
      const existing = nodesRef.current.get(id);
      if (existing) {
        existing.lastActivity = Date.now();
        if (name) existing.name = name;
        if (probableIdentity !== undefined) existing.probableIdentity = probableIdentity;
        if (ambiguousNames) existing.ambiguousNames = ambiguousNames;
        if (lastSeen !== undefined) existing.lastSeen = lastSeen;
      } else {
        // Initialize in 3D sphere around origin
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        const r = 80 + Math.random() * 100;
        nodesRef.current.set(id, {
          id,
          name,
          type,
          isAmbiguous,
          lastActivity: Date.now(),
          probableIdentity,
          lastSeen,
          ambiguousNames,
          x: r * Math.sin(phi) * Math.cos(theta),
          y: r * Math.sin(phi) * Math.sin(theta),
          z: r * Math.cos(phi),
        });
      }
    },
    []
  );

  const addLink = useCallback((sourceId: string, targetId: string) => {
    const key = [sourceId, targetId].sort().join('->');
    const existing = linksRef.current.get(key);
    if (existing) {
      existing.lastActivity = Date.now();
    } else {
      linksRef.current.set(key, { source: sourceId, target: targetId, lastActivity: Date.now() });
    }
  }, []);

  const publishPacket = useCallback((packetKey: string) => {
    const pending = pendingRef.current.get(packetKey);
    if (!pending) return;

    pendingRef.current.delete(packetKey);
    timersRef.current.delete(packetKey);

    // Skip particle creation when tab is hidden — nobody is watching, and
    // creating them now would cause a burst of animations when the tab
    // becomes visible again (since rAF is paused while hidden).
    if (document.hidden) return;

    for (const path of pending.paths) {
      const dedupedPath = dedupeConsecutive(path.nodes);
      if (dedupedPath.length < 2) continue;

      for (let i = 0; i < dedupedPath.length - 1; i++) {
        particlesRef.current.push({
          linkKey: [dedupedPath[i], dedupedPath[i + 1]].sort().join('->'),
          progress: -i,
          speed: PARTICLE_SPEED * speedMultiplierRef.current,
          color: PARTICLE_COLOR_MAP[pending.label],
          label: pending.label,
          fromNodeId: dedupedPath[i],
          toNodeId: dedupedPath[i + 1],
        });
      }
    }
  }, []);

  const pickLikelyRepeaterByAdvertPath = useCallback(
    (candidates: Contact[], nextPrefix: string | null) => {
      const nextHop = nextPrefix?.toLowerCase() ?? null;
      const scored = candidates
        .map((candidate) => {
          const prefix12 = candidate.public_key.slice(0, 12).toLowerCase();
          const paths = advertPathIndex.byRepeater.get(prefix12) ?? [];
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

      // Require stronger-than-trivial evidence and a clear winner.
      if (top.matchScore < 2) return null;
      if (second && top.matchScore < second.matchScore * 2) return null;

      return top.candidate;
    },
    [advertPathIndex]
  );

  const resolveNode = useCallback(
    (
      source: { type: 'prefix' | 'pubkey' | 'name'; value: string },
      isRepeater: boolean,
      showAmbiguous: boolean,
      myPrefix: string | null,
      trafficContext?: { packetSource: string | null; nextPrefix: string | null }
    ): string | null => {
      if (source.type === 'pubkey') {
        if (source.value.length < 12) return null;
        const nodeId = source.value.slice(0, 12).toLowerCase();
        if (myPrefix && nodeId === myPrefix) return 'self';
        const contact = contactIndex.byPrefix12.get(nodeId);
        addNode(
          nodeId,
          contact?.name || null,
          getNodeType(contact),
          false,
          undefined,
          undefined,
          contact?.last_seen
        );
        return nodeId;
      }

      if (source.type === 'name') {
        const contact = contactIndex.byName.get(source.value) ?? null;
        if (contact) {
          const nodeId = contact.public_key.slice(0, 12).toLowerCase();
          if (myPrefix && nodeId === myPrefix) return 'self';
          addNode(
            nodeId,
            contact.name,
            getNodeType(contact),
            false,
            undefined,
            undefined,
            contact.last_seen
          );
          return nodeId;
        }
        const nodeId = `name:${source.value}`;
        addNode(nodeId, source.value, 'client', false, undefined);
        return nodeId;
      }

      // type === 'prefix'
      const matches = contactIndex.byPrefix.get(source.value.toLowerCase()) ?? [];
      const contact = matches.length === 1 ? matches[0] : null;
      if (contact) {
        const nodeId = contact.public_key.slice(0, 12).toLowerCase();
        if (myPrefix && nodeId === myPrefix) return 'self';
        addNode(
          nodeId,
          contact.name,
          getNodeType(contact),
          false,
          undefined,
          undefined,
          contact.last_seen
        );
        return nodeId;
      }

      if (showAmbiguous) {
        const filtered = isRepeater
          ? matches.filter((c) => c.type === CONTACT_TYPE_REPEATER)
          : matches.filter((c) => c.type !== CONTACT_TYPE_REPEATER);

        if (filtered.length === 1) {
          const c = filtered[0];
          const nodeId = c.public_key.slice(0, 12).toLowerCase();
          addNode(nodeId, c.name, getNodeType(c), false, undefined, undefined, c.last_seen);
          return nodeId;
        }

        if (filtered.length > 1 || (filtered.length === 0 && isRepeater)) {
          const names = filtered.map((c) => c.name || c.public_key.slice(0, 8));
          const lastSeen = filtered.reduce(
            (max, c) => (c.last_seen && (!max || c.last_seen > max) ? c.last_seen : max),
            null as number | null
          );

          let nodeId = `?${source.value.toLowerCase()}`;
          let displayName = source.value.toUpperCase();
          let probableIdentity: string | null = null;
          let ambiguousNames = names.length > 0 ? names : undefined;

          if (useAdvertPathHints && isRepeater && trafficContext) {
            const likely = pickLikelyRepeaterByAdvertPath(filtered, trafficContext.nextPrefix);
            if (likely) {
              const likelyName = likely.name || likely.public_key.slice(0, 12).toUpperCase();
              probableIdentity = likelyName;
              displayName = likelyName;
              ambiguousNames = filtered
                .filter((c) => c.public_key !== likely.public_key)
                .map((c) => c.name || c.public_key.slice(0, 8));
            }
          }

          if (splitAmbiguousByTraffic && isRepeater && trafficContext) {
            const prefix = source.value.toLowerCase();

            if (trafficContext.packetSource) {
              recordTrafficObservation(
                trafficPatternsRef.current,
                prefix,
                trafficContext.packetSource,
                trafficContext.nextPrefix
              );
            }

            const trafficData = trafficPatternsRef.current.get(prefix);
            if (trafficData) {
              const analysis = analyzeRepeaterTraffic(trafficData);
              if (analysis.shouldSplit && trafficContext.nextPrefix) {
                const nextShort = trafficContext.nextPrefix.slice(0, 2).toLowerCase();
                nodeId = `?${prefix}:>${nextShort}`;
                if (!probableIdentity) {
                  displayName = `${source.value.toUpperCase()}:>${nextShort}`;
                }
              }
            }
          }

          addNode(
            nodeId,
            displayName,
            isRepeater ? 'repeater' : 'client',
            true,
            probableIdentity,
            ambiguousNames,
            lastSeen
          );
          return nodeId;
        }
      }

      return null;
    },
    [
      contactIndex,
      addNode,
      useAdvertPathHints,
      pickLikelyRepeaterByAdvertPath,
      splitAmbiguousByTraffic,
    ]
  );

  const buildPath = useCallback(
    (
      parsed: ReturnType<typeof parsePacket>,
      packet: RawPacket,
      myPrefix: string | null
    ): string[] => {
      if (!parsed) return [];
      const path: string[] = [];
      let packetSource: string | null = null;

      if (parsed.payloadType === PayloadType.Advert && parsed.advertPubkey) {
        const nodeId = resolveNode(
          { type: 'pubkey', value: parsed.advertPubkey },
          false,
          false,
          myPrefix
        );
        if (nodeId) {
          path.push(nodeId);
          packetSource = nodeId;
        }
      } else if (parsed.payloadType === PayloadType.AnonRequest && parsed.anonRequestPubkey) {
        const nodeId = resolveNode(
          { type: 'pubkey', value: parsed.anonRequestPubkey },
          false,
          false,
          myPrefix
        );
        if (nodeId) {
          path.push(nodeId);
          packetSource = nodeId;
        }
      } else if (parsed.payloadType === PayloadType.TextMessage && parsed.srcHash) {
        if (myPrefix && parsed.srcHash.toLowerCase() === myPrefix) {
          path.push('self');
          packetSource = 'self';
        } else {
          const nodeId = resolveNode(
            { type: 'prefix', value: parsed.srcHash },
            false,
            showAmbiguousNodes,
            myPrefix
          );
          if (nodeId) {
            path.push(nodeId);
            packetSource = nodeId;
          }
        }
      } else if (parsed.payloadType === PayloadType.GroupText) {
        const senderName = parsed.groupTextSender || packet.decrypted_info?.sender;
        if (senderName) {
          const nodeId = resolveNode({ type: 'name', value: senderName }, false, false, myPrefix);
          if (nodeId) {
            path.push(nodeId);
            packetSource = nodeId;
          }
        }
      }

      for (let i = 0; i < parsed.pathBytes.length; i++) {
        const hexPrefix = parsed.pathBytes[i];
        const nextPrefix = parsed.pathBytes[i + 1] || null;
        const nodeId = resolveNode(
          { type: 'prefix', value: hexPrefix },
          true,
          showAmbiguousPaths,
          myPrefix,
          { packetSource, nextPrefix }
        );
        if (nodeId) path.push(nodeId);
      }

      if (parsed.payloadType === PayloadType.TextMessage && parsed.dstHash) {
        if (myPrefix && parsed.dstHash.toLowerCase() === myPrefix) {
          path.push('self');
        } else {
          const nodeId = resolveNode(
            { type: 'prefix', value: parsed.dstHash },
            false,
            showAmbiguousNodes,
            myPrefix
          );
          if (nodeId) path.push(nodeId);
          else path.push('self');
        }
      } else if (path.length > 0) {
        path.push('self');
      }

      if (path.length > 0 && path[path.length - 1] !== 'self') {
        path.push('self');
      }

      return dedupeConsecutive(path);
    },
    [resolveNode, showAmbiguousPaths, showAmbiguousNodes]
  );

  // Process packets
  useEffect(() => {
    let newProcessed = 0;
    let newAnimated = 0;
    let needsUpdate = false;
    const myPrefix = config?.public_key?.slice(0, 12).toLowerCase() || null;

    for (const packet of packets) {
      const observationKey = getRawPacketObservationKey(packet);
      if (processedRef.current.has(observationKey)) continue;
      processedRef.current.add(observationKey);
      newProcessed++;

      if (processedRef.current.size > 1000) {
        processedRef.current = new Set(Array.from(processedRef.current).slice(-500));
      }

      const parsed = parsePacket(packet.data);
      if (!parsed) continue;

      const path = buildPath(parsed, packet, myPrefix);
      if (path.length < 2) continue;

      // Tag each node with why it's considered active
      const label = getPacketLabel(parsed.payloadType);
      for (let i = 0; i < path.length; i++) {
        const n = nodesRef.current.get(path[i]);
        if (n && n.id !== 'self') {
          n.lastActivityReason = i === 0 ? `${label} source` : `Relayed ${label}`;
        }
      }

      for (let i = 0; i < path.length - 1; i++) {
        if (path[i] !== path[i + 1]) {
          addLink(path[i], path[i + 1]);
          needsUpdate = true;
        }
      }

      const packetKey = generatePacketKey(parsed, packet);
      const now = Date.now();
      const existing = pendingRef.current.get(packetKey);

      if (existing && now < existing.expiresAt) {
        existing.paths.push({ nodes: path, snr: packet.snr ?? null, timestamp: now });
      } else {
        if (timersRef.current.has(packetKey)) {
          clearTimeout(timersRef.current.get(packetKey));
        }
        const windowMs = observationWindowRef.current;
        pendingRef.current.set(packetKey, {
          key: packetKey,
          label: getPacketLabel(parsed.payloadType),
          paths: [{ nodes: path, snr: packet.snr ?? null, timestamp: now }],
          firstSeen: now,
          expiresAt: now + windowMs,
        });
        timersRef.current.set(
          packetKey,
          setTimeout(() => publishPacket(packetKey), windowMs)
        );
      }

      if (pendingRef.current.size > 100) {
        const entries = Array.from(pendingRef.current.entries())
          .sort((a, b) => a[1].firstSeen - b[1].firstSeen)
          .slice(0, 50);
        for (const [key] of entries) {
          clearTimeout(timersRef.current.get(key));
          timersRef.current.delete(key);
          pendingRef.current.delete(key);
        }
      }

      newAnimated++;
    }

    if (needsUpdate) syncSimulation();
    if (newProcessed > 0) {
      setStats((prev) => ({
        ...prev,
        processed: prev.processed + newProcessed,
        animated: prev.animated + newAnimated,
      }));
    }
  }, [packets, config, buildPath, addLink, syncSimulation, publishPacket]);

  const expandContract = useCallback(() => {
    const sim = simulationRef.current;
    if (!sim) return;

    if (stretchRafRef.current !== null) {
      cancelAnimationFrame(stretchRafRef.current);
      stretchRafRef.current = null;
    }

    const startChargeStrength = chargeStrength;
    const peakChargeStrength = -5000;
    const startLinkStrength = 0.3;
    const minLinkStrength = 0.02;
    const expandDuration = 1000;
    const holdDuration = 2000;
    const contractDuration = 1000;
    const startTime = performance.now();

    const animate = (now: number) => {
      const elapsed = now - startTime;
      let currentChargeStrength: number;
      let currentLinkStrength: number;

      if (elapsed < expandDuration) {
        const t = elapsed / expandDuration;
        currentChargeStrength =
          startChargeStrength + (peakChargeStrength - startChargeStrength) * t;
        currentLinkStrength = startLinkStrength + (minLinkStrength - startLinkStrength) * t;
      } else if (elapsed < expandDuration + holdDuration) {
        currentChargeStrength = peakChargeStrength;
        currentLinkStrength = minLinkStrength;
      } else if (elapsed < expandDuration + holdDuration + contractDuration) {
        const t = (elapsed - expandDuration - holdDuration) / contractDuration;
        currentChargeStrength = peakChargeStrength + (startChargeStrength - peakChargeStrength) * t;
        currentLinkStrength = minLinkStrength + (startLinkStrength - minLinkStrength) * t;
      } else {
        sim.force(
          'charge',
          forceManyBody<GraphNode>()
            .strength((d) => (d.id === 'self' ? startChargeStrength * 6 : startChargeStrength))
            .distanceMax(800)
        );
        sim.force(
          'link',
          forceLink<GraphNode, GraphLink>(Array.from(linksRef.current.values()))
            .id((d) => d.id)
            .distance(120)
            .strength(startLinkStrength)
        );
        sim.alpha(0.3).restart();
        stretchRafRef.current = null;
        return;
      }

      sim.force(
        'charge',
        forceManyBody<GraphNode>()
          .strength((d) => (d.id === 'self' ? currentChargeStrength * 6 : currentChargeStrength))
          .distanceMax(800)
      );
      sim.force(
        'link',
        forceLink<GraphNode, GraphLink>(Array.from(linksRef.current.values()))
          .id((d) => d.id)
          .distance(120)
          .strength(currentLinkStrength)
      );
      sim.alpha(0.5).restart();

      stretchRafRef.current = requestAnimationFrame(animate);
    };

    stretchRafRef.current = requestAnimationFrame(animate);
  }, [chargeStrength]);

  const clearAndReset = useCallback(() => {
    if (stretchRafRef.current !== null) {
      cancelAnimationFrame(stretchRafRef.current);
      stretchRafRef.current = null;
    }

    for (const timer of timersRef.current.values()) {
      clearTimeout(timer);
    }
    timersRef.current.clear();
    pendingRef.current.clear();
    processedRef.current.clear();
    trafficPatternsRef.current.clear();
    particlesRef.current.length = 0;
    linksRef.current.clear();

    const selfNode = nodesRef.current.get('self');
    nodesRef.current.clear();
    if (selfNode) {
      selfNode.x = 0;
      selfNode.y = 0;
      selfNode.z = 0;
      selfNode.vx = 0;
      selfNode.vy = 0;
      selfNode.vz = 0;
      selfNode.lastActivity = Date.now();
      nodesRef.current.set('self', selfNode);
    }

    const sim = simulationRef.current;
    if (sim) {
      sim.nodes(Array.from(nodesRef.current.values()));
      const linkForce = sim.force('link') as ForceLink3D<GraphNode, GraphLink> | undefined;
      linkForce?.links([]);
      sim.alpha(0.3).restart();
    }

    setStats({ processed: 0, animated: 0, nodes: 1, links: 0 });
  }, []);

  useEffect(() => {
    const stretchRaf = stretchRafRef;
    const timers = timersRef.current;
    const pending = pendingRef.current;
    return () => {
      if (stretchRaf.current !== null) {
        cancelAnimationFrame(stretchRaf.current);
      }
      for (const timer of timers.values()) {
        clearTimeout(timer);
      }
      timers.clear();
      pending.clear();
    };
  }, []);

  // Prune nodes with no activity in the last 5 minutes
  useEffect(() => {
    if (!pruneStaleNodes) return;

    const STALE_MS = 5 * 60 * 1000;
    const PRUNE_INTERVAL_MS = 10_000;

    const interval = setInterval(() => {
      const cutoff = Date.now() - STALE_MS;
      let pruned = false;

      for (const [id, node] of nodesRef.current) {
        if (id === 'self') continue;
        if (node.lastActivity < cutoff) {
          nodesRef.current.delete(id);
          pruned = true;
        }
      }

      if (pruned) {
        // Remove links that reference pruned nodes
        for (const [key, link] of linksRef.current) {
          const { sourceId, targetId } = getLinkId(link);
          if (!nodesRef.current.has(sourceId) || !nodesRef.current.has(targetId)) {
            linksRef.current.delete(key);
          }
        }
        syncSimulation();
      }
    }, PRUNE_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [pruneStaleNodes, syncSimulation]);

  return useMemo(
    () => ({
      nodes: nodesRef.current,
      links: linksRef.current,
      particles: particlesRef.current,
      stats,
      expandContract,
      clearAndReset,
    }),
    [stats, expandContract, clearAndReset]
  );
}

// =============================================================================
// THREE.JS SCENE MANAGEMENT
// =============================================================================

interface NodeMeshData {
  mesh: THREE.Mesh;
  label: CSS2DObject;
  labelDiv: HTMLDivElement;
}

// =============================================================================
// MAIN COMPONENT
// =============================================================================

interface PacketVisualizer3DProps {
  packets: RawPacket[];
  contacts: Contact[];
  config: RadioConfig | null;
  fullScreen?: boolean;
  onFullScreenChange?: (fullScreen: boolean) => void;
}

export function PacketVisualizer3D({
  packets,
  contacts,
  config,
  fullScreen,
  onFullScreenChange,
}: PacketVisualizer3DProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const cssRendererRef = useRef<CSS2DRenderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const nodeMeshesRef = useRef<Map<string, NodeMeshData>>(new Map());
  const raycastTargetsRef = useRef<THREE.Mesh[]>([]);
  const linkLineRef = useRef<THREE.LineSegments | null>(null);
  const highlightLineRef = useRef<THREE.LineSegments | null>(null);
  const particlePointsRef = useRef<THREE.Points | null>(null);
  const particleTextureRef = useRef<THREE.Texture | null>(null);
  const linkPositionBufferRef = useRef<Float32Array<ArrayBufferLike>>(new Float32Array(0));
  const highlightPositionBufferRef = useRef<Float32Array<ArrayBufferLike>>(new Float32Array(0));
  const particlePositionBufferRef = useRef<Float32Array<ArrayBufferLike>>(new Float32Array(0));
  const particleColorBufferRef = useRef<Float32Array<ArrayBufferLike>>(new Float32Array(0));
  const raycasterRef = useRef(new THREE.Raycaster());
  const mouseRef = useRef(new THREE.Vector2());

  // Options
  const [savedSettings] = useState(getVisualizerSettings);
  const [showAmbiguousPaths, setShowAmbiguousPaths] = useState(savedSettings.showAmbiguousPaths);
  const [showAmbiguousNodes, setShowAmbiguousNodes] = useState(savedSettings.showAmbiguousNodes);
  const [useAdvertPathHints, setUseAdvertPathHints] = useState(savedSettings.useAdvertPathHints);
  const [splitAmbiguousByTraffic, setSplitAmbiguousByTraffic] = useState(
    savedSettings.splitAmbiguousByTraffic
  );
  const [chargeStrength, setChargeStrength] = useState(savedSettings.chargeStrength);
  const [observationWindowSec, setObservationWindowSec] = useState(
    savedSettings.observationWindowSec
  );
  const [letEmDrift, setLetEmDrift] = useState(savedSettings.letEmDrift);
  const [particleSpeedMultiplier, setParticleSpeedMultiplier] = useState(
    savedSettings.particleSpeedMultiplier
  );
  const [showControls, setShowControls] = useState(savedSettings.showControls);
  const [autoOrbit, setAutoOrbit] = useState(savedSettings.autoOrbit);
  const [pruneStaleNodes, setPruneStaleNodes] = useState(savedSettings.pruneStaleNodes);
  const [repeaterAdvertPaths, setRepeaterAdvertPaths] = useState<ContactAdvertPathSummary[]>([]);

  // Persist visualizer controls to localStorage on change
  useEffect(() => {
    saveVisualizerSettings({
      ...getVisualizerSettings(),
      showAmbiguousPaths,
      showAmbiguousNodes,
      useAdvertPathHints,
      splitAmbiguousByTraffic,
      chargeStrength,
      observationWindowSec,
      letEmDrift,
      particleSpeedMultiplier,
      pruneStaleNodes,
      autoOrbit,
      showControls,
    });
  }, [
    showAmbiguousPaths,
    showAmbiguousNodes,
    useAdvertPathHints,
    splitAmbiguousByTraffic,
    chargeStrength,
    observationWindowSec,
    letEmDrift,
    particleSpeedMultiplier,
    pruneStaleNodes,
    autoOrbit,
    showControls,
  ]);

  useEffect(() => {
    let cancelled = false;

    async function loadRepeaterAdvertPaths() {
      try {
        const data = await api.getRepeaterAdvertPaths(10);
        if (!cancelled) {
          setRepeaterAdvertPaths(data);
        }
      } catch (error) {
        if (!cancelled) {
          // Best-effort hinting; keep visualizer fully functional without this data.
          console.debug('Failed to load repeater advert path hints', error);
          setRepeaterAdvertPaths([]);
        }
      }
    }

    loadRepeaterAdvertPaths();
    return () => {
      cancelled = true;
    };
  }, [contacts.length]);

  // Hover & click-to-pin
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const hoveredNodeIdRef = useRef<string | null>(null);
  const [hoveredNeighborIds, setHoveredNeighborIds] = useState<string[]>([]);
  const hoveredNeighborIdsRef = useRef<string[]>([]);
  const pinnedNodeIdRef = useRef<string | null>(null);
  const [pinnedNodeId, setPinnedNodeId] = useState<string | null>(null);

  // Data layer
  const data = useVisualizerData3D({
    packets,
    contacts,
    config,
    repeaterAdvertPaths,
    showAmbiguousPaths,
    showAmbiguousNodes,
    useAdvertPathHints,
    splitAmbiguousByTraffic,
    chargeStrength,
    letEmDrift,
    particleSpeedMultiplier,
    observationWindowSec,
    pruneStaleNodes,
  });
  const dataRef = useRef(data);
  useEffect(() => {
    dataRef.current = data;
  }, [data]);

  // Initialize Three.js scene
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // Scene
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(COLORS.background);
    sceneRef.current = scene;

    // Camera
    const camera = new THREE.PerspectiveCamera(60, 1, 1, 5000);
    camera.position.set(0, 0, 400);
    cameraRef.current = camera;

    // WebGL renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    container.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    // Circular particle sprite texture (so particles render as circles, not squares)
    const texSize = 64;
    const texCanvas = document.createElement('canvas');
    texCanvas.width = texSize;
    texCanvas.height = texSize;
    const texCtx = texCanvas.getContext('2d')!;
    const gradient = texCtx.createRadialGradient(
      texSize / 2,
      texSize / 2,
      0,
      texSize / 2,
      texSize / 2,
      texSize / 2
    );
    gradient.addColorStop(0, 'rgba(255,255,255,1)');
    gradient.addColorStop(0.5, 'rgba(255,255,255,0.8)');
    gradient.addColorStop(1, 'rgba(255,255,255,0)');
    texCtx.fillStyle = gradient;
    texCtx.fillRect(0, 0, texSize, texSize);
    const particleTexture = new THREE.CanvasTexture(texCanvas);
    particleTextureRef.current = particleTexture;

    // CSS2D renderer for text labels
    const cssRenderer = new CSS2DRenderer();
    cssRenderer.domElement.style.position = 'absolute';
    cssRenderer.domElement.style.top = '0';
    cssRenderer.domElement.style.left = '0';
    cssRenderer.domElement.style.pointerEvents = 'none';
    cssRenderer.domElement.style.zIndex = '1';
    container.appendChild(cssRenderer.domElement);
    cssRendererRef.current = cssRenderer;

    // OrbitControls
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.1;
    controls.minDistance = 50;
    controls.maxDistance = 2000;
    controlsRef.current = controls;

    // Persistent line meshes (their buffers are updated in-place each frame)
    const linkGeometry = new THREE.BufferGeometry();
    const linkMaterial = new THREE.LineBasicMaterial({
      color: COLORS.link,
      transparent: true,
      opacity: 0.6,
    });
    const linkSegments = new THREE.LineSegments(linkGeometry, linkMaterial);
    linkSegments.visible = false;
    scene.add(linkSegments);
    linkLineRef.current = linkSegments;

    const highlightGeometry = new THREE.BufferGeometry();
    const highlightMaterial = new THREE.LineBasicMaterial({
      color: 0xffd700,
      transparent: true,
      opacity: 1.0,
      linewidth: 2,
    });
    const highlightSegments = new THREE.LineSegments(highlightGeometry, highlightMaterial);
    highlightSegments.visible = false;
    scene.add(highlightSegments);
    highlightLineRef.current = highlightSegments;

    const particleGeometry = new THREE.BufferGeometry();
    const particleMaterial = new THREE.PointsMaterial({
      size: 20,
      map: particleTexture,
      vertexColors: true,
      sizeAttenuation: true,
      transparent: true,
      opacity: 0.9,
      depthWrite: false,
    });
    const particlePoints = new THREE.Points(particleGeometry, particleMaterial);
    particlePoints.visible = false;
    scene.add(particlePoints);
    particlePointsRef.current = particlePoints;

    // Initial sizing
    const rect = container.getBoundingClientRect();
    renderer.setSize(rect.width, rect.height);
    cssRenderer.setSize(rect.width, rect.height);
    camera.aspect = rect.width / rect.height;
    camera.updateProjectionMatrix();

    // Resize observer
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width === 0 || height === 0) continue;
        renderer.setSize(width, height);
        cssRenderer.setSize(width, height);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
      }
    });
    observer.observe(container);

    const nodeMeshes = nodeMeshesRef.current;
    return () => {
      observer.disconnect();
      controls.dispose();
      renderer.dispose();
      // Remove renderer DOM elements
      if (renderer.domElement.parentNode) {
        renderer.domElement.parentNode.removeChild(renderer.domElement);
      }
      if (cssRenderer.domElement.parentNode) {
        cssRenderer.domElement.parentNode.removeChild(cssRenderer.domElement);
      }
      // Clean up node meshes and their CSS2D label DOM elements
      for (const nd of nodeMeshes.values()) {
        nd.mesh.remove(nd.label);
        nd.labelDiv.remove();
        scene.remove(nd.mesh);
        nd.mesh.geometry.dispose();
        (nd.mesh.material as THREE.Material).dispose();
      }
      nodeMeshes.clear();
      raycastTargetsRef.current = [];

      if (linkLineRef.current) {
        scene.remove(linkLineRef.current);
        linkLineRef.current.geometry.dispose();
        (linkLineRef.current.material as THREE.Material).dispose();
        linkLineRef.current = null;
      }
      if (highlightLineRef.current) {
        scene.remove(highlightLineRef.current);
        highlightLineRef.current.geometry.dispose();
        (highlightLineRef.current.material as THREE.Material).dispose();
        highlightLineRef.current = null;
      }
      if (particlePointsRef.current) {
        scene.remove(particlePointsRef.current);
        particlePointsRef.current.geometry.dispose();
        (particlePointsRef.current.material as THREE.Material).dispose();
        particlePointsRef.current = null;
      }
      particleTexture.dispose();
      particleTextureRef.current = null;
      linkPositionBufferRef.current = new Float32Array(0);
      highlightPositionBufferRef.current = new Float32Array(0);
      particlePositionBufferRef.current = new Float32Array(0);
      particleColorBufferRef.current = new Float32Array(0);
      sceneRef.current = null;
      cameraRef.current = null;
      rendererRef.current = null;
      cssRendererRef.current = null;
      controlsRef.current = null;
    };
  }, []);

  // Sync auto-orbit with OrbitControls
  useEffect(() => {
    const controls = controlsRef.current;
    if (!controls) return;
    controls.autoRotate = autoOrbit;
    controls.autoRotateSpeed = -0.5; // negative = clockwise from above
  }, [autoOrbit]);

  // Mouse handlers for raycasting and click-to-pin
  useEffect(() => {
    const renderer = rendererRef.current;
    const camera = cameraRef.current;
    if (!renderer || !camera) return;

    const onMouseMove = (event: MouseEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      mouseRef.current.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      mouseRef.current.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    };

    let mouseDownPos = { x: 0, y: 0 };

    const onMouseDown = (event: MouseEvent) => {
      mouseDownPos = { x: event.clientX, y: event.clientY };
    };

    const onMouseUp = (event: MouseEvent) => {
      // Only count as click if mouse didn't move much (not a drag/orbit)
      const dx = event.clientX - mouseDownPos.x;
      const dy = event.clientY - mouseDownPos.y;
      if (dx * dx + dy * dy > 25) return;

      const rect = renderer.domElement.getBoundingClientRect();
      const clickMouse = new THREE.Vector2(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1
      );

      const raycaster = raycasterRef.current;
      raycaster.setFromCamera(clickMouse, camera);
      const intersects = raycaster.intersectObjects(raycastTargetsRef.current, false);
      const clickedObject = intersects[0]?.object as THREE.Mesh | undefined;
      const clickedId = (clickedObject?.userData?.nodeId as string | undefined) ?? null;

      if (clickedId === pinnedNodeIdRef.current) {
        // Unpin
        pinnedNodeIdRef.current = null;
        setPinnedNodeId(null);
      } else if (clickedId) {
        // Pin this node
        pinnedNodeIdRef.current = clickedId;
        setPinnedNodeId(clickedId);
      } else {
        // Clicked empty space — unpin
        pinnedNodeIdRef.current = null;
        setPinnedNodeId(null);
      }
    };

    renderer.domElement.addEventListener('mousemove', onMouseMove);
    renderer.domElement.addEventListener('mousedown', onMouseDown);
    renderer.domElement.addEventListener('mouseup', onMouseUp);
    return () => {
      renderer.domElement.removeEventListener('mousemove', onMouseMove);
      renderer.domElement.removeEventListener('mousedown', onMouseDown);
      renderer.domElement.removeEventListener('mouseup', onMouseUp);
    };
  }, []);

  // Animation loop
  useEffect(() => {
    const scene = sceneRef.current;
    const camera = cameraRef.current;
    const renderer = rendererRef.current;
    const cssRenderer = cssRendererRef.current;
    const controls = controlsRef.current;
    if (!scene || !camera || !renderer || !cssRenderer || !controls) return;

    let running = true;

    const animate = () => {
      if (!running) return;
      requestAnimationFrame(animate);

      controls.update();

      const { nodes, links, particles } = dataRef.current;

      // --- Sync node meshes ---
      const currentNodeIds = new Set<string>();

      for (const node of nodes.values()) {
        currentNodeIds.add(node.id);

        let nd = nodeMeshesRef.current.get(node.id);
        if (!nd) {
          const isSelf = node.type === 'self';
          const radius = isSelf ? 12 : 6;
          const geometry = new THREE.SphereGeometry(radius, 16, 12);
          const material = new THREE.MeshBasicMaterial({ color: getBaseNodeColor(node) });
          const mesh = new THREE.Mesh(geometry, material);
          mesh.userData.nodeId = node.id;
          scene.add(mesh);

          const labelDiv = document.createElement('div');
          labelDiv.style.color = node.isAmbiguous ? COLORS.ambiguous : '#e5e7eb';
          labelDiv.style.fontSize = '11px';
          labelDiv.style.fontFamily = 'sans-serif';
          labelDiv.style.textAlign = 'center';
          labelDiv.style.whiteSpace = 'nowrap';
          labelDiv.style.textShadow = '0 0 4px #000, 0 0 2px #000';
          const label = new CSS2DObject(labelDiv);
          label.position.set(0, -(radius + 6), 0);
          mesh.add(label);

          nd = { mesh, label, labelDiv };
          nodeMeshesRef.current.set(node.id, nd);
          raycastTargetsRef.current.push(mesh);
        }

        nd.mesh.position.set(node.x ?? 0, node.y ?? 0, node.z ?? 0);
        const labelColor = node.isAmbiguous ? COLORS.ambiguous : '#e5e7eb';
        if (nd.labelDiv.style.color !== labelColor) {
          nd.labelDiv.style.color = labelColor;
        }
        const labelText = node.name || (node.type === 'self' ? 'Me' : node.id.slice(0, 8));
        if (nd.labelDiv.textContent !== labelText) {
          nd.labelDiv.textContent = labelText;
        }
      }

      for (const [id, nd] of nodeMeshesRef.current) {
        if (!currentNodeIds.has(id)) {
          nd.mesh.remove(nd.label);
          nd.labelDiv.remove();
          scene.remove(nd.mesh);
          nd.mesh.geometry.dispose();
          (nd.mesh.material as THREE.Material).dispose();
          const meshIdx = raycastTargetsRef.current.indexOf(nd.mesh);
          if (meshIdx >= 0) raycastTargetsRef.current.splice(meshIdx, 1);
          nodeMeshesRef.current.delete(id);
        }
      }

      // --- Raycasting for hover ---
      raycasterRef.current.setFromCamera(mouseRef.current, camera);
      const intersects = raycasterRef.current.intersectObjects(raycastTargetsRef.current, false);
      const hitObject = intersects[0]?.object as THREE.Mesh | undefined;
      const hitId = (hitObject?.userData?.nodeId as string | undefined) ?? null;
      if (hitId !== hoveredNodeIdRef.current) {
        hoveredNodeIdRef.current = hitId;
        setHoveredNodeId(hitId);
      }
      const activeId = pinnedNodeIdRef.current ?? hoveredNodeIdRef.current;

      // --- Sync links (buffers updated in-place) ---
      const visibleLinks: GraphLink[] = [];
      for (const link of links.values()) {
        const { sourceId, targetId } = getLinkId(link);
        if (currentNodeIds.has(sourceId) && currentNodeIds.has(targetId)) {
          visibleLinks.push(link);
        }
      }

      const connectedIds = activeId ? new Set<string>([activeId]) : null;

      const linkLine = linkLineRef.current;
      if (linkLine) {
        const geometry = linkLine.geometry as THREE.BufferGeometry;
        const requiredLength = visibleLinks.length * 6;
        if (linkPositionBufferRef.current.length < requiredLength) {
          linkPositionBufferRef.current = growFloat32Buffer(
            linkPositionBufferRef.current,
            requiredLength
          );
          geometry.setAttribute(
            'position',
            new THREE.BufferAttribute(linkPositionBufferRef.current, 3).setUsage(
              THREE.DynamicDrawUsage
            )
          );
        }

        const highlightLine = highlightLineRef.current;
        if (highlightLine && highlightPositionBufferRef.current.length < requiredLength) {
          highlightPositionBufferRef.current = growFloat32Buffer(
            highlightPositionBufferRef.current,
            requiredLength
          );
          (highlightLine.geometry as THREE.BufferGeometry).setAttribute(
            'position',
            new THREE.BufferAttribute(highlightPositionBufferRef.current, 3).setUsage(
              THREE.DynamicDrawUsage
            )
          );
        }

        const positions = linkPositionBufferRef.current;
        const hlPositions = highlightPositionBufferRef.current;
        let idx = 0;
        let hlIdx = 0;

        for (const link of visibleLinks) {
          const { sourceId, targetId } = getLinkId(link);
          const sNode = nodes.get(sourceId);
          const tNode = nodes.get(targetId);
          if (!sNode || !tNode) continue;

          const sx = sNode.x ?? 0;
          const sy = sNode.y ?? 0;
          const sz = sNode.z ?? 0;
          const tx = tNode.x ?? 0;
          const ty = tNode.y ?? 0;
          const tz = tNode.z ?? 0;

          positions[idx++] = sx;
          positions[idx++] = sy;
          positions[idx++] = sz;
          positions[idx++] = tx;
          positions[idx++] = ty;
          positions[idx++] = tz;

          if (activeId && (sourceId === activeId || targetId === activeId)) {
            connectedIds?.add(sourceId === activeId ? targetId : sourceId);
            hlPositions[hlIdx++] = sx;
            hlPositions[hlIdx++] = sy;
            hlPositions[hlIdx++] = sz;
            hlPositions[hlIdx++] = tx;
            hlPositions[hlIdx++] = ty;
            hlPositions[hlIdx++] = tz;
          }
        }

        const positionAttr = geometry.getAttribute('position') as THREE.BufferAttribute | undefined;
        if (positionAttr) {
          positionAttr.needsUpdate = true;
        }
        geometry.setDrawRange(0, idx / 3);
        linkLine.visible = idx > 0;

        if (highlightLine) {
          const hlGeometry = highlightLine.geometry as THREE.BufferGeometry;
          const hlAttr = hlGeometry.getAttribute('position') as THREE.BufferAttribute | undefined;
          if (hlAttr) {
            hlAttr.needsUpdate = true;
          }
          hlGeometry.setDrawRange(0, hlIdx / 3);
          highlightLine.visible = hlIdx > 0;
        }
      }

      // --- Sync particles (buffers updated in-place) ---
      let writeIdx = 0;
      for (let readIdx = 0; readIdx < particles.length; readIdx++) {
        const particle = particles[readIdx];
        particle.progress += particle.speed;
        if (particle.progress <= 1) {
          particles[writeIdx++] = particle;
        }
      }
      particles.length = writeIdx;

      const particlePoints = particlePointsRef.current;
      if (particlePoints) {
        const geometry = particlePoints.geometry as THREE.BufferGeometry;
        const requiredLength = particles.length * 3;

        if (particlePositionBufferRef.current.length < requiredLength) {
          particlePositionBufferRef.current = growFloat32Buffer(
            particlePositionBufferRef.current,
            requiredLength
          );
          geometry.setAttribute(
            'position',
            new THREE.BufferAttribute(particlePositionBufferRef.current, 3).setUsage(
              THREE.DynamicDrawUsage
            )
          );
        }
        if (particleColorBufferRef.current.length < requiredLength) {
          particleColorBufferRef.current = growFloat32Buffer(
            particleColorBufferRef.current,
            requiredLength
          );
          geometry.setAttribute(
            'color',
            new THREE.BufferAttribute(particleColorBufferRef.current, 3).setUsage(
              THREE.DynamicDrawUsage
            )
          );
        }

        const pPositions = particlePositionBufferRef.current;
        const pColors = particleColorBufferRef.current;
        const color = new THREE.Color();
        let visibleCount = 0;

        for (const p of particles) {
          if (p.progress < 0) continue;
          if (!currentNodeIds.has(p.fromNodeId) || !currentNodeIds.has(p.toNodeId)) continue;

          const fromNode = nodes.get(p.fromNodeId);
          const toNode = nodes.get(p.toNodeId);
          if (!fromNode || !toNode) continue;

          const t = p.progress;
          const x = (fromNode.x ?? 0) + ((toNode.x ?? 0) - (fromNode.x ?? 0)) * t;
          const y = (fromNode.y ?? 0) + ((toNode.y ?? 0) - (fromNode.y ?? 0)) * t;
          const z = (fromNode.z ?? 0) + ((toNode.z ?? 0) - (fromNode.z ?? 0)) * t;

          pPositions[visibleCount * 3] = x;
          pPositions[visibleCount * 3 + 1] = y;
          pPositions[visibleCount * 3 + 2] = z;

          color.set(p.color);
          pColors[visibleCount * 3] = color.r;
          pColors[visibleCount * 3 + 1] = color.g;
          pColors[visibleCount * 3 + 2] = color.b;
          visibleCount++;
        }

        const posAttr = geometry.getAttribute('position') as THREE.BufferAttribute | undefined;
        const colorAttr = geometry.getAttribute('color') as THREE.BufferAttribute | undefined;
        if (posAttr) posAttr.needsUpdate = true;
        if (colorAttr) colorAttr.needsUpdate = true;
        geometry.setDrawRange(0, visibleCount);
        particlePoints.visible = visibleCount > 0;
      }

      // Sync neighbor info only when changed to avoid re-rendering every frame.
      const nextNeighbors = connectedIds
        ? Array.from(connectedIds)
            .filter((id) => id !== activeId)
            .sort()
        : [];
      if (!arraysEqual(hoveredNeighborIdsRef.current, nextNeighbors)) {
        hoveredNeighborIdsRef.current = nextNeighbors;
        setHoveredNeighborIds(nextNeighbors);
      }

      // Highlight active node and neighbors
      for (const [id, nd] of nodeMeshesRef.current) {
        const node = nodes.get(id);
        if (!node) continue;
        const mat = nd.mesh.material as THREE.MeshBasicMaterial;
        if (id === activeId) {
          mat.color.set(0xffd700);
        } else if (connectedIds?.has(id)) {
          mat.color.set(0xfff0b3);
        } else {
          mat.color.set(getBaseNodeColor(node));
        }
      }

      renderer.render(scene, camera);
      cssRenderer.render(scene, camera);
    };

    animate();
    return () => {
      running = false;
    };
  }, []);

  return (
    <div
      ref={containerRef}
      className="w-full h-full bg-background relative overflow-hidden"
      role="img"
      aria-label="3D mesh network visualizer showing radio nodes as colored spheres and packet transmissions as animated arcs between them"
    >
      {/* Legend */}
      {showControls && (
        <div className="absolute bottom-4 left-4 bg-background/80 backdrop-blur-sm rounded-lg p-3 text-xs border border-border z-10">
          <div className="flex gap-6">
            <div className="flex flex-col gap-1.5">
              <div className="text-muted-foreground font-medium mb-1">Packets</div>
              {PACKET_LEGEND_ITEMS.map((item) => (
                <div key={item.label} className="flex items-center gap-2">
                  <div
                    className="w-5 h-5 rounded-full flex items-center justify-center text-[8px] font-bold text-white"
                    style={{ backgroundColor: item.color }}
                  >
                    {item.label}
                  </div>
                  <span>{item.description}</span>
                </div>
              ))}
            </div>
            <div className="flex flex-col gap-1.5">
              <div className="text-muted-foreground font-medium mb-1">Nodes</div>
              {NODE_LEGEND_ITEMS.map((item) => (
                <div key={item.label} className="flex items-center gap-2">
                  <div
                    className="rounded-full"
                    style={{
                      width: item.size,
                      height: item.size,
                      backgroundColor: item.color,
                    }}
                  />
                  <span>{item.label}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Options */}
      <div className="absolute top-4 left-4 bg-background/80 backdrop-blur-sm rounded-lg p-3 text-xs border border-border z-10">
        <div className="flex flex-col gap-2">
          {showControls && (
            <>
              <div>Nodes: {data.stats.nodes}</div>
              <div>Links: {data.stats.links}</div>
              <div className="border-t border-border pt-2 mt-1 flex flex-col gap-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <Checkbox
                    checked={showAmbiguousPaths}
                    onCheckedChange={(c) => setShowAmbiguousPaths(c === true)}
                  />
                  <span title="Show placeholder nodes for repeaters when the 1-byte prefix matches multiple contacts">
                    Show ambiguous repeaters
                  </span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <Checkbox
                    checked={showAmbiguousNodes}
                    onCheckedChange={(c) => setShowAmbiguousNodes(c === true)}
                  />
                  <span title="Show placeholder nodes for senders/recipients when only a 1-byte prefix is known">
                    Show ambiguous sender/recipient
                  </span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <Checkbox
                    checked={useAdvertPathHints}
                    onCheckedChange={(c) => setUseAdvertPathHints(c === true)}
                    disabled={!showAmbiguousPaths}
                  />
                  <span
                    title="Use stored repeater advert paths to assign likely identity labels for ambiguous repeater nodes"
                    className={!showAmbiguousPaths ? 'text-muted-foreground' : ''}
                  >
                    Use repeater advert-path identity hints
                  </span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <Checkbox
                    checked={splitAmbiguousByTraffic}
                    onCheckedChange={(c) => setSplitAmbiguousByTraffic(c === true)}
                    disabled={!showAmbiguousPaths}
                  />
                  <span
                    title="Split ambiguous repeaters into separate nodes based on traffic patterns (prev→next). Helps identify colliding prefixes representing different physical nodes, but requires enough traffic to disambiguate."
                    className={!showAmbiguousPaths ? 'text-muted-foreground' : ''}
                  >
                    Heuristically group repeaters by traffic pattern
                  </span>
                </label>
                <div className="flex items-center gap-2">
                  <label
                    htmlFor="observation-window-3d"
                    className="text-muted-foreground"
                    title="How long to wait for duplicate packets via different paths before animating"
                  >
                    Ack/echo listen window:
                  </label>
                  <input
                    id="observation-window-3d"
                    type="number"
                    min="1"
                    max="60"
                    value={observationWindowSec}
                    onChange={(e) =>
                      setObservationWindowSec(
                        Math.max(1, Math.min(60, parseInt(e.target.value) || 1))
                      )
                    }
                    className="w-12 px-1 py-0.5 bg-background border border-border rounded text-xs text-center"
                  />
                  <span className="text-muted-foreground">sec</span>
                </div>
                <div className="border-t border-border pt-2 mt-1 flex flex-col gap-2">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <Checkbox
                      checked={pruneStaleNodes}
                      onCheckedChange={(c) => setPruneStaleNodes(c === true)}
                    />
                    <span title="Automatically remove nodes with no traffic in the last 5 minutes to keep the mesh manageable">
                      Only show nodes heard/pathed in last 5min
                    </span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <Checkbox
                      checked={letEmDrift}
                      onCheckedChange={(c) => setLetEmDrift(c === true)}
                    />
                    <span title="When enabled, the graph continuously reorganizes itself into a better layout">
                      Let &apos;em drift
                    </span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <Checkbox
                      checked={autoOrbit}
                      onCheckedChange={(c) => setAutoOrbit(c === true)}
                    />
                    <span title="Automatically orbit the camera around the scene">
                      Orbit the mesh
                    </span>
                  </label>
                  <div className="flex flex-col gap-1 mt-1">
                    <label
                      className="text-muted-foreground"
                      title="How strongly nodes repel each other. Higher values spread nodes out more."
                    >
                      Repulsion: {Math.abs(chargeStrength)}
                    </label>
                    <input
                      type="range"
                      min="50"
                      max="2500"
                      value={Math.abs(chargeStrength)}
                      onChange={(e) => setChargeStrength(-parseInt(e.target.value))}
                      className="w-full h-2 bg-border rounded-lg appearance-none cursor-pointer accent-primary"
                    />
                  </div>
                  <div className="flex flex-col gap-1 mt-1">
                    <label
                      className="text-muted-foreground"
                      title="How fast particles travel along links. Higher values make packets move faster."
                    >
                      Packet speed: {particleSpeedMultiplier}x
                    </label>
                    <input
                      type="range"
                      min="1"
                      max="5"
                      step="0.5"
                      value={particleSpeedMultiplier}
                      onChange={(e) => setParticleSpeedMultiplier(parseFloat(e.target.value))}
                      className="w-full h-2 bg-border rounded-lg appearance-none cursor-pointer accent-primary"
                    />
                  </div>
                </div>
                <button
                  onClick={data.expandContract}
                  className="mt-1 px-3 py-1.5 bg-primary/20 hover:bg-primary/30 text-primary rounded text-xs transition-colors"
                  title="Expand nodes apart then contract back - can help untangle the graph"
                >
                  Oooh Big Stretch!
                </button>
                <button
                  onClick={() => data.clearAndReset()}
                  className="mt-1 px-3 py-1.5 bg-yellow-500/20 hover:bg-yellow-500/30 text-yellow-500 rounded text-xs transition-colors"
                  title="Clear all nodes and links from the visualization - packets are preserved"
                >
                  Clear &amp; Reset
                </button>
              </div>
            </>
          )}
          <div
            className={
              !showControls
                ? 'flex flex-col gap-2'
                : 'border-t border-border pt-2 mt-1 flex flex-col gap-2'
            }
          >
            <label className="flex items-center gap-2 cursor-pointer">
              <Checkbox
                checked={showControls}
                onCheckedChange={(c) => setShowControls(c === true)}
              />
              <span title="Toggle legends and controls visibility">Show controls</span>
            </label>
            {onFullScreenChange && (
              <label className="flex items-center gap-2 cursor-pointer">
                <Checkbox
                  checked={!fullScreen}
                  onCheckedChange={(c) => onFullScreenChange(c !== true)}
                />
                <span title="Show or hide the packet feed sidebar">Show packet feed sidebar</span>
              </label>
            )}
          </div>
        </div>
      </div>

      {/* Hovered/pinned node tooltip */}
      {(pinnedNodeId ?? hoveredNodeId) && (
        <div className="absolute top-4 right-4 bg-background/90 backdrop-blur-sm rounded-lg p-3 text-xs border border-border z-10 max-w-72 max-h-[calc(100%-2rem)] overflow-y-auto">
          {(() => {
            const tooltipNodeId = pinnedNodeId ?? hoveredNodeId;
            const node = tooltipNodeId ? data.nodes.get(tooltipNodeId) : null;
            if (!node) return null;
            const neighbors = hoveredNeighborIds
              .map((nid) => {
                const n = data.nodes.get(nid);
                if (!n) return null;
                const displayName = n.name || (n.type === 'self' ? 'Me' : n.id.slice(0, 8));
                return { id: nid, name: displayName, ambiguousNames: n.ambiguousNames };
              })
              .filter(Boolean);
            return (
              <div className="flex flex-col gap-1">
                <div className="font-medium">
                  {node.name || (node.type === 'self' ? 'Me' : node.id.slice(0, 8))}
                </div>
                <div className="text-muted-foreground">ID: {node.id}</div>
                <div className="text-muted-foreground">
                  Type: {node.type}
                  {node.isAmbiguous ? ' (ambiguous)' : ''}
                </div>
                {node.probableIdentity && (
                  <div className="text-muted-foreground">Probably: {node.probableIdentity}</div>
                )}
                {node.ambiguousNames && node.ambiguousNames.length > 0 && (
                  <div className="text-muted-foreground">
                    {node.probableIdentity ? 'Other possible: ' : 'Possible: '}
                    {node.ambiguousNames.join(', ')}
                  </div>
                )}
                {pinnedNodeId && node.type !== 'self' && (
                  <div className="text-muted-foreground border-t border-border pt-1 mt-1">
                    <div>Last active: {formatRelativeTime(node.lastActivity)}</div>
                    {node.lastActivityReason && <div>Reason: {node.lastActivityReason}</div>}
                  </div>
                )}
                {neighbors.length > 0 && (
                  <div className="text-muted-foreground border-t border-border pt-1 mt-1">
                    <div className="mb-0.5">Traffic exchanged with:</div>
                    <ul className="pl-3 flex flex-col gap-0.5">
                      {neighbors.map((nb) => (
                        <li key={nb!.id}>
                          {nb!.name}
                          {nb!.ambiguousNames && nb!.ambiguousNames.length > 0 && (
                            <span className="text-muted-foreground/60">
                              {' '}
                              ({nb!.ambiguousNames.join(', ')})
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}
