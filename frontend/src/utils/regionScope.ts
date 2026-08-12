// Canonical persisted marker meaning "force this channel unscoped / plain flood"
// (mirrors the backend UNSCOPED_OVERRIDE_MARKER). Distinct from null, which means
// "inherit the global scope".
export const UNSCOPED_OVERRIDE_MARKER = '*';

export function isUnscopedMarker(scope: string | null | undefined): boolean {
  return scope === UNSCOPED_OVERRIDE_MARKER;
}

export function stripRegionScopePrefix(scope: string | null | undefined): string {
  if (!scope) return '';
  return scope.startsWith('#') ? scope.slice(1) : scope;
}

// Compact, human-readable label for a channel's persisted flood-scope override,
// suitable for a header badge. Returns null when there is no override (inherit),
// "unscoped" for the unscoped marker, and the region name as-is otherwise.
export function floodScopeOverrideLabel(scope: string | null | undefined): string | null {
  if (!scope) return null;
  if (isUnscopedMarker(scope)) return 'unscoped';
  return scope;
}
