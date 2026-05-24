export const DISTANCE_UNIT_KEY = 'remoteterm-distance-unit';

export const DISTANCE_UNITS = ['imperial', 'metric', 'smoots'] as const;

export type DistanceUnit = (typeof DISTANCE_UNITS)[number];

export const DISTANCE_UNIT_LABELS: Record<DistanceUnit, string> = {
  imperial: 'Imperial',
  metric: 'Metric',
  smoots: 'Smoots',
};

function isDistanceUnit(value: unknown): value is DistanceUnit {
  return typeof value === 'string' && DISTANCE_UNITS.includes(value as DistanceUnit);
}

export function getSavedDistanceUnit(): DistanceUnit {
  try {
    const raw = localStorage.getItem(DISTANCE_UNIT_KEY);
    return isDistanceUnit(raw) ? raw : 'metric';
  } catch {
    return 'metric';
  }
}

export function setSavedDistanceUnit(unit: DistanceUnit): void {
  try {
    localStorage.setItem(DISTANCE_UNIT_KEY, unit);
  } catch {
    // localStorage may be unavailable
  }
}
