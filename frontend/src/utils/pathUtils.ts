import type { Contact, ContactRoute, RadioConfig, MessagePath } from '../types';
import type { DistanceUnit } from './distanceUnits';
import { CONTACT_TYPE_REPEATER } from '../types';

const MAX_PATH_BYTES = 64;

export interface PathHop {
  prefix: string; // Hex hop identifier (e.g., "1A" for 1-byte, "1A2B" for 2-byte)
  matches: Contact[]; // Matched repeaters (empty=unknown, multiple=ambiguous)
  distanceFromPrev: number | null; // km from previous hop
}

export interface ResolvedPath {
  sender: { name: string; prefix: string; lat: number | null; lon: number | null };
  hops: PathHop[];
  receiver: {
    name: string;
    prefix: string;
    lat: number | null;
    lon: number | null;
    publicKey: string | null;
  };
  totalDistances: number[] | null; // Single-element array with sum of unambiguous distances
  /** True if path has any gaps (unknown, ambiguous, or missing location hops) */
  hasGaps: boolean;
}

export interface SenderInfo {
  name: string;
  publicKeyOrPrefix: string;
  lat: number | null;
  lon: number | null;
  pathHashMode?: number | null;
}

export interface EffectiveContactRoute {
  path: string | null;
  pathLen: number;
  pathHashMode: number;
  forced: boolean;
  source: 'override' | 'direct' | 'flood';
}

function normalizePathHashMode(mode: number | null | undefined): number | null {
  if (mode == null || !Number.isInteger(mode) || mode < 0 || mode > 2) {
    return null;
  }
  return mode;
}

function inferPathHashMode(
  path: string | null | undefined,
  hopCount?: number | null
): number | null {
  if (!path || path.length === 0 || hopCount == null || hopCount <= 0) {
    return null;
  }

  const charsPerHop = path.length / hopCount;
  if (
    charsPerHop < 2 ||
    charsPerHop > 6 ||
    charsPerHop % 2 !== 0 ||
    charsPerHop * hopCount !== path.length
  ) {
    return null;
  }

  return charsPerHop / 2 - 1;
}

function formatEndpointPrefix(key: string | null | undefined, pathHashMode: number | null): string {
  if (!key) {
    return '??';
  }

  const normalized = key.toUpperCase();
  const hashMode = normalizePathHashMode(pathHashMode) ?? 0;
  const chars = (hashMode + 1) * 2;
  return normalized.slice(0, Math.min(chars, normalized.length));
}

/**
 * Split hex path string into per-hop chunks.
 *
 * When hopCount is provided (from path_len metadata), the bytes-per-hop is
 * derived from the hex length divided by the hop count. This correctly handles
 * multi-byte hop identifiers (1, 2, or 3 bytes per hop).
 *
 * Falls back to 2-char (1-byte) chunks when hopCount is missing or doesn't
 * divide evenly — matching legacy behavior.
 */
export function parsePathHops(path: string | null | undefined, hopCount?: number | null): string[] {
  if (!path || path.length === 0) {
    return [];
  }

  const normalized = path.toUpperCase();

  // Derive chars-per-hop from metadata when available
  let charsPerHop = 2; // default: 1-byte hops
  if (hopCount && hopCount > 0) {
    const derived = normalized.length / hopCount;
    // Accept only valid even widths (2, 4, 6) that divide evenly
    if (derived >= 2 && derived % 2 === 0 && derived * hopCount === normalized.length) {
      charsPerHop = derived;
    }
  }

  const hops: string[] = [];
  for (let i = 0; i + charsPerHop <= normalized.length; i += charsPerHop) {
    hops.push(normalized.slice(i, i + charsPerHop));
  }

  return hops;
}

export function hasRoutingOverride(contact: Contact): boolean {
  return (
    (contact.route_override !== null && contact.route_override !== undefined) ||
    (contact.route_override_len !== null && contact.route_override_len !== undefined)
  );
}

export function getDirectContactRoute(contact: Contact): ContactRoute | null {
  if (contact.direct_route) {
    return contact.direct_route;
  }

  if (contact.direct_path_len < 0) {
    return null;
  }

  return {
    path: contact.direct_path ?? '',
    path_len: contact.direct_path_len,
    path_hash_mode:
      normalizePathHashMode(contact.direct_path_hash_mode) ??
      inferPathHashMode(contact.direct_path, contact.direct_path_len) ??
      0,
  };
}

