import type { PacketNetworkNode } from '../../networkGraph/packetNetworkGraph';
import { formatRelativeTime } from './shared';

interface VisualizerTooltipProps {
  activeNodeId: string | null;
  canonicalNodes: Map<string, PacketNetworkNode>;
  canonicalNeighborIds: Map<string, string[]>;
  renderedNodeIds: Set<string>;
}

export function VisualizerTooltip({
  activeNodeId,
  canonicalNodes,
  canonicalNeighborIds,
  renderedNodeIds,
}: VisualizerTooltipProps) {
  if (!activeNodeId) return null;

  const node = canonicalNodes.get(activeNodeId);
  if (!node) return null;

  const neighborIds = canonicalNeighborIds.get(activeNodeId) ?? [];
  const neighbors = neighborIds
    .map((nid) => {
      const neighbor = canonicalNodes.get(nid);
      if (!neighbor) return null;
      const displayName =
        neighbor.name || (neighbor.type === 'self' ? 'Me' : neighbor.id.slice(0, 8));
      return {
        id: nid,
        name: displayName,
        ambiguousNames: neighbor.ambiguousNames,
        hidden: !renderedNodeIds.has(nid),
      };
    })
    .filter((neighbor): neighbor is NonNullable<typeof neighbor> => neighbor !== null);

  return (
    <div className="absolute top-4 right-4 bg-background/90 backdrop-blur-sm rounded-lg p-3 text-xs border border-border z-10 max-w-72 max-h-[calc(100%-2rem)] overflow-y-auto">
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
        {node.type !== 'self' && (
          <div className="text-muted-foreground border-t border-border pt-1 mt-1">
            <div>Last active: {formatRelativeTime(node.lastActivity)}</div>
            {node.lastActivityReason && <div>Reason: {node.lastActivityReason}</div>}
          </div>
        )}
        {neighbors.length > 0 && (
          <div className="text-muted-foreground border-t border-border pt-1 mt-1">
            <div className="mb-0.5">Traffic exchanged with:</div>
            <ul className="pl-3 flex flex-col gap-0.5">
              {neighbors.map((neighbor) => (
                <li key={neighbor.id}>
                  {neighbor.name}
                  {neighbor.hidden && <span className="text-muted-foreground/60"> (hidden)</span>}
                  {neighbor.ambiguousNames && neighbor.ambiguousNames.length > 0 && (
                    <span className="text-muted-foreground/60">
                      {' '}
                      ({neighbor.ambiguousNames.join(', ')})
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
