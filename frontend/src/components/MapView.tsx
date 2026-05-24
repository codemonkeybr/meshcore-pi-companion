import { Fragment, useEffect, useState, useMemo, useRef, useCallback } from 'react';
import {
  MapContainer,
  TileLayer,
  CircleMarker,
  Popup,
  useMap,
  useMapEvents,
  Polyline,
  LayersControl,
} from 'react-leaflet';
import type { LatLngBoundsExpression, CircleMarker as LeafletCircleMarker } from 'leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import type { Contact, RadioConfig, RawPacket } from '../types';
import { formatTime } from '../utils/messageParser';
import { isValidLocation } from '../utils/pathUtils';
import { CONTACT_TYPE_REPEATER } from '../types';
import {
  parsePacket,
  getPacketLabel,
  PARTICLE_COLOR_MAP,
  dedupeConsecutive,
} from '../utils/visualizerUtils';
import { getRawPacketObservationKey } from '../utils/rawPacketIdentity';

interface MapViewProps {
  contacts: Contact[];
  /** Public key of contact to focus on and open popup */
  focusedKey?: string | null;
  rawPackets?: RawPacket[];
  config?: RadioConfig | null;
  /** When provided, the contact name in each popup becomes a clickable link
   *  that opens the conversation for that contact (DM, repeater, or room). */
  onSelectContact?: (contact: Contact) => void;
}

// --- Tile layer presets ---
// Every provider here is free and works without an API key. Attribution strings
// follow each provider's requirements; do not remove them. If you add a new
// provider, verify its terms of service (especially for Esri / Google-style
// satellite tiles) before committing.
interface TileLayerPreset {
  id: string;
  label: string;
  url: string;
  attribution: string;
  background: string;
  /** Highest zoom the provider publishes tiles at. When the layer is active,
   *  the map's zoom ceiling is tightened to this value via
   *  `MaxZoomByActiveLayer` so the user cannot zoom into a grey void. */
  maxZoom?: number;
}

// Global zoom bounds for the MapContainer itself. These are pinned to the
// container so Leaflet's internal tile-range math never has to guess when
// layers swap in/out via LayersControl. Without this, an initial-mount race
// between MapContainer layout and LayersControl.BaseLayer addition has been
// observed to throw "Attempted to load an infinite number of tiles".
const MAP_MIN_ZOOM = 2;
const MAP_MAX_ZOOM = 19;

const TILE_LAYERS: readonly TileLayerPreset[] = [
  {
    id: 'light',
    label: 'Light (OpenStreetMap)',
    url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    background: '#1a1a2e',
    maxZoom: 19,
  },
  {
    id: 'dark',
    label: 'Dark (CARTO)',
    url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
    background: '#0d0d0d',
    maxZoom: 19,
  },
  {
    id: 'topographic',
    label: 'Topographic (OpenTopoMap)',
    url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    attribution:
      'Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, <a href="http://viewfinderpanoramas.org">SRTM</a> | Map style: &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>)',
    background: '#a3b3bc',
    maxZoom: 17,
  },
  {
    id: 'satellite',
    label: 'Satellite (Esri)',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution:
      'Tiles &copy; <a href="https://www.esri.com/">Esri</a> &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community',
    background: '#1a1f2e',
    // Esri's tile service advertises LODs up to 23 and returns HTTP 200 for
    // every tile request, but the underlying imagery is only high-resolution
    // up to ~18 in most developed areas and shallower in rural regions. We
    // cap at 18 rather than 19 so users don't zoom into visibly-empty or
    // severely-upscaled tiles. Remote regions may still be sparse at 18.
    maxZoom: 18,
  },
] as const;

const MAP_LAYER_STORAGE_KEY = 'remoteterm-map-layer';
const LEGACY_DARK_MAP_STORAGE_KEY = 'remoteterm-dark-map';

