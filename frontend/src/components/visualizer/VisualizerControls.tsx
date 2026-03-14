import { Checkbox } from '../ui/checkbox';
import { PACKET_LEGEND_ITEMS } from '../../utils/visualizerUtils';
import { NODE_LEGEND_ITEMS } from './shared';

interface VisualizerControlsProps {
  showControls: boolean;
  setShowControls: (value: boolean) => void;
  fullScreen?: boolean;
  onFullScreenChange?: (fullScreen: boolean) => void;
  showAmbiguousPaths: boolean;
  setShowAmbiguousPaths: (value: boolean) => void;
  showAmbiguousNodes: boolean;
  setShowAmbiguousNodes: (value: boolean) => void;
  useAdvertPathHints: boolean;
  setUseAdvertPathHints: (value: boolean) => void;
  collapseLikelyKnownSiblingRepeaters: boolean;
  setCollapseLikelyKnownSiblingRepeaters: (value: boolean) => void;
  splitAmbiguousByTraffic: boolean;
  setSplitAmbiguousByTraffic: (value: boolean) => void;
  observationWindowSec: number;
  setObservationWindowSec: (value: number) => void;
  pruneStaleNodes: boolean;
  setPruneStaleNodes: (value: boolean) => void;
  pruneStaleMinutes: number;
  setPruneStaleMinutes: (value: number) => void;
  letEmDrift: boolean;
  setLetEmDrift: (value: boolean) => void;
  autoOrbit: boolean;
  setAutoOrbit: (value: boolean) => void;
  chargeStrength: number;
  setChargeStrength: (value: number) => void;
  particleSpeedMultiplier: number;
  setParticleSpeedMultiplier: (value: number) => void;
  nodeCount: number;
  linkCount: number;
  onExpandContract: () => void;
  onClearAndReset: () => void;
}

