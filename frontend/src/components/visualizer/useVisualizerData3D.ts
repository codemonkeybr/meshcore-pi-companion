import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  forceCenter,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  forceZ,
  type ForceLink3D,
  type Simulation3D,
} from 'd3-force-3d';

import type { PacketNetworkNode } from '../../networkGraph/packetNetworkGraph';
import {
  buildPacketNetworkContext,
  clearPacketNetworkState,
  createPacketNetworkState,
  ensureSelfNode,
  ingestPacketIntoPacketNetwork,
  projectCanonicalPath,
  projectPacketNetwork,
  prunePacketNetworkState,
  snapshotNeighborIds,
} from '../../networkGraph/packetNetworkGraph';
import {
  type Contact,
  type ContactAdvertPathSummary,
  type RadioConfig,
  type RawPacket,
} from '../../types';
import { getRawPacketObservationKey } from '../../utils/rawPacketIdentity';
import {
  buildLinkKey,
  dedupeConsecutive,
  generatePacketKey,
  type Particle,
  PARTICLE_COLOR_MAP,
  PARTICLE_SPEED,
  type PendingPacket,
} from '../../utils/visualizerUtils';
import { type GraphLink, type GraphNode } from './shared';

export interface UseVisualizerData3DOptions {
  packets: RawPacket[];
  contacts: Contact[];
  config: RadioConfig | null;
  repeaterAdvertPaths: ContactAdvertPathSummary[];
  showAmbiguousPaths: boolean;
  showAmbiguousNodes: boolean;
  useAdvertPathHints: boolean;
  collapseLikelyKnownSiblingRepeaters: boolean;
  splitAmbiguousByTraffic: boolean;
  chargeStrength: number;
  letEmDrift: boolean;
  particleSpeedMultiplier: number;
  observationWindowSec: number;
  pruneStaleNodes: boolean;
  pruneStaleMinutes: number;
}

export interface VisualizerData3D {
  nodes: Map<string, GraphNode>;
  links: Map<string, GraphLink>;
  canonicalNodes: Map<string, PacketNetworkNode>;
  canonicalNeighborIds: Map<string, string[]>;
  renderedNodeIds: Set<string>;
  particles: Particle[];
  stats: { processed: number; animated: number; nodes: number; links: number };
  expandContract: () => void;
  clearAndReset: () => void;
}

function buildInitialRenderNode(node: PacketNetworkNode): GraphNode {
  if (node.id === 'self') {
    return {
      ...node,
      x: 0,
      y: 0,
      z: 0,
      fx: 0,
      fy: 0,
      fz: 0,
      vx: 0,
      vy: 0,
      vz: 0,
    };
  }

  const theta = Math.random() * Math.PI * 2;
  const phi = Math.acos(2 * Math.random() - 1);
  const r = 80 + Math.random() * 100;
  return {
    ...node,
    x: r * Math.sin(phi) * Math.cos(theta),
    y: r * Math.sin(phi) * Math.sin(theta),
    z: r * Math.cos(phi),
  };
}