function getSavedLayerId(): string {
  try {
    const stored = localStorage.getItem(MAP_LAYER_STORAGE_KEY);
    if (stored && TILE_LAYERS.some((l) => l.id === stored)) return stored;
    // Legacy migration: boolean dark-map flag predates multi-layer support.
    const legacyDark = localStorage.getItem(LEGACY_DARK_MAP_STORAGE_KEY) === 'true';
    return legacyDark ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

/**
 * Leaflet-internal companion component: listens for base-layer changes driven
 * by Leaflet's own LayersControl UI and pipes the selection back to React.
 * Kept separate so the persistence/state logic stays out of the render tree.
 */
function LayerChangeWatcher({ onChange }: { onChange: (name: string) => void }) {
  useMapEvents({
    baselayerchange: (event) => {
      if (event.name) onChange(event.name);
    },
  });
  return null;
}

/**
 * Enforces the active layer's zoom ceiling on the underlying Leaflet map.
 *
 * Leaflet's `map.getMaxZoom()` prefers `options.maxZoom` (set on MapContainer)
 * over per-layer `maxZoom`, so a per-TileLayer cap is silently ignored unless
 * we push it down to the map itself. We do that here whenever the active
 * layer changes, and clamp the current zoom if the user happened to be zoomed
 * past the new cap at the moment of the switch.
 *
 * The MapContainer's fixed `minZoom`/`maxZoom` remain the absolute hull that
 * prevents the "Attempted to load an infinite number of tiles" race during
 * initial mount (see `MAP_MIN_ZOOM`/`MAP_MAX_ZOOM` below).
 */
function MaxZoomByActiveLayer({ maxZoom }: { maxZoom: number }) {
  const map = useMap();
  useEffect(() => {
    map.setMaxZoom(maxZoom);
    if (map.getZoom() > maxZoom) {
      map.setZoom(maxZoom);
    }
  }, [map, maxZoom]);
  return null;
}

const MAP_RECENCY_COLORS = {
  recent: '#06b6d4',
  today: '#2563eb',
  stale: '#f59e0b',
  old: '#64748b',
} as const;
const MAP_MARKER_STROKE = '#0f172a';
const MAP_REPEATER_RING = '#f8fafc';

// --- Packet visualization constants ---
const THREE_DAYS_SEC = 3 * 24 * 60 * 60;
const PARTICLE_LIFETIME_MS = 3000;
const PARTICLE_TAIL_LENGTH = 0.25; // fraction of progress to trail behind
const PARTICLE_RADIUS = 8;
const PARTICLE_TAIL_WIDTH = 5;
const MAX_MAP_PARTICLES = 200;

// --- Helpers ---

function getMarkerColor(lastSeen: number | null | undefined): string {
  if (lastSeen == null) return MAP_RECENCY_COLORS.old;
  const now = Date.now() / 1000;
  const age = now - lastSeen;
  const hour = 3600;
  const day = 86400;

  if (age < hour) return MAP_RECENCY_COLORS.recent;
  if (age < day) return MAP_RECENCY_COLORS.today;
  if (age < 3 * day) return MAP_RECENCY_COLORS.stale;
  return MAP_RECENCY_COLORS.old;
}

/** Resolve a hop token to a single contact with GPS, or null. */
function resolveHopToGps(hopToken: string, prefixIndex: Map<string, Contact[]>): Contact | null {
  const matches = prefixIndex.get(hopToken.toLowerCase());
  if (!matches || matches.length !== 1) return null;
  const c = matches[0];
  return isValidLocation(c.lat, c.lon) ? c : null;
}

/** Resolve a contact by display name (for GroupText senders). */
function resolveNameToGps(name: string, nameIndex: Map<string, Contact>): Contact | null {
  const c = nameIndex.get(name);
  if (!c) return null;
  return isValidLocation(c.lat, c.lon) ? c : null;
}

/** Collect public keys of all unambiguously resolved GPS-bearing contacts from a parsed packet. */
function resolvePacketContacts(
  parsed: ReturnType<typeof parsePacket>,
  prefixIndex: Map<string, Contact[]>,
  nameIndex: Map<string, Contact>,
  myLatLon: [number, number] | null,
  config?: RadioConfig | null
): Set<string> {
  const keys = new Set<string>();
  if (!parsed) return keys;

  // Source by pubkey prefix
  const sourcePrefixes = parsed.advertPubkey
    ? [parsed.advertPubkey.slice(0, 12).toLowerCase()]
    : parsed.srcHash
      ? [parsed.srcHash.toLowerCase()]
      : [];
  for (const prefix of sourcePrefixes) {
    const matches = prefixIndex.get(prefix);
    if (matches?.length === 1 && isValidLocation(matches[0].lat, matches[0].lon)) {
      keys.add(matches[0].public_key);
    }
  }

  // Source by name (GroupText sender)
  if (parsed.groupTextSender) {
    const c = resolveNameToGps(parsed.groupTextSender, nameIndex);
    if (c) keys.add(c.public_key);
  }

  // Intermediate hops
  for (const hop of parsed.pathBytes) {
    if (hop.length < 4) continue;
    const matches = prefixIndex.get(hop.toLowerCase());
    if (matches?.length === 1 && isValidLocation(matches[0].lat, matches[0].lon)) {
      keys.add(matches[0].public_key);
    }
  }

  // Self
  if (myLatLon && config?.public_key) {
    keys.add(config.public_key.toLowerCase());
  }

  // Destination
  if (parsed.dstHash) {
    const matches = prefixIndex.get(parsed.dstHash.toLowerCase());
    if (matches?.length === 1 && isValidLocation(matches[0].lat, matches[0].lon)) {
      keys.add(matches[0].public_key);
    }
  }

  return keys;
}

interface MapParticle {
  id: number;
  path: [number, number][]; // lat/lon waypoints
  color: string;
  startedAt: number;
}

// --- Map bounds handler ---

function MapBoundsHandler({
  contacts,
  focusedContact,
}: {
  contacts: Contact[];
  focusedContact: Contact | null;
}) {
  const map = useMap();
  const [hasInitialized, setHasInitialized] = useState(false);

  useEffect(() => {
    if (focusedContact && focusedContact.lat != null && focusedContact.lon != null) {
      map.setView([focusedContact.lat, focusedContact.lon], 12);
      setHasInitialized(true);
      return;
    }

    if (hasInitialized) return;

    const fitToContacts = () => {
      if (contacts.length === 0) {
        map.setView([20, 0], 2);
        setHasInitialized(true);
        return;
      }

      if (contacts.length === 1) {
        map.setView([contacts[0].lat!, contacts[0].lon!], 10);
        setHasInitialized(true);
        return;
      }

      const bounds: LatLngBoundsExpression = contacts.map(
        (c) => [c.lat!, c.lon!] as [number, number]
      );
      map.fitBounds(bounds, { padding: [50, 50], maxZoom: 12 });
      setHasInitialized(true);
    };

    if ('geolocation' in navigator) {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          map.setView([position.coords.latitude, position.coords.longitude], 8);
          setHasInitialized(true);
        },
        () => {
          fitToContacts();
        },
        { timeout: 5000, maximumAge: 300000 }
      );
    } else {
      fitToContacts();
    }
  }, [map, contacts, hasInitialized, focusedContact]);

  return null;
}