function getRouteOverride(contact: Contact): ContactRoute | null {
  if (contact.route_override) {
    return contact.route_override;
  }

  if (!hasRoutingOverride(contact)) {
    return null;
  }

  const pathLen = contact.route_override_len ?? -1;
  let pathHashMode = normalizePathHashMode(contact.route_override_hash_mode);
  if (pathLen === -1) {
    pathHashMode = -1;
  } else if (pathHashMode == null) {
    pathHashMode = inferPathHashMode(contact.route_override_path, pathLen) ?? 0;
  }

  return {
    path: contact.route_override_path ?? '',
    path_len: pathLen,
    path_hash_mode: pathHashMode,
  };
}

export function getEffectiveContactRoute(contact: Contact): EffectiveContactRoute {
  const route = contact.effective_route;
  if (route) {
    return {
      path: route.path || null,
      pathLen: route.path_len,
      pathHashMode: route.path_hash_mode,
      forced: contact.effective_route_source === 'override',
      source: contact.effective_route_source ?? 'flood',
    };
  }

  const directRoute = getDirectContactRoute(contact);
  const overrideRoute = getRouteOverride(contact);
  const resolvedRoute = overrideRoute ?? directRoute;
  const source = overrideRoute ? 'override' : directRoute ? 'direct' : 'flood';
  const pathLen = resolvedRoute?.path_len ?? -1;
  let pathHashMode = resolvedRoute?.path_hash_mode ?? null;

  if (pathLen === -1) {
    pathHashMode = -1;
  } else if (pathHashMode == null || pathHashMode < 0 || pathHashMode > 2) {
    pathHashMode = inferPathHashMode(resolvedRoute?.path, pathLen) ?? 0;
  }

  return {
    path: resolvedRoute?.path || null,
    pathLen,
    pathHashMode,
    forced: source === 'override',
    source,
  };
}

export function formatRouteLabel(pathLen: number, capitalize: boolean = false): string {
  const label =
    pathLen === -1
      ? 'flood'
      : pathLen === 0
        ? 'direct'
        : `${pathLen} hop${pathLen === 1 ? '' : 's'}`;
  return capitalize ? label.charAt(0).toUpperCase() + label.slice(1) : label;
}

/**
 * Format the learned direct route for display in route-editing dialogs,
 * e.g. "2 hops (AE -> F1)", "Direct", or "Flood".
 */
export function formatLearnedRouteSummary(contact: Contact): string {
  const directRoute = getDirectContactRoute(contact);
  if (!directRoute) {
    return formatRouteLabel(-1, true);
  }
  const hops = parsePathHops(directRoute.path, directRoute.path_len);
  const label = formatRouteLabel(directRoute.path_len, true);
  return hops.length > 0 ? `${label} (${hops.join(' -> ')})` : label;
}

/**
 * Format the forced (override) route for display in route-editing dialogs,
 * matching the learned-route format. Returns null when no override is set.
 */
export function formatForcedRouteSummary(contact: Contact): string | null {
  if (!hasRoutingOverride(contact)) {
    return null;
  }
  const effectiveRoute = getEffectiveContactRoute(contact);
  if (effectiveRoute.pathLen === -1) {
    return formatRouteLabel(-1, true);
  }
  const hops = parsePathHops(effectiveRoute.path, effectiveRoute.pathLen);
  const label = formatRouteLabel(effectiveRoute.pathLen, true);
  return hops.length > 0 ? `${label} (${hops.join(' -> ')})` : label;
}

export function formatRoutingOverrideInput(contact: Contact): string {
  const routeOverride = getRouteOverride(contact);
  if (!routeOverride) {
    return '';
  }
  if (routeOverride.path_len === -1) {
    return '-1';
  }
  if (routeOverride.path_len === 0) {
    return '0';
  }
  return parsePathHops(routeOverride.path, routeOverride.path_len)
    .map((hop) => hop.toLowerCase())
    .join(',');
}

/**
 * Extract the payload portion from a raw packet hex string using firmware-equivalent
 * path-byte validation. Returns null for malformed or payload-less packets.
 */
