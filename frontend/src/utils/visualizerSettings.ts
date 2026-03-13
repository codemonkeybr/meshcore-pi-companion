const VISUALIZER_SETTINGS_KEY = 'remoteterm-visualizer-settings';

export interface VisualizerSettings {
  showAmbiguousPaths: boolean;
  showAmbiguousNodes: boolean;
  useAdvertPathHints: boolean;
  collapseLikelyKnownSiblingRepeaters: boolean;
  splitAmbiguousByTraffic: boolean;
  chargeStrength: number;
  observationWindowSec: number;
  letEmDrift: boolean;
  particleSpeedMultiplier: number;
  pruneStaleNodes: boolean;
  pruneStaleMinutes: number;
  autoOrbit: boolean;
  showControls: boolean;
  hidePacketFeed: boolean;
}

export const VISUALIZER_DEFAULTS: VisualizerSettings = {
  showAmbiguousPaths: true,
  showAmbiguousNodes: false,
  useAdvertPathHints: true,
  collapseLikelyKnownSiblingRepeaters: true,
  splitAmbiguousByTraffic: true,
  chargeStrength: -200,
  observationWindowSec: 15,
  letEmDrift: true,
  particleSpeedMultiplier: 2,
  pruneStaleNodes: false,
  pruneStaleMinutes: 5,
  autoOrbit: false,
  showControls: true,
  hidePacketFeed: false,
};

export function getVisualizerSettings(): VisualizerSettings {
  try {
    const raw = localStorage.getItem(VISUALIZER_SETTINGS_KEY);
    if (!raw) return { ...VISUALIZER_DEFAULTS };
    const parsed = JSON.parse(raw) as Partial<VisualizerSettings>;
    return {
      showAmbiguousPaths:
        typeof parsed.showAmbiguousPaths === 'boolean'
          ? parsed.showAmbiguousPaths
          : VISUALIZER_DEFAULTS.showAmbiguousPaths,
      showAmbiguousNodes:
        typeof parsed.showAmbiguousNodes === 'boolean'
          ? parsed.showAmbiguousNodes
          : VISUALIZER_DEFAULTS.showAmbiguousNodes,
      useAdvertPathHints:
        typeof parsed.useAdvertPathHints === 'boolean'
          ? parsed.useAdvertPathHints
          : VISUALIZER_DEFAULTS.useAdvertPathHints,
      collapseLikelyKnownSiblingRepeaters:
        typeof parsed.collapseLikelyKnownSiblingRepeaters === 'boolean'
          ? parsed.collapseLikelyKnownSiblingRepeaters
          : VISUALIZER_DEFAULTS.collapseLikelyKnownSiblingRepeaters,
      splitAmbiguousByTraffic:
        typeof parsed.splitAmbiguousByTraffic === 'boolean'
          ? parsed.splitAmbiguousByTraffic
          : VISUALIZER_DEFAULTS.splitAmbiguousByTraffic,
      chargeStrength:
        typeof parsed.chargeStrength === 'number'
          ? parsed.chargeStrength
          : VISUALIZER_DEFAULTS.chargeStrength,
      observationWindowSec:
        typeof parsed.observationWindowSec === 'number'
          ? parsed.observationWindowSec
          : VISUALIZER_DEFAULTS.observationWindowSec,
      letEmDrift:
        typeof parsed.letEmDrift === 'boolean' ? parsed.letEmDrift : VISUALIZER_DEFAULTS.letEmDrift,
      particleSpeedMultiplier:
        typeof parsed.particleSpeedMultiplier === 'number'
          ? parsed.particleSpeedMultiplier
          : VISUALIZER_DEFAULTS.particleSpeedMultiplier,
      pruneStaleNodes:
        typeof parsed.pruneStaleNodes === 'boolean'
          ? parsed.pruneStaleNodes
          : VISUALIZER_DEFAULTS.pruneStaleNodes,
      pruneStaleMinutes:
        typeof parsed.pruneStaleMinutes === 'number' && parsed.pruneStaleMinutes >= 1
          ? parsed.pruneStaleMinutes
          : VISUALIZER_DEFAULTS.pruneStaleMinutes,
      autoOrbit:
        typeof parsed.autoOrbit === 'boolean' ? parsed.autoOrbit : VISUALIZER_DEFAULTS.autoOrbit,
      showControls:
        typeof parsed.showControls === 'boolean'
          ? parsed.showControls
          : VISUALIZER_DEFAULTS.showControls,
      hidePacketFeed:
        typeof parsed.hidePacketFeed === 'boolean'
          ? parsed.hidePacketFeed
          : VISUALIZER_DEFAULTS.hidePacketFeed,
    };
  } catch {
    return { ...VISUALIZER_DEFAULTS };
  }
}

export function saveVisualizerSettings(settings: VisualizerSettings): void {
  try {
    localStorage.setItem(VISUALIZER_SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // localStorage may be unavailable
  }
}