// --- Canvas particle overlay ---

function ParticleOverlay({ particles }: { particles: MapParticle[] }) {
  const map = useMap();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const animRef = useRef<number>(0);

  useEffect(() => {
    const container = map.getContainer();
    const canvas = document.createElement('canvas');
    canvas.style.position = 'absolute';
    canvas.style.top = '0';
    canvas.style.left = '0';
    canvas.style.pointerEvents = 'none';
    canvas.style.zIndex = '450'; // above tiles, below popups
    container.appendChild(canvas);
    canvasRef.current = canvas;

    const resize = () => {
      const size = map.getSize();
      canvas.width = size.x * window.devicePixelRatio;
      canvas.height = size.y * window.devicePixelRatio;
      canvas.style.width = `${size.x}px`;
      canvas.style.height = `${size.y}px`;
    };
    resize();
    map.on('resize', resize);
    map.on('zoom', resize);

    return () => {
      cancelAnimationFrame(animRef.current);
      map.off('resize', resize);
      map.off('zoom', resize);
      container.removeChild(canvas);
      canvasRef.current = null;
    };
  }, [map]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const draw = () => {
      const now = Date.now();
      const dpr = window.devicePixelRatio;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.save();
      ctx.scale(dpr, dpr);

      for (const particle of particles) {
        const elapsed = now - particle.startedAt;
        if (elapsed < 0 || elapsed > PARTICLE_LIFETIME_MS) continue;
        const progress = elapsed / PARTICLE_LIFETIME_MS;
        const path = particle.path;
        if (path.length < 2) continue;

        // Calculate total path length in pixels for even speed
        const pixelPath = path.map((ll) => map.latLngToContainerPoint(L.latLng(ll[0], ll[1])));
        const segLengths: number[] = [];
        let totalLen = 0;
        for (let i = 1; i < pixelPath.length; i++) {
          const dx = pixelPath[i].x - pixelPath[i - 1].x;
          const dy = pixelPath[i].y - pixelPath[i - 1].y;
          const len = Math.sqrt(dx * dx + dy * dy);
          segLengths.push(len);
          totalLen += len;
        }
        if (totalLen === 0) continue;

        // Interpolate head position
        const headDist = progress * totalLen;
        const tailDist = Math.max(0, headDist - PARTICLE_TAIL_LENGTH * totalLen);

        const pointAtDist = (d: number): { x: number; y: number } => {
          let accum = 0;
          for (let i = 0; i < segLengths.length; i++) {
            if (accum + segLengths[i] >= d) {
              const t = segLengths[i] > 0 ? (d - accum) / segLengths[i] : 0;
              return {
                x: pixelPath[i].x + (pixelPath[i + 1].x - pixelPath[i].x) * t,
                y: pixelPath[i].y + (pixelPath[i + 1].y - pixelPath[i].y) * t,
              };
            }
            accum += segLengths[i];
          }
          const last = pixelPath[pixelPath.length - 1];
          return { x: last.x, y: last.y };
        };

        const head = pointAtDist(headDist);
        const tail = pointAtDist(tailDist);

        // Draw tail as a gradient line from transparent to opaque
        const grad = ctx.createLinearGradient(tail.x, tail.y, head.x, head.y);
        grad.addColorStop(0, particle.color + '00');
        grad.addColorStop(1, particle.color + 'cc');
        ctx.beginPath();
        ctx.moveTo(tail.x, tail.y);

        // Sample intermediate points along the tail for curved paths
        const steps = 8;
        for (let s = 1; s <= steps; s++) {
          const d = tailDist + ((headDist - tailDist) * s) / steps;
          const pt = pointAtDist(d);
          ctx.lineTo(pt.x, pt.y);
        }
        ctx.strokeStyle = grad;
        ctx.lineWidth = PARTICLE_TAIL_WIDTH;
        ctx.lineCap = 'round';
        ctx.stroke();

        // Draw head blob with glow
        const fade = progress > 0.8 ? 1 - (progress - 0.8) / 0.2 : 1;
        const alpha = Math.round(fade * 230)
          .toString(16)
          .padStart(2, '0');
        // Outer glow
        ctx.beginPath();
        ctx.arc(head.x, head.y, PARTICLE_RADIUS + 4, 0, Math.PI * 2);
        ctx.fillStyle =
          particle.color +
          Math.round(fade * 40)
            .toString(16)
            .padStart(2, '0');
        ctx.fill();
        // Core blob
        ctx.beginPath();
        ctx.arc(head.x, head.y, PARTICLE_RADIUS, 0, Math.PI * 2);
        ctx.fillStyle = particle.color + alpha;
        ctx.shadowColor = particle.color;
        ctx.shadowBlur = 12 * fade;
        ctx.fill();
        ctx.shadowBlur = 0;
        // Bright center
        ctx.beginPath();
        ctx.arc(head.x, head.y, PARTICLE_RADIUS * 0.4, 0, Math.PI * 2);
        ctx.fillStyle = '#ffffff' + alpha;
        ctx.fill();
      }

      ctx.restore();
      animRef.current = requestAnimationFrame(draw);
    };

    animRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(animRef.current);
  }, [map, particles]);

  // Redraw on map move/zoom
  useEffect(() => {
    const redraw = () => {}; // Animation loop already redraws every frame
    map.on('move', redraw);
    map.on('zoom', redraw);
    return () => {
      map.off('move', redraw);
      map.off('zoom', redraw);
    };
  }, [map]);

  return null;
}