export function VisualizerControls({
  showControls,
  setShowControls,
  fullScreen,
  onFullScreenChange,
  showAmbiguousPaths,
  setShowAmbiguousPaths,
  showAmbiguousNodes,
  setShowAmbiguousNodes,
  useAdvertPathHints,
  setUseAdvertPathHints,
  collapseLikelyKnownSiblingRepeaters,
  setCollapseLikelyKnownSiblingRepeaters,
  splitAmbiguousByTraffic,
  setSplitAmbiguousByTraffic,
  observationWindowSec,
  setObservationWindowSec,
  pruneStaleNodes,
  setPruneStaleNodes,
  pruneStaleMinutes,
  setPruneStaleMinutes,
  letEmDrift,
  setLetEmDrift,
  autoOrbit,
  setAutoOrbit,
  chargeStrength,
  setChargeStrength,
  particleSpeedMultiplier,
  setParticleSpeedMultiplier,
  nodeCount,
  linkCount,
  onExpandContract,
  onClearAndReset,
}: VisualizerControlsProps) {
  return (
    <>
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

      <div
        className={`absolute top-4 left-4 bg-background/80 backdrop-blur-sm rounded-lg p-3 text-xs border border-border z-10 transition-opacity ${!showControls ? 'opacity-40 hover:opacity-100' : ''}`}
      >
        <div className="flex flex-col gap-2">
          <div className="flex flex-col gap-2">
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
          {showControls && (
            <>
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
                <details className="rounded border border-border/60 px-2 py-1">
                  <summary className="cursor-pointer select-none text-muted-foreground">
                    Advanced
                  </summary>
                  <div className="mt-2 flex flex-col gap-2">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <Checkbox
                        checked={useAdvertPathHints}
                        onCheckedChange={(c) => setUseAdvertPathHints(c === true)}
                        disabled={!showAmbiguousPaths}
                      />
                      <span
                        title="Use stored repeater advert paths to assign likely identity labels for ambiguous repeater nodes."
                        className={!showAmbiguousPaths ? 'text-muted-foreground' : ''}
                      >
                        Use repeater advert-path identity hints
                      </span>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <Checkbox
                        checked={collapseLikelyKnownSiblingRepeaters}
                        onCheckedChange={(c) => setCollapseLikelyKnownSiblingRepeaters(c === true)}
                        disabled={!showAmbiguousPaths || !useAdvertPathHints}
                      />
                      <span
                        title="When an ambiguous repeater has a high-confidence likely-identity that matches a sibling definitely-known repeater, and they both connect to the same next hop, collapse them into the known repeater. This should resolve more ambiguity as the mesh navigates the 1.14 upgrade."
                        className={
                          !showAmbiguousPaths || !useAdvertPathHints ? 'text-muted-foreground' : ''
                        }
                      >
                        Collapse likely sibling repeaters
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
                            Math.max(1, Math.min(60, parseInt(e.target.value, 10) || 1))
                          )
                        }
                        className="w-12 px-1 py-0.5 bg-background border border-border rounded text-xs text-center"
                      />
                      <span className="text-muted-foreground">sec</span>
                    </div>
                  </div>
                </details>
                <div className="border-t border-border pt-2 mt-1 flex flex-col gap-2">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <Checkbox
                      checked={pruneStaleNodes}
                      onCheckedChange={(c) => setPruneStaleNodes(c === true)}
                    />
                    <span title="Automatically remove nodes with no traffic within the configured window to keep the mesh manageable">
                      Only show recently heard/in-a-path nodes
                    </span>
                  </label>
                  {pruneStaleNodes && (
                    <div className="flex items-center gap-2 pl-6">
                      <label
                        htmlFor="prune-window"
                        className="text-muted-foreground whitespace-nowrap"
                      >
                        Window:
                      </label>
                      <input
                        id="prune-window"
                        type="number"
                        min={1}
                        max={60}
                        value={pruneStaleMinutes}
                        onChange={(e) => {
                          const v = parseInt(e.target.value, 10);
                          if (!isNaN(v) && v >= 1 && v <= 60) setPruneStaleMinutes(v);
                        }}
                        className="w-14 rounded border border-border bg-background px-2 py-0.5 text-sm"
                      />
                      <span className="text-muted-foreground" aria-hidden="true">
                        min
                      </span>
                    </div>
                  )}
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
                      htmlFor="viz-repulsion"
                      className="text-muted-foreground"
                      title="How strongly nodes repel each other. Higher values spread nodes out more."
                    >
                      Repulsion: {Math.abs(chargeStrength)}
                    </label>
                    <input
                      id="viz-repulsion"
                      type="range"
                      min="50"
                      max="2500"
                      value={Math.abs(chargeStrength)}
                      onChange={(e) => setChargeStrength(-parseInt(e.target.value, 10))}
                      className="w-full h-2 bg-border rounded-lg appearance-none cursor-pointer accent-primary"
                    />
                  </div>
                  <div className="flex flex-col gap-1 mt-1">
                    <label
                      htmlFor="viz-packet-speed"
                      className="text-muted-foreground"
                      title="How fast particles travel along links. Higher values make packets move faster."
                    >
                      Packet speed: {particleSpeedMultiplier}x
                    </label>
                    <input
                      id="viz-packet-speed"
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
                  onClick={onExpandContract}
                  className="mt-1 px-3 py-1.5 bg-primary/20 hover:bg-primary/30 text-primary rounded text-xs transition-colors"
                  title="Expand nodes apart then contract back - can help untangle the graph"
                >
                  Oooh Big Stretch!
                </button>
                <button
                  onClick={onClearAndReset}
                  className="mt-1 rounded border border-warning/40 bg-warning/10 px-3 py-1.5 text-warning text-xs transition-colors hover:bg-warning/20"
                  title="Clear all nodes and links from the visualization - packets are preserved"
                >
                  Clear &amp; Reset
                </button>
              </div>
              <div className="border-t border-border pt-2 mt-1">
                <div>Nodes: {nodeCount}</div>
                <div>Links: {linkCount}</div>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