export function extractPacketPayloadHex(packetHex: string): string | null {
  if (packetHex.length < 4) {
    return null;
  }

  try {
    const normalized = packetHex.toUpperCase();
    const header = parseInt(normalized.slice(0, 2), 16);
    const routeType = header & 0x03;
    let offset = 2;

    if (routeType === 0x00 || routeType === 0x03) {
      if (normalized.length < offset + 8) {
        return null;
      }
      offset += 8;
    }

    if (normalized.length < offset + 2) {
      return null;
    }
    const pathByte = parseInt(normalized.slice(offset, offset + 2), 16);
    offset += 2;

    const hashMode = (pathByte >> 6) & 0x03;
    if (hashMode === 0x03) {
      return null;
    }
    const hopCount = pathByte & 0x3f;
    const hashSize = hashMode + 1;
    const pathByteLen = hopCount * hashSize;
    if (pathByteLen > MAX_PATH_BYTES) {
      return null;
    }

    const pathHexChars = pathByteLen * 2;
    if (normalized.length < offset + pathHexChars) {
      return null;
    }
    offset += pathHexChars;

    if (offset >= normalized.length) {
      return null;
    }

    return normalized.slice(offset);
  } catch {
    return null;
  }
}

/**
 * Find contacts matching first 2 chars of public key (repeaters only for intermediate hops)
 */
export function findContactsByPrefix(
  prefix: string,
  contacts: Contact[],
  repeatersOnly: boolean = true
): Contact[] {
  const normalizedPrefix = prefix.toUpperCase();
  return contacts.filter((c) => {
    if (repeatersOnly && c.type !== CONTACT_TYPE_REPEATER) {
      return false;
    }
    return c.public_key.toUpperCase().startsWith(normalizedPrefix);
  });
}

/**
 * Calculate distance between two points using Haversine formula
 * @returns Distance in km, or null if coordinates are missing
 */
export function calculateDistance(
  lat1: number | null,
  lon1: number | null,
  lat2: number | null,
  lon2: number | null
): number | null {
  if (lat1 === null || lon1 === null || lat2 === null || lon2 === null) {
    return null;
  }

  const R = 6371; // Earth's radius in km
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
}

function toRad(deg: number): number {
  return (deg * Math.PI) / 180;
}

/**
 * Check if coordinates represent a valid location
 * Returns false for null or (0, 0) which indicates unset location
 */
export function isValidLocation(lat: number | null, lon: number | null): boolean {
  if (lat === null || lon === null) {
    return false;
  }
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) {
    return false;
  }
  // (0, 0) is in the Atlantic Ocean - treat as unset
  if (lat === 0 && lon === 0) {
    return false;
  }
  return true;
}

/**
 * Format distance in human-readable form using the selected display unit.
 */
export function formatDistance(km: number, unit: DistanceUnit = 'imperial'): string {
  const formatInteger = (value: number) => value.toLocaleString();
  const formatOneDecimal = (value: number) =>
    value.toLocaleString(undefined, {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    });

  if (unit === 'metric') {
    if (km < 1) {
      return `${formatInteger(Math.round(km * 1000))}m`;
    }
    return `${formatOneDecimal(km)}km`;
  }

  if (unit === 'smoots') {
    const smoots = (km * 1000) / 1.7018;
    const rounded = smoots < 10 ? Number(smoots.toFixed(1)) : Math.round(smoots);
    const display = smoots < 10 ? formatOneDecimal(rounded) : formatInteger(rounded);
    return `${display} ${rounded === 1 ? 'smoot' : 'smoots'}`;
  }

  const miles = km * 0.621371;
  if (miles < 0.1) {
    return `${formatInteger(Math.round(km * 3280.839895))}ft`;
  }
  return `${formatOneDecimal(miles)}mi`;
}

/**
 * Sort contacts by distance from a reference point
 * Contacts without location are placed at the end
 */
function sortContactsByDistance(
  contacts: Contact[],
  fromLat: number | null,
  fromLon: number | null
): Contact[] {
  if (fromLat === null || fromLon === null) {
    return contacts;
  }

  return [...contacts].sort((a, b) => {
    const distA = calculateDistance(fromLat, fromLon, a.lat, a.lon);
    const distB = calculateDistance(fromLat, fromLon, b.lat, b.lon);

    // Null distances go to the end
    if (distA === null && distB === null) return 0;
    if (distA === null) return 1;
    if (distB === null) return -1;

    return distA - distB;
  });
}

