import { useEffect, useRef } from 'react';
import { MapContainer, TileLayer, Marker, Tooltip, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { isValidLocation } from '../utils/pathUtils';
import type { ResolvedPath, SenderInfo } from '../utils/pathUtils';

interface PathRouteMapProps {
  resolved: ResolvedPath;
  senderInfo: SenderInfo;
  height?: number;
}

// Colors for hop markers (indexed by hop number - 1)
const HOP_COLORS = [
  '#f97316', // Hop 1: orange
  '#eab308', // Hop 2: yellow
  '#22c55e', // Hop 3: green
  '#06b6d4', // Hop 4: cyan
  '#ec4899', // Hop 5: pink
  '#f43f5e', // Hop 6: rose
  '#a855f7', // Hop 7: purple
  '#64748b', // Hop 8: slate
];

const SENDER_COLOR = '#3b82f6'; // blue
const RECEIVER_COLOR = '#8b5cf6'; // violet

function makeIcon(label: string, color: string): L.DivIcon {
  return L.divIcon({
    className: '',
    iconSize: [24, 24],
    iconAnchor: [12, 12],
    html: `<div style="
      width:24px;height:24px;border-radius:50%;
      background:${color};color:#fff;
      display:flex;align-items:center;justify-content:center;
      font-size:11px;font-weight:700;
      border:2px solid rgba(255,255,255,0.8);
      box-shadow:0 1px 4px rgba(0,0,0,0.4);
    ">${label}</div>`,
  });
}

function getHopColor(hopIndex: number): string {
  return HOP_COLORS[hopIndex % HOP_COLORS.length];
}

/** Collect all valid [lat, lon] points for bounds fitting */
function collectPoints(resolved: ResolvedPath): [number, number][] {
  const pts: [number, number][] = [];
  if (isValidLocation(resolved.sender.lat, resolved.sender.lon)) {
    pts.push([resolved.sender.lat!, resolved.sender.lon!]);
  }
  for (const hop of resolved.hops) {
    for (const m of hop.matches) {
      if (isValidLocation(m.lat, m.lon)) {
        pts.push([m.lat!, m.lon!]);
      }
    }
  }
  if (isValidLocation(resolved.receiver.lat, resolved.receiver.lon)) {
    pts.push([resolved.receiver.lat!, resolved.receiver.lon!]);
  }
  return pts;
}

/** Fit map bounds once on mount, then let the user pan/zoom freely */
function RouteMapBounds({ points }: { points: [number, number][] }) {
  const map = useMap();
  const fitted = useRef(false);

  useEffect(() => {
    if (fitted.current || points.length === 0) return;
    fitted.current = true;
    if (points.length === 1) {
      map.setView(points[0], 12);
    } else {
      map.fitBounds(points as L.LatLngBoundsExpression, { padding: [30, 30], maxZoom: 14 });
    }
  }, [map, points]);

  return null;
}

export function PathRouteMap({ resolved, senderInfo, height = 220 }: PathRouteMapProps) {
  const points = collectPoints(resolved);
  const hasAnyGps = points.length > 0;

  // Check if some nodes are missing GPS
  let totalNodes = 2; // sender + receiver
  let nodesWithGps = 0;
  if (isValidLocation(resolved.sender.lat, resolved.sender.lon)) nodesWithGps++;
  if (isValidLocation(resolved.receiver.lat, resolved.receiver.lon)) nodesWithGps++;
  for (const hop of resolved.hops) {
    if (hop.matches.length === 0) {
      totalNodes++;
    } else {
      totalNodes += hop.matches.length;
      nodesWithGps += hop.matches.filter((m) => isValidLocation(m.lat, m.lon)).length;
    }
  }
  const someMissingGps = hasAnyGps && nodesWithGps < totalNodes;

  if (!hasAnyGps) {
    return (
      <div className="h-14 rounded border border-border bg-muted/30 flex items-center justify-center text-sm text-muted-foreground">
        No nodes in this route have GPS coordinates
      </div>
    );
  }

  const center: [number, number] = points[0];

  return (
    <div>
      <div
        className="rounded border border-border overflow-hidden"
        role="img"
        aria-label="Map showing message route between nodes"
        style={{ height }}
      >
        <MapContainer
          center={center}
          zoom={10}
          className="h-full w-full"
          style={{ background: '#1a1a2e' }}
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <RouteMapBounds points={points} />

          {/* Sender marker */}
          {isValidLocation(resolved.sender.lat, resolved.sender.lon) && (
            <Marker
              position={[resolved.sender.lat!, resolved.sender.lon!]}
              icon={makeIcon('S', SENDER_COLOR)}
            >
              <Tooltip direction="top" offset={[0, -14]}>
                <span className="font-mono">{resolved.sender.prefix}</span>
                {' · '}
                {senderInfo.name || 'Sender'}
              </Tooltip>
            </Marker>
          )}

          {/* Hop markers */}
          {resolved.hops.map((hop, hopIdx) =>
            hop.matches
              .filter((m) => isValidLocation(m.lat, m.lon))
              .map((m, mIdx) => (
                <Marker
                  key={`hop-${hopIdx}-${mIdx}`}
                  position={[m.lat!, m.lon!]}
                  icon={makeIcon(String(hopIdx + 1), getHopColor(hopIdx))}
                >
                  <Tooltip direction="top" offset={[0, -14]}>
                    <span className="font-mono">{hop.prefix}</span>
                    {' · '}
                    {m.name || m.public_key.slice(0, 12)}
                  </Tooltip>
                </Marker>
              ))
          )}

          {/* Receiver marker */}
          {isValidLocation(resolved.receiver.lat, resolved.receiver.lon) && (
            <Marker
              position={[resolved.receiver.lat!, resolved.receiver.lon!]}
              icon={makeIcon('R', RECEIVER_COLOR)}
            >
              <Tooltip direction="top" offset={[0, -14]}>
                <span className="font-mono">{resolved.receiver.prefix}</span>
                {' · '}
                {resolved.receiver.name || 'Receiver'}
              </Tooltip>
            </Marker>
          )}
        </MapContainer>
      </div>
      {someMissingGps && (
        <p className="text-xs text-muted-foreground mt-1">
          Some nodes in this route have no GPS and are not shown
        </p>
      )}
    </div>
  );
}