// --- Main component ---

export function MapView({
  contacts,
  focusedKey,
  rawPackets,
  config,
  onSelectContact,
}: MapViewProps) {
  const [sevenDaysAgo] = useState(() => Date.now() / 1000 - 7 * 24 * 60 * 60);
  const [selectedLayerId, setSelectedLayerId] = useState<string>(getSavedLayerId);
  const activeLayer = TILE_LAYERS.find((l) => l.id === selectedLayerId) ?? TILE_LAYERS[0];

  // Sync layer selection across tabs and windows.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== MAP_LAYER_STORAGE_KEY) return;
      const next = e.newValue ?? '';
      if (TILE_LAYERS.some((l) => l.id === next)) {
        setSelectedLayerId(next);
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const handleLayerChange = useCallback((layerName: string) => {
    const match = TILE_LAYERS.find((l) => l.label === layerName);
    if (!match) return;
    setSelectedLayerId(match.id);
    try {
      localStorage.setItem(MAP_LAYER_STORAGE_KEY, match.id);
      // Clear the legacy key so a future downgrade-rollback doesn't revert us.
      localStorage.removeItem(LEGACY_DARK_MAP_STORAGE_KEY);
    } catch {
      // localStorage may be disabled; selection stays in memory only.
    }
  }, []);

  const [showPackets, setShowPackets] = useState(false);
  const [discoveryMode, setDiscoveryMode] = useState(false);
  const [discoveredKeys, setDiscoveredKeys] = useState<Set<string>>(new Set());
  const [particles, setParticles] = useState<MapParticle[]>([]);
  const particleIdRef = useRef(0);
  const seenObservationsRef = useRef(new Set<string>());

  // Build prefix index and name index for hop resolution
  const { prefixIndex, nameIndex } = useMemo(() => {
    const prefix = new Map<string, Contact[]>();
    const name = new Map<string, Contact>();
    for (const c of contacts) {
      const pubkey = c.public_key.toLowerCase();
      for (let len = 1; len <= 12 && len <= pubkey.length; len++) {
        const p = pubkey.slice(0, len);
        const arr = prefix.get(p);
        if (arr) arr.push(c);
        else prefix.set(p, [c]);
      }
      if (c.name && !name.has(c.name)) name.set(c.name, c);
    }
    return { prefixIndex: prefix, nameIndex: name };
  }, [contacts]);

  // Self GPS
  const myLatLon = useMemo<[number, number] | null>(() => {
    if (!config || !isValidLocation(config.lat, config.lon)) return null;
    return [config.lat, config.lon];
  }, [config]);

  // Determine time window for packet visualization
  const threeDaysAgoSec = useMemo(() => Date.now() / 1000 - THREE_DAYS_SEC, []);

  // Filter contacts for map display
  const mappableContacts = useMemo(() => {
    if (showPackets && discoveryMode) {
      // Discovery mode: only show nodes that have appeared in resolved packets
      return contacts.filter(
        (c) => isValidLocation(c.lat, c.lon) && discoveredKeys.has(c.public_key)
      );
    }
    if (showPackets) {
      // Packet mode: show only last 3 days
      return contacts.filter(
        (c) =>
          isValidLocation(c.lat, c.lon) &&
          (c.public_key === focusedKey || (c.last_seen != null && c.last_seen > threeDaysAgoSec))
      );
    }
    return contacts.filter(
      (c) =>
        isValidLocation(c.lat, c.lon) &&
        (c.public_key === focusedKey || (c.last_seen != null && c.last_seen > sevenDaysAgo))
    );
  }, [
    contacts,
    focusedKey,
    sevenDaysAgo,
    threeDaysAgoSec,
    showPackets,
    discoveryMode,
    discoveredKeys,
  ]);

  // Resolve a path of hop tokens to geographic waypoints (only unambiguous + has GPS)
  const resolvePacketPath = useCallback(
    (parsed: ReturnType<typeof parsePacket>): [number, number][] | null => {
      if (!parsed) return null;

      const waypoints: [number, number][] = [];

      // Source: advertPubkey, srcHash, or groupTextSender resolved by name
      let sourceContact: Contact | null = null;
      if (parsed.advertPubkey) {
        const prefix = parsed.advertPubkey.slice(0, 12).toLowerCase();
        const matches = prefixIndex.get(prefix);
        if (matches?.length === 1 && isValidLocation(matches[0].lat, matches[0].lon)) {
          sourceContact = matches[0];
        }
      } else if (parsed.srcHash) {
        sourceContact = resolveHopToGps(parsed.srcHash, prefixIndex);
      } else if (parsed.groupTextSender) {
        sourceContact = resolveNameToGps(parsed.groupTextSender, nameIndex);
      }

      if (sourceContact) {
        waypoints.push([sourceContact.lat!, sourceContact.lon!]);
      }

      // Intermediate hops (path bytes)
      for (const hop of parsed.pathBytes) {
        // Only resolve 2+ byte hops (4+ hex chars) to avoid ambiguous 1-byte hops
        if (hop.length < 4) continue;
        const contact = resolveHopToGps(hop, prefixIndex);
        if (contact) {
          waypoints.push([contact.lat!, contact.lon!]);
        }
      }

      // Destination: self (our radio), or dstHash
      if (myLatLon) {
        waypoints.push(myLatLon);
      } else if (parsed.dstHash) {
        const dest = resolveHopToGps(parsed.dstHash, prefixIndex);
        if (dest) {
          waypoints.push([dest.lat!, dest.lon!]);
        }
      }

      // Dedupe consecutive identical waypoints
      const deduped = dedupeConsecutive(waypoints.map((w) => `${w[0]},${w[1]}`));
      if (deduped.length < 2) return null;

      return deduped.map((s) => {
        const [lat, lon] = s.split(',').map(Number);
        return [lat, lon] as [number, number];
      });
    },
    [prefixIndex, nameIndex, myLatLon]
  );

  // Process new packets into particles and track discovered contacts
  useEffect(() => {
    if (!showPackets || !rawPackets?.length) return;

    const now = Date.now();
    const newParticles: MapParticle[] = [];
    const newDiscovered = new Set<string>();

    for (const pkt of rawPackets) {
      // Skip old packets
      if (pkt.timestamp < threeDaysAgoSec) continue;

      // Deduplicate by observation
      const obsKey = getRawPacketObservationKey(pkt);
      if (seenObservationsRef.current.has(obsKey)) continue;

      const parsed = parsePacket(pkt.data);
      if (!parsed) continue;

      // Discover contacts from this packet regardless of whether a full path resolves
      const resolvedContacts = resolvePacketContacts(
        parsed,
        prefixIndex,
        nameIndex,
        myLatLon,
        config
      );
      const path = resolvePacketPath(parsed);

      // Only mark as seen if we got something useful; otherwise a later run
      // with updated contacts/config can retry this observation.
      if (resolvedContacts.size === 0 && !path) continue;
      seenObservationsRef.current.add(obsKey);

      for (const key of resolvedContacts) newDiscovered.add(key);

      if (path) {
        newParticles.push({
          id: particleIdRef.current++,
          path,
          color: PARTICLE_COLOR_MAP[getPacketLabel(parsed.payloadType)],
          startedAt: now,
        });
      }
    }

    if (newDiscovered.size > 0) {
      setDiscoveredKeys((prev) => {
        const next = new Set(prev);
        for (const k of newDiscovered) next.add(k);
        return next.size !== prev.size ? next : prev;
      });
    }

    if (newParticles.length === 0) return;

    setParticles((prev) => {
      const combined = [...prev, ...newParticles];
      // Prune expired and cap total
      const alive = combined.filter((p) => now - p.startedAt < PARTICLE_LIFETIME_MS);
      return alive.slice(-MAX_MAP_PARTICLES);
    });
  }, [
    rawPackets,
    showPackets,
    resolvePacketPath,
    threeDaysAgoSec,
    prefixIndex,
    nameIndex,
    myLatLon,
    config,
  ]);

  // Prune expired particles periodically
  useEffect(() => {
    if (!showPackets) return;
    const interval = setInterval(() => {
      const now = Date.now();
      setParticles((prev) => prev.filter((p) => now - p.startedAt < PARTICLE_LIFETIME_MS));
    }, 1000);
    return () => clearInterval(interval);
  }, [showPackets]);

  // Reset discovered set when exiting discovery mode
  useEffect(() => {
    if (!discoveryMode) setDiscoveredKeys(new Set());
  }, [discoveryMode]);

  // Clear state when toggling off
  useEffect(() => {
    if (!showPackets) {
      setParticles([]);
      setDiscoveredKeys(new Set());
      setDiscoveryMode(false);
      seenObservationsRef.current.clear();
    }
  }, [showPackets]);

  // Find the focused contact by key
  const focusedContact = useMemo(() => {
    if (!focusedKey) return null;
    return mappableContacts.find((c) => c.public_key === focusedKey) || null;
  }, [focusedKey, mappableContacts]);

  const includesFocusedOutsideWindow =
    focusedContact != null &&
    (focusedContact.last_seen == null ||
      focusedContact.last_seen <= (showPackets ? threeDaysAgoSec : sevenDaysAgo));

  // Track marker refs to open popup programmatically
  const markerRefs = useRef<Record<string, LeafletCircleMarker | null>>({});

  const setMarkerRef = useCallback((key: string, ref: LeafletCircleMarker | null) => {
    if (ref === null) {
      delete markerRefs.current[key];
      return;
    }
    markerRefs.current[key] = ref;
  }, []);

  useEffect(() => {
    const currentKeys = new Set(mappableContacts.map((contact) => contact.public_key));
    for (const key of Object.keys(markerRefs.current)) {
      if (!currentKeys.has(key)) {
        delete markerRefs.current[key];
      }
    }
  }, [mappableContacts]);

  useEffect(() => {
    if (focusedContact && markerRefs.current[focusedContact.public_key]) {
      const timer = setTimeout(() => {
        markerRefs.current[focusedContact.public_key]?.openPopup();
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [focusedContact]);

  // Gather unique link paths for static route lines when packet viz is on
  const routeLines = useMemo(() => {
    if (!showPackets) return [];
    const seen = new Set<string>();
    const lines: { path: [number, number][]; color: string }[] = [];
    for (const p of particles) {
      const key = p.path.map((w) => `${w[0]},${w[1]}`).join('|');
      if (seen.has(key)) continue;
      seen.add(key);
      lines.push({ path: p.path, color: p.color });
    }
    return lines;
  }, [showPackets, particles]);

  const timeWindowLabel = showPackets ? '3 days' : '7 days';
  const infoLabel =
    showPackets && discoveryMode
      ? `${mappableContacts.length} node${mappableContacts.length !== 1 ? 's' : ''} discovered from live traffic`
      : `Showing ${mappableContacts.length} contact${mappableContacts.length !== 1 ? 's' : ''} heard in the last ${timeWindowLabel}${includesFocusedOutsideWindow ? ' plus the focused contact' : ''}`;

  return (
    <div className="flex flex-col h-full">
      {/* Info bar: stacks vertically on narrow viewports (info label, legend
          row, controls row) so nothing truncates; flattens to a single row
          with right-aligned cluster at md and up. */}
      <div className="px-4 py-2 bg-muted/50 text-xs text-muted-foreground flex flex-col gap-1 md:flex-row md:items-center md:justify-between md:gap-3">
        <span>{infoLabel}</span>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 md:justify-end">
          {!showPackets && (
            <>
              <span className="flex items-center gap-1">
                <span
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: MAP_RECENCY_COLORS.recent }}
                  aria-hidden="true"
                />{' '}
                &lt;1h
              </span>
              <span className="flex items-center gap-1">
                <span
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: MAP_RECENCY_COLORS.today }}
                  aria-hidden="true"
                />{' '}
                &lt;1d
              </span>
              <span className="flex items-center gap-1">
                <span
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: MAP_RECENCY_COLORS.stale }}
                  aria-hidden="true"
                />{' '}
                &lt;3d
              </span>
              <span className="flex items-center gap-1">
                <span
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: MAP_RECENCY_COLORS.old }}
                  aria-hidden="true"
                />{' '}
                older
              </span>
            </>
          )}
          {showPackets && (
            <>
              <span className="flex items-center gap-1">
                <span
                  className="w-2 h-2 rounded-full"
                  style={{ backgroundColor: PARTICLE_COLOR_MAP['AD'] }}
                  aria-hidden="true"
                />
                Ad
              </span>
              <span className="flex items-center gap-1">
                <span
                  className="w-2 h-2 rounded-full"
                  style={{ backgroundColor: PARTICLE_COLOR_MAP['GT'] }}
                  aria-hidden="true"
                />
                Ch
              </span>
              <span className="flex items-center gap-1">
                <span
                  className="w-2 h-2 rounded-full"
                  style={{ backgroundColor: PARTICLE_COLOR_MAP['DM'] }}
                  aria-hidden="true"
                />
                DM
              </span>
              <span className="flex items-center gap-1">
                <span
                  className="w-2 h-2 rounded-full"
                  style={{ backgroundColor: PARTICLE_COLOR_MAP['ACK'] }}
                  aria-hidden="true"
                />
                ACK
              </span>
            </>
          )}
          <span className="flex items-center gap-1">
            <span
              className="w-3 h-3 rounded-full border-2"
              style={{ borderColor: MAP_REPEATER_RING, backgroundColor: MAP_RECENCY_COLORS.today }}
              aria-hidden="true"
            />{' '}
            repeater
          </span>
          <label className="flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={showPackets}
              onChange={(e) => setShowPackets(e.target.checked)}
              className="rounded border-border"
            />
            <span className="text-[0.6875rem]">Visualize packets</span>
          </label>
          {showPackets && (
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={discoveryMode}
                onChange={(e) => setDiscoveryMode(e.target.checked)}
                className="rounded border-border"
              />
              <span className="text-[0.6875rem]">Discover nodes</span>
            </label>
          )}
        </div>
      </div>

      {/* Map */}
      <div
        className="flex-1 relative"
        style={{ zIndex: 0 }}
        role="img"
        aria-label="Map showing mesh node locations"
      >
        <MapContainer
          center={[20, 0]}
          zoom={2}
          minZoom={MAP_MIN_ZOOM}
          maxZoom={MAP_MAX_ZOOM}
          className="h-full w-full"
          style={{ background: activeLayer.background }}
        >
          <LayersControl position="topright" collapsed={false}>
            {TILE_LAYERS.map((layer) => (
              <LayersControl.BaseLayer
                key={layer.id}
                name={layer.label}
                checked={layer.id === selectedLayerId}
              >
                <TileLayer
                  url={layer.url}
                  attribution={layer.attribution}
                  maxZoom={layer.maxZoom}
                />
              </LayersControl.BaseLayer>
            ))}
          </LayersControl>
          <LayerChangeWatcher onChange={handleLayerChange} />
          <MaxZoomByActiveLayer maxZoom={activeLayer.maxZoom ?? MAP_MAX_ZOOM} />
          <MapBoundsHandler contacts={mappableContacts} focusedContact={focusedContact} />

          {/* Faint route lines for active packet paths */}
          {showPackets &&
            routeLines.map((line, i) => (
              <Polyline
                key={i}
                positions={line.path}
                pathOptions={{ color: line.color, weight: 1, opacity: 0.15, dashArray: '4 6' }}
              />
            ))}

          {mappableContacts.map((contact) => {
            const isRepeater = contact.type === CONTACT_TYPE_REPEATER;
            const color = getMarkerColor(contact.last_seen);
            const displayName = contact.name || contact.public_key.slice(0, 12);
            const lastHeardLabel =
              contact.last_seen != null
                ? formatTime(contact.last_seen)
                : 'Never heard by this server';
            const radius = isRepeater ? 10 : 7;

            return (
              <Fragment key={contact.public_key}>
                <CircleMarker
                  key={contact.public_key}
                  ref={(ref) => setMarkerRef(contact.public_key, ref)}
                  center={[contact.lat!, contact.lon!]}
                  radius={radius}
                  pathOptions={{
                    color: isRepeater ? MAP_REPEATER_RING : MAP_MARKER_STROKE,
                    fillColor: color,
                    fillOpacity: 0.9,
                    weight: isRepeater ? 3 : 2,
                  }}
                >
                  <Popup>
                    <div className="text-sm">
                      <div className="font-medium flex items-center gap-1">
                        {isRepeater && (
                          <span title="Repeater" aria-hidden="true">
                            🛜
                          </span>
                        )}
                        {onSelectContact ? (
                          <button
                            type="button"
                            className="p-0 bg-transparent border-0 font-inherit text-primary underline hover:text-primary/80 cursor-pointer"
                            onClick={(event) => {
                              event.stopPropagation();
                              onSelectContact(contact);
                            }}
                            title={`Open conversation with ${displayName}`}
                          >
                            {displayName}
                          </button>
                        ) : (
                          displayName
                        )}
                      </div>
                      <div className="text-xs text-gray-500 mt-1">Last heard: {lastHeardLabel}</div>
                      <div className="text-xs text-gray-400 mt-1 font-mono">
                        {contact.lat!.toFixed(5)}, {contact.lon!.toFixed(5)}
                      </div>
                    </div>
                  </Popup>
                </CircleMarker>
              </Fragment>
            );
          })}

          {showPackets && <ParticleOverlay particles={particles} />}
        </MapContainer>
      </div>
    </div>
  );
}
