import { MapContainer, TileLayer, CircleMarker, Popup, Polyline } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

interface Neighbor {
  lat: number | null;
  lon: number | null;
  name: string | null;
  pubkey_prefix: string;
  snr: number;
}

interface Props {
  neighbors: Neighbor[];
  radioLat?: number | null;
  radioLon?: number | null;
  radioName?: string | null;
}

export function NeighborsMiniMap({ neighbors, radioLat, radioLon, radioName }: Props) {
  const valid = neighbors.filter(
    (n): n is Neighbor & { lat: number; lon: number } => n.lat != null && n.lon != null
  );

  const hasRadio = radioLat != null && radioLon != null && !(radioLat === 0 && radioLon === 0);

  if (valid.length === 0 && !hasRadio) return null;

  // Center on radio if available, otherwise first neighbor
  const center: [number, number] = hasRadio ? [radioLat!, radioLon!] : [valid[0].lat, valid[0].lon];

  return (
    <div className="min-h-48 flex-1 rounded border border-border overflow-hidden">
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
        {/* Dotted lines from radio to each neighbor */}
        {hasRadio &&
          valid.map((n, i) => (
            <Polyline
              key={`line-${i}`}
              positions={[
                [radioLat!, radioLon!],
                [n.lat, n.lon],
              ]}
              pathOptions={{
                color: '#3b82f6',
                weight: 1.5,
                opacity: 0.5,
                dashArray: '6 4',
              }}
            />
          ))}
        {/* Radio node (bright blue) */}
        {hasRadio && (
          <CircleMarker
            center={[radioLat!, radioLon!]}
            radius={8}
            pathOptions={{
              color: '#1d4ed8',
              fillColor: '#3b82f6',
              fillOpacity: 1,
              weight: 2,
            }}
          >
            <Popup>
              <span className="text-sm font-medium">{radioName || 'Our Radio'}</span>
            </Popup>
          </CircleMarker>
        )}
        {/* Neighbor nodes (SNR-colored) */}
        {valid.map((n, i) => (
          <CircleMarker
            key={i}
            center={[n.lat, n.lon]}
            radius={6}
            pathOptions={{
              color: '#000',
              fillColor: n.snr >= 6 ? '#22c55e' : n.snr >= 0 ? '#eab308' : '#ef4444',
              fillOpacity: 0.8,
              weight: 1,
            }}
          >
            <Popup>
              <span className="text-sm">{n.name || n.pubkey_prefix}</span>
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  );
}