/**
 * Get hop count from path, using explicit metadata when available.
 */
function getHopCount(path: string | null | undefined, hopCount?: number | null): number {
  if (hopCount != null && hopCount >= 0) {
    return hopCount;
  }
  if (!path || path.length === 0) {
    return 0;
  }
  // Legacy fallback: assume 1-byte (2 hex chars) per hop
  return Math.floor(path.length / 2);
}

/**
 * Format hop counts from multiple paths for display.
 * Returns something like "d/1/3/3" for direct, 1-hop, 3-hop, 3-hop paths.
 * Returns null if no paths or only direct.
 */
export function formatHopCounts(paths: MessagePath[] | null | undefined): {
  display: string;
  allDirect: boolean;
  hasMultiple: boolean;
} {
  if (!paths || paths.length === 0) {
    return { display: '', allDirect: true, hasMultiple: false };
  }

  // Get hop counts for all paths and sort ascending
  const hopCounts = paths.map((p) => getHopCount(p.path, p.path_len)).sort((a, b) => a - b);

  const allDirect = hopCounts.every((h) => h === 0);
  const hasMultiple = paths.length > 1;

  // Format: "d" for 0, numbers for others
  const parts = hopCounts.map((h) => (h === 0 ? 'd' : h.toString()));
  const display = parts.join('/');

  return { display, allDirect, hasMultiple };
}

/**
 * Build complete path resolution with sender, hops, and receiver
 */
export function resolvePath(
  path: string | null | undefined,
  sender: SenderInfo,
  contacts: Contact[],
  config: RadioConfig | null,
  hopCount?: number | null
): ResolvedPath {
  const hopPrefixes = parsePathHops(path, hopCount);
  const inferredPathHashMode = inferPathHashMode(path, hopCount);

  // Build sender info
  const senderPrefix = formatEndpointPrefix(
    sender.publicKeyOrPrefix,
    normalizePathHashMode(sender.pathHashMode) ?? inferredPathHashMode
  );
  const resolvedSender = {
    name: sender.name,
    prefix: senderPrefix,
    lat: sender.lat,
    lon: sender.lon,
  };

  // Build receiver info from radio config
  const receiverPrefix = formatEndpointPrefix(
    config?.public_key,
    normalizePathHashMode(config?.path_hash_mode) ?? inferredPathHashMode
  );
  const resolvedReceiver = {
    name: config?.name || 'Unknown',
    prefix: receiverPrefix,
    lat: config?.lat ?? null,
    lon: config?.lon ?? null,
    publicKey: config?.public_key ?? null,
  };

  // Build hops
  const hops: PathHop[] = [];
  let prevLat = sender.lat;
  let prevLon = sender.lon;
  // Start uncertain if sender has no valid location
  let prevHopUncertain = !isValidLocation(sender.lat, sender.lon);

  for (const prefix of hopPrefixes) {
    const matches = findContactsByPrefix(prefix, contacts, true);
    const sortedMatches = sortContactsByDistance(matches, prevLat, prevLon);

    // Calculate distance from previous hop
    // Can't calculate if previous hop was uncertain (unknown/ambiguous/no location) or current hop is unknown/invalid
    let distanceFromPrev: number | null = null;
    const currentHasValidLocation =
      sortedMatches.length === 1 && isValidLocation(sortedMatches[0].lat, sortedMatches[0].lon);
    if (!prevHopUncertain && currentHasValidLocation) {
      distanceFromPrev = calculateDistance(
        prevLat,
        prevLon,
        sortedMatches[0].lat,
        sortedMatches[0].lon
      );
    }

    hops.push({
      prefix,
      matches: sortedMatches,
      distanceFromPrev,
    });

    // Update previous location for next hop
    if (sortedMatches.length === 0) {
      // Unknown hop - can't calculate distance for next hop
      prevHopUncertain = true;
      prevLat = null;
      prevLon = null;
    } else if (sortedMatches.length > 1) {
      // Ambiguous hop - can't calculate distance for next hop (too many combinations)
      prevHopUncertain = true;
      // Use first match's location for sorting purposes, but distance won't be shown
      if (isValidLocation(sortedMatches[0].lat, sortedMatches[0].lon)) {
        prevLat = sortedMatches[0].lat;
        prevLon = sortedMatches[0].lon;
      } else {
        prevLat = null;
        prevLon = null;
      }
    } else if (isValidLocation(sortedMatches[0].lat, sortedMatches[0].lon)) {
      prevHopUncertain = false;
      prevLat = sortedMatches[0].lat;
      prevLon = sortedMatches[0].lon;
    } else {
      // Known hop but no valid location - treat as uncertain for distance purposes
      prevHopUncertain = true;
      prevLat = null;
      prevLon = null;
    }
  }

  // Calculate total distances (can be multiple if ambiguous)
  const totalDistances = calculateTotalDistances(resolvedSender, hops, resolvedReceiver);

  // Determine if path has any gaps (unknown, ambiguous, or missing location)
  const hasGaps =
    !isValidLocation(resolvedSender.lat, resolvedSender.lon) ||
    !isValidLocation(resolvedReceiver.lat, resolvedReceiver.lon) ||
    hops.some(
      (hop) => hop.matches.length !== 1 || !isValidLocation(hop.matches[0].lat, hop.matches[0].lon)
    );

  return {
    sender: resolvedSender,
    hops,
    receiver: resolvedReceiver,
    totalDistances,
    hasGaps,
  };
}

