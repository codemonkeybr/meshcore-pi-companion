import * as THREE from 'three';
import type { SimulationLinkDatum } from 'd3-force';
import type { SimulationNodeDatum3D } from 'd3-force-3d';
import type { CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js';

import type { NodeType } from '../../utils/visualizerUtils';

export interface GraphNode extends SimulationNodeDatum3D {
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

export interface GraphLink extends SimulationLinkDatum<GraphNode> {
  source: string | GraphNode;
  target: string | GraphNode;
  lastActivity: number;
  hasDirectObservation: boolean;
  hasHiddenIntermediate: boolean;
  hiddenHopLabels: string[];
}

export interface NodeMeshData {
  mesh: THREE.Mesh;
  label: CSS2DObject;
  labelDiv: HTMLDivElement;
}

export const NODE_COLORS = {
  self: 0x22c55e,
  repeater: 0x3b82f6,
  client: 0xffffff,
  ambiguous: 0x9ca3af,
} as const;

export const NODE_LEGEND_ITEMS = [
  { color: '#22c55e', label: 'You', size: 14 },
  { color: '#3b82f6', label: 'Repeater', size: 10 },
  { color: '#ffffff', label: 'Node', size: 10 },
  { color: '#9ca3af', label: 'Ambiguous', size: 10 },
] as const;

export function getBaseNodeColor(node: Pick<GraphNode, 'type' | 'isAmbiguous'>): number {
  if (node.type === 'self') return NODE_COLORS.self;
  if (node.type === 'repeater') return NODE_COLORS.repeater;
  return node.isAmbiguous ? NODE_COLORS.ambiguous : NODE_COLORS.client;
}

export function growFloat32Buffer(current: Float32Array, requiredLength: number): Float32Array {
  let nextLength = Math.max(12, current.length);
  while (nextLength < requiredLength) {
    nextLength *= 2;
  }
  return new Float32Array(nextLength);
}

export function arraysEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

export function formatRelativeTime(timestamp: number): string {
  const seconds = Math.floor((Date.now() - timestamp) / 1000);
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return secs > 0 ? `${minutes}m ${secs}s ago` : `${minutes}m ago`;
}

export function getSceneNodeLabel(node: Pick<GraphNode, 'id' | 'name' | 'type' | 'isAmbiguous'>) {
  const baseLabel = node.name || (node.type === 'self' ? 'Me' : node.id.slice(0, 8));
  return node.isAmbiguous ? `${baseLabel} (?)` : baseLabel;
}

export function normalizePacketTimestampMs(timestamp: number | null | undefined): number {
  if (!Number.isFinite(timestamp) || !timestamp || timestamp <= 0) {
    return Date.now();
  }
  const ts = Number(timestamp);
  return ts > 1_000_000_000_000 ? ts : ts * 1000;
}
