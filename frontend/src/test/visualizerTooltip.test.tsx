import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { VisualizerTooltip } from '../components/visualizer/VisualizerTooltip';
import type { PacketNetworkNode } from '../networkGraph/packetNetworkGraph';

function createNode(
  overrides: Partial<PacketNetworkNode> & Pick<PacketNetworkNode, 'id' | 'type'>
): PacketNetworkNode {
  return {
    id: overrides.id,
    type: overrides.type,
    name: overrides.name ?? null,
    isAmbiguous: overrides.isAmbiguous ?? false,
    lastActivity: overrides.lastActivity ?? Date.now(),
    probableIdentity: overrides.probableIdentity,
    ambiguousNames: overrides.ambiguousNames,
    lastActivityReason: overrides.lastActivityReason,
  };
}

describe('VisualizerTooltip', () => {
  it('renders nothing without an active node', () => {
    const { container } = render(
      <VisualizerTooltip
        activeNodeId={null}
        canonicalNodes={new Map()}
        canonicalNeighborIds={new Map()}
        renderedNodeIds={new Set()}
      />
    );

    expect(container).toBeEmptyDOMElement();
  });

  it('renders ambiguous node details and neighbors', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-03-10T22:00:00Z'));

    const node = createNode({
      id: '?32',
      type: 'repeater',
      name: 'Likely Relay',
      isAmbiguous: true,
      probableIdentity: 'Likely Relay',
      ambiguousNames: ['Relay A', 'Relay B'],
      lastActivity: new Date('2026-03-10T21:58:30Z').getTime(),
      lastActivityReason: 'Relayed GT',
    });
    const neighbor = createNode({
      id: 'abcd1234ef56',
      type: 'client',
      name: 'Neighbor Node',
      ambiguousNames: ['Alt Neighbor'],
    });
    const hiddenRepeater = createNode({
      id: '?44',
      type: 'repeater',
      name: '44',
      isAmbiguous: true,
    });

    render(
      <VisualizerTooltip
        activeNodeId={node.id}
        canonicalNodes={
          new Map([
            [node.id, node],
            [neighbor.id, neighbor],
            [hiddenRepeater.id, hiddenRepeater],
          ])
        }
        canonicalNeighborIds={new Map([[node.id, [neighbor.id, hiddenRepeater.id]]])}
        renderedNodeIds={new Set([node.id, neighbor.id])}
      />
    );

    expect(screen.getByText('Likely Relay')).toBeInTheDocument();
    expect(screen.getByText('ID: ?32')).toBeInTheDocument();
    expect(screen.getByText('Type: repeater (ambiguous)')).toBeInTheDocument();
    expect(screen.getByText('Probably: Likely Relay')).toBeInTheDocument();
    expect(screen.getByText('Other possible: Relay A, Relay B')).toBeInTheDocument();
    expect(screen.getByText('Last active: 1m 30s ago')).toBeInTheDocument();
    expect(screen.getByText('Reason: Relayed GT')).toBeInTheDocument();
    expect(screen.getByText('Neighbor Node')).toBeInTheDocument();
    expect(screen.getByText('(Alt Neighbor)')).toBeInTheDocument();
    expect(screen.getByText('44')).toBeInTheDocument();
    expect(screen.getByText('(hidden)')).toBeInTheDocument();

    vi.useRealTimers();
  });
});