/**
 * Calculate total distance(s) for the path
 * Returns array for ambiguous paths, null if any segment can't be calculated
 * If sender has no location, starts calculating from first hop with location
 */
function calculateTotalDistances(
  sender: { lat: number | null; lon: number | null },
  hops: PathHop[],
  receiver: { lat: number | null; lon: number | null }
): number[] | null {
  // Simple case: no hops
  if (hops.length === 0) {
    if (!isValidLocation(sender.lat, sender.lon) || !isValidLocation(receiver.lat, receiver.lon)) {
      return null;
    }
    const dist = calculateDistance(sender.lat, sender.lon, receiver.lat, receiver.lon);
    return dist !== null ? [dist] : null;
  }

  // Start from sender if it has valid location, otherwise find first hop with valid location
  let prevLat = sender.lat;
  let prevLon = sender.lon;
  let startHopIndex = 0;

  if (!isValidLocation(prevLat, prevLon)) {
    // Find first hop with a known, unambiguous, valid location
    for (let i = 0; i < hops.length; i++) {
      const hop = hops[i];
      if (hop.matches.length === 1 && isValidLocation(hop.matches[0].lat, hop.matches[0].lon)) {
        prevLat = hop.matches[0].lat;
        prevLon = hop.matches[0].lon;
        startHopIndex = i + 1;
        break;
      }
    }
    // If no hop has valid location, can't calculate
    if (!isValidLocation(prevLat, prevLon)) {
      return null;
    }
  }

  // Sum up only unambiguous segments (where both endpoints are known and unambiguous)
  let totalDistance = 0;
  let hasAnyDistance = false;
  let lastUnambiguousHopIndex = -1; // Track last unambiguous hop for receiver distance

  for (let i = startHopIndex; i < hops.length; i++) {
    const hop = hops[i];

    // Skip if hop is unknown or ambiguous or has no valid location
    if (hop.matches.length !== 1 || !isValidLocation(hop.matches[0].lat, hop.matches[0].lon)) {
      // Can't include this segment - reset prevLat/prevLon for next potential segment
      prevLat = null;
      prevLon = null;
      continue;
    }

    // Only calculate distance if previous location is known (unambiguous)
    if (prevLat !== null && prevLon !== null) {
      const dist = calculateDistance(prevLat, prevLon, hop.matches[0].lat, hop.matches[0].lon);
      if (dist !== null) {
        totalDistance += dist;
        hasAnyDistance = true;
      }
    }

    // Update for next iteration
    prevLat = hop.matches[0].lat;
    prevLon = hop.matches[0].lon;
    lastUnambiguousHopIndex = i;
  }

  // Add final leg to receiver only if last hop was unambiguous and receiver has valid location
  if (lastUnambiguousHopIndex === hops.length - 1 && prevLat !== null && prevLon !== null) {
    if (isValidLocation(receiver.lat, receiver.lon)) {
      const finalDist = calculateDistance(prevLat, prevLon, receiver.lat, receiver.lon);
      if (finalDist !== null) {
        totalDistance += finalDist;
        hasAnyDistance = true;
      }
    }
  }

  // Return total if we calculated any distance
  return hasAnyDistance ? [totalDistance] : null;
}
