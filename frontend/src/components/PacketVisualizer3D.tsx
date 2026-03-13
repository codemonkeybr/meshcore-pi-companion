import { useEffect, useRef, useState } from 'react';

import { api } from '../api';
import type { Contact, ContactAdvertPathSummary, RadioConfig, RawPacket } from '../types';
import { getVisualizerSettings, saveVisualizerSettings } from '../utils/visualizerSettings';
import { VisualizerControls } from './visualizer/VisualizerControls';
import { VisualizerTooltip } from './visualizer/VisualizerTooltip';
import { useVisualizerData3D } from './visualizer/useVisualizerData3D';
import { useVisualizer3DScene } from './visualizer/useVisualizer3DScene';

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

  const [savedSettings] = useState(getVisualizerSettings);
  const [showAmbiguousPaths, setShowAmbiguousPaths] = useState(savedSettings.showAmbiguousPaths);
  const [showAmbiguousNodes, setShowAmbiguousNodes] = useState(savedSettings.showAmbiguousNodes);
  const [useAdvertPathHints, setUseAdvertPathHints] = useState(savedSettings.useAdvertPathHints);
  const [collapseLikelyKnownSiblingRepeaters, setCollapseLikelyKnownSiblingRepeaters] = useState(
    savedSettings.collapseLikelyKnownSiblingRepeaters
  );
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
  const [pruneStaleMinutes, setPruneStaleMinutes] = useState(savedSettings.pruneStaleMinutes);
  const [repeaterAdvertPaths, setRepeaterAdvertPaths] = useState<ContactAdvertPathSummary[]>([]);

  useEffect(() => {
    saveVisualizerSettings({
      ...getVisualizerSettings(),
      showAmbiguousPaths,
      showAmbiguousNodes,
      useAdvertPathHints,
      collapseLikelyKnownSiblingRepeaters,
      splitAmbiguousByTraffic,
      chargeStrength,
      observationWindowSec,
      letEmDrift,
      particleSpeedMultiplier,
      pruneStaleNodes,
      pruneStaleMinutes,
      autoOrbit,
      showControls,
    });
  }, [
    showAmbiguousPaths,
    showAmbiguousNodes,
    useAdvertPathHints,
    collapseLikelyKnownSiblingRepeaters,
    splitAmbiguousByTraffic,
    chargeStrength,
    observationWindowSec,
    letEmDrift,
    particleSpeedMultiplier,
    pruneStaleNodes,
    pruneStaleMinutes,
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

  const data = useVisualizerData3D({
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
  });

  const { hoveredNodeId, pinnedNodeId } = useVisualizer3DScene({
    containerRef,
    data,
    autoOrbit,
  });

  const tooltipNodeId = pinnedNodeId ?? hoveredNodeId;

  return (
    <div
      ref={containerRef}
      className="w-full h-full bg-background relative overflow-hidden"
      role="img"
      aria-label="3D mesh network visualizer showing radio nodes as colored spheres and packet transmissions as animated arcs between them"
    >
      <VisualizerControls
        showControls={showControls}
        setShowControls={setShowControls}
        fullScreen={fullScreen}
        onFullScreenChange={onFullScreenChange}
        showAmbiguousPaths={showAmbiguousPaths}
        setShowAmbiguousPaths={setShowAmbiguousPaths}
        showAmbiguousNodes={showAmbiguousNodes}
        setShowAmbiguousNodes={setShowAmbiguousNodes}
        useAdvertPathHints={useAdvertPathHints}
        setUseAdvertPathHints={setUseAdvertPathHints}
        collapseLikelyKnownSiblingRepeaters={collapseLikelyKnownSiblingRepeaters}
        setCollapseLikelyKnownSiblingRepeaters={setCollapseLikelyKnownSiblingRepeaters}
        splitAmbiguousByTraffic={splitAmbiguousByTraffic}
        setSplitAmbiguousByTraffic={setSplitAmbiguousByTraffic}
        observationWindowSec={observationWindowSec}
        setObservationWindowSec={setObservationWindowSec}
        pruneStaleNodes={pruneStaleNodes}
        setPruneStaleNodes={setPruneStaleNodes}
        pruneStaleMinutes={pruneStaleMinutes}
        setPruneStaleMinutes={setPruneStaleMinutes}
        letEmDrift={letEmDrift}
        setLetEmDrift={setLetEmDrift}
        autoOrbit={autoOrbit}
        setAutoOrbit={setAutoOrbit}
        chargeStrength={chargeStrength}
        setChargeStrength={setChargeStrength}
        particleSpeedMultiplier={particleSpeedMultiplier}
        setParticleSpeedMultiplier={setParticleSpeedMultiplier}
        nodeCount={data.stats.nodes}
        linkCount={data.stats.links}
        onExpandContract={data.expandContract}
        onClearAndReset={data.clearAndReset}
      />

      <VisualizerTooltip
        activeNodeId={tooltipNodeId}
        canonicalNodes={data.canonicalNodes}
        canonicalNeighborIds={data.canonicalNeighborIds}
        renderedNodeIds={data.renderedNodeIds}
      />
    </div>
  );
}