export function useVisualizerData3D({
  packets,
  contacts,
  config,
  repeaterAdvertPaths,
  showAmbiguousPaths,
  showAmbiguousNodes,
  useAdvertPathHints,
  collapseLikelyKnownSiblingRepeaters,
  splitAmbiguousByTraffic,
  chargeStrength,
  letEmDrift,
  particleSpeedMultiplier,
  observationWindowSec,
  pruneStaleNodes,
  pruneStaleMinutes,
}: UseVisualizerData3DOptions): VisualizerData3D {
  const networkStateRef = useRef(createPacketNetworkState(config?.name || 'Me'));
  const nodesRef = useRef<Map<string, GraphNode>>(new Map());
  const linksRef = useRef<Map<string, GraphLink>>(new Map());
  const particlesRef = useRef<Particle[]>([]);
  const simulationRef = useRef<Simulation3D<GraphNode, GraphLink> | null>(null);
  const processedRef = useRef<Set<string>>(new Set());
  const pendingRef = useRef<Map<string, PendingPacket>>(new Map());
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const speedMultiplierRef = useRef(particleSpeedMultiplier);
  const observationWindowRef = useRef(observationWindowSec * 1000);
  const stretchRafRef = useRef<number | null>(null);
  const [stats, setStats] = useState({ processed: 0, animated: 0, nodes: 0, links: 0 });
  const [, setProjectionVersion] = useState(0);

  const packetNetworkContext = useMemo(
    () =>
      buildPacketNetworkContext({
        contacts,
        config,
        repeaterAdvertPaths,
        splitAmbiguousByTraffic,
        useAdvertPathHints,
      }),
    [contacts, config, repeaterAdvertPaths, splitAmbiguousByTraffic, useAdvertPathHints]
  );

  useEffect(() => {
    speedMultiplierRef.current = particleSpeedMultiplier;
  }, [particleSpeedMultiplier]);

  useEffect(() => {
    observationWindowRef.current = observationWindowSec * 1000;
  }, [observationWindowSec]);

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

  useEffect(() => {
    const sim = simulationRef.current;
    if (!sim) return;
    sim.alphaTarget(letEmDrift ? 0.05 : 0);
  }, [letEmDrift]);

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
    setProjectionVersion((prev) => prev + 1);
  }, []);

  const upsertRenderNode = useCallback(
    (node: PacketNetworkNode, existing?: GraphNode): GraphNode => {
      if (!existing) {
        return buildInitialRenderNode(node);
      }

      existing.name = node.name;
      existing.type = node.type;
      existing.isAmbiguous = node.isAmbiguous;
      existing.lastActivity = node.lastActivity;
      existing.lastActivityReason = node.lastActivityReason;
      existing.lastSeen = node.lastSeen;
      existing.probableIdentity = node.probableIdentity;
      existing.ambiguousNames = node.ambiguousNames;

      if (node.id === 'self') {
        existing.x = 0;
        existing.y = 0;
        existing.z = 0;
        existing.fx = 0;
        existing.fy = 0;
        existing.fz = 0;
        existing.vx = 0;
        existing.vy = 0;
        existing.vz = 0;
      }

      return existing;
    },
    []
  );

  const rebuildRenderProjection = useCallback(() => {
    const projection = projectPacketNetwork(networkStateRef.current, {
      showAmbiguousNodes,
      showAmbiguousPaths,
      collapseLikelyKnownSiblingRepeaters,
    });
    const previousNodes = nodesRef.current;
    const nextNodes = new Map<string, GraphNode>();

    for (const [nodeId, node] of projection.nodes) {
      nextNodes.set(nodeId, upsertRenderNode(node, previousNodes.get(nodeId)));
    }

    const nextLinks = new Map<string, GraphLink>();
    for (const [key, link] of projection.links) {
      nextLinks.set(key, {
        source: link.sourceId,
        target: link.targetId,
        lastActivity: link.lastActivity,
        hasDirectObservation: link.hasDirectObservation,
        hasHiddenIntermediate: link.hasHiddenIntermediate,
        hiddenHopLabels: [...link.hiddenHopLabels],
      });
    }

    nodesRef.current = nextNodes;
    linksRef.current = nextLinks;
    syncSimulation();
  }, [
    collapseLikelyKnownSiblingRepeaters,
    showAmbiguousNodes,
    showAmbiguousPaths,
    syncSimulation,
    upsertRenderNode,
  ]);

  useEffect(() => {
    ensureSelfNode(networkStateRef.current, config?.name || 'Me');
    const selfNode = networkStateRef.current.nodes.get('self');
    if (selfNode) {
      nodesRef.current.set('self', upsertRenderNode(selfNode, nodesRef.current.get('self')));
    }
    syncSimulation();
  }, [config?.name, syncSimulation, upsertRenderNode]);

  useEffect(() => {
    processedRef.current.clear();
    clearPacketNetworkState(networkStateRef.current, { selfName: config?.name || 'Me' });
    nodesRef.current.clear();
    linksRef.current.clear();
    particlesRef.current = [];
    pendingRef.current.clear();
    timersRef.current.forEach((timer) => clearTimeout(timer));
    timersRef.current.clear();

    const selfNode = networkStateRef.current.nodes.get('self');
    if (selfNode) {
      nodesRef.current.set('self', upsertRenderNode(selfNode));
    }

    setStats({ processed: 0, animated: 0, nodes: selfNode ? 1 : 0, links: 0 });
    syncSimulation();
  }, [config?.name, splitAmbiguousByTraffic, syncSimulation, upsertRenderNode, useAdvertPathHints]);

  useEffect(() => {
    rebuildRenderProjection();
  }, [rebuildRenderProjection]);

  const publishPacket = useCallback((packetKey: string) => {
    const pending = pendingRef.current.get(packetKey);
    if (!pending) return;

    pendingRef.current.delete(packetKey);
    timersRef.current.delete(packetKey);

    if (document.hidden) return;

    for (const path of pending.paths) {
      const dedupedPath = dedupeConsecutive(path.nodes);
      if (dedupedPath.length < 2) continue;

      for (let i = 0; i < dedupedPath.length - 1; i++) {
        particlesRef.current.push({
          linkKey: buildLinkKey(dedupedPath[i], dedupedPath[i + 1]),
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

  useEffect(() => {
    let newProcessed = 0;
    let newAnimated = 0;
    let needsProjectionRebuild = false;

    for (const packet of packets) {
      const observationKey = getRawPacketObservationKey(packet);
      if (processedRef.current.has(observationKey)) continue;
      processedRef.current.add(observationKey);
      newProcessed++;

      if (processedRef.current.size > 1000) {
        processedRef.current = new Set(Array.from(processedRef.current).slice(-500));
      }

      const ingested = ingestPacketIntoPacketNetwork(
        networkStateRef.current,
        packetNetworkContext,
        packet
      );
      if (!ingested) continue;
      needsProjectionRebuild = true;

      const projectedPath = projectCanonicalPath(networkStateRef.current, ingested.canonicalPath, {
        showAmbiguousNodes,
        showAmbiguousPaths,
        collapseLikelyKnownSiblingRepeaters,
      });
      if (projectedPath.nodes.length < 2) continue;

      const packetKey = generatePacketKey(ingested.parsed, packet);
      const now = Date.now();
      const existing = pendingRef.current.get(packetKey);

      if (existing && now < existing.expiresAt) {
        existing.paths.push({
          nodes: projectedPath.nodes,
          snr: packet.snr ?? null,
          timestamp: now,
        });
      } else {
        const existingTimer = timersRef.current.get(packetKey);
        if (existingTimer) {
          clearTimeout(existingTimer);
        }
        const windowMs = observationWindowRef.current;
        pendingRef.current.set(packetKey, {
          key: packetKey,
          label: ingested.label,
          paths: [{ nodes: projectedPath.nodes, snr: packet.snr ?? null, timestamp: now }],
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
          const timer = timersRef.current.get(key);
          if (timer) {
            clearTimeout(timer);
          }
          timersRef.current.delete(key);
          pendingRef.current.delete(key);
        }
      }

      newAnimated++;
    }

    if (needsProjectionRebuild) {
      rebuildRenderProjection();
    }
    if (newProcessed > 0) {
      setStats((prev) => ({
        ...prev,
        processed: prev.processed + newProcessed,
        animated: prev.animated + newAnimated,
      }));
    }
  }, [
    packets,
    packetNetworkContext,
    publishPacket,
    collapseLikelyKnownSiblingRepeaters,
    rebuildRenderProjection,
    showAmbiguousNodes,
    showAmbiguousPaths,
  ]);

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
    particlesRef.current.length = 0;
    clearPacketNetworkState(networkStateRef.current, { selfName: config?.name || 'Me' });

    linksRef.current.clear();
    nodesRef.current.clear();
    const selfNode = networkStateRef.current.nodes.get('self');
    if (selfNode) {
      nodesRef.current.set('self', upsertRenderNode(selfNode));
    }

    const sim = simulationRef.current;
    if (sim) {
      sim.nodes(Array.from(nodesRef.current.values()));
      const linkForce = sim.force('link') as ForceLink3D<GraphNode, GraphLink> | undefined;
      linkForce?.links([]);
      sim.alpha(0.3).restart();
    }

    setStats({ processed: 0, animated: 0, nodes: selfNode ? 1 : 0, links: 0 });
  }, [config?.name, upsertRenderNode]);

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

  useEffect(() => {
    if (!pruneStaleNodes) return;

    const staleMs = pruneStaleMinutes * 60 * 1000;
    const pruneIntervalMs = 1000;

    const interval = setInterval(() => {
      const cutoff = Date.now() - staleMs;
      if (prunePacketNetworkState(networkStateRef.current, cutoff)) {
        rebuildRenderProjection();
      }
    }, pruneIntervalMs);

    return () => clearInterval(interval);
  }, [pruneStaleMinutes, pruneStaleNodes, rebuildRenderProjection]);

  return {
    nodes: nodesRef.current,
    links: linksRef.current,
    canonicalNodes: networkStateRef.current.nodes,
    canonicalNeighborIds: snapshotNeighborIds(networkStateRef.current),
    renderedNodeIds: new Set(nodesRef.current.keys()),
    particles: particlesRef.current,
    stats,
    expandContract,
    clearAndReset,
  };
}
