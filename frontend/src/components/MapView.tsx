import { Fragment, useEffect, useState, useMemo, useRef, useCallback } from 'react';
import { MapContainer, TileLayer, CircleMarker, Popup, useMap } from 'react-leaflet';
import type { LatLngBoundsExpression, CircleMarker as LeafletCircleMarker } from 'leaflet';
import 'leaflet/dist/leaflet.css';
import type { Contact } from '../types';
import { formatTime } from '../utils/messageParser';
import { isValidLocation } from '../utils/pathUtils';
import { CONTACT_TYPE_REPEATER } from '../types';

interface MapViewProps {
  contacts: Contact[];
  /** Public key of contact to focus on and open popup */
  focusedKey?: string | null;
}

const MAP_RECENCY_COLORS = {
  recent: '#06b6d4',
  today: '#2563eb',
  stale: '#f59e0b',
  old: '#64748b',
} as const;
const MAP_MARKER_STROKE = '#0f172a';
const MAP_REPEATER_RING = '#f8fafc';

// Calculate marker color based on how recently the contact was heard
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

// Component to handle map bounds fitting
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
    // If we have a focused contact, center on it immediately (even if already initialized)
    if (focusedContact && focusedContact.lat != null && focusedContact.lon != null) {
      map.setView([focusedContact.lat, focusedContact.lon], 12);
      setHasInitialized(true);
      return;
    }

    if (hasInitialized) return;

    const fitToContacts = () => {
      if (contacts.length === 0) {
        // No contacts with location - show world view
        map.setView([20, 0], 2);
        setHasInitialized(true);
        return;
      }

      if (contacts.length === 1) {
        // Single contact - center on it
        map.setView([contacts[0].lat!, contacts[0].lon!], 10);
        setHasInitialized(true);
        return;
      }

      // Multiple contacts - fit bounds
      const bounds: LatLngBoundsExpression = contacts.map(
        (c) => [c.lat!, c.lon!] as [number, number]
      );
      map.fitBounds(bounds, { padding: [50, 50], maxZoom: 12 });
      setHasInitialized(true);
    };

    // Try geolocation first
    if ('geolocation' in navigator) {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          // Success - center on user location with reasonable zoom
          map.setView([position.coords.latitude, position.coords.longitude], 8);
          setHasInitialized(true);
        },
        () => {
          // Geolocation denied/failed - fit to contacts
          fitToContacts();
        },
        { timeout: 5000, maximumAge: 300000 }
      );
    } else {
      // No geolocation support - fit to contacts
      fitToContacts();
    }
  }, [map, contacts, hasInitialized, focusedContact]);

  return null;
}

export function MapView({ contacts, focusedKey }: MapViewProps) {
  const sevenDaysAgo = Date.now() / 1000 - 7 * 24 * 60 * 60;

  // Filter to contacts with GPS coordinates, heard within the last 7 days.
  // Always include the focused contact so "view on map" links work for older nodes.
  const mappableContacts = useMemo(() => {
    return contacts.filter(
      (c) =>
        isValidLocation(c.lat, c.lon) &&
        (c.public_key === focusedKey || (c.last_seen != null && c.last_seen > sevenDaysAgo))
    );
  }, [contacts, focusedKey, sevenDaysAgo]);

  // Find the focused contact by key
  const focusedContact = useMemo(() => {
    if (!focusedKey) return null;
    return mappableContacts.find((c) => c.public_key === focusedKey) || null;
  }, [focusedKey, mappableContacts]);

  const includesFocusedOutsideWindow =
    focusedContact != null &&
    (focusedContact.last_seen == null || focusedContact.last_seen <= sevenDaysAgo);

  // Track marker refs to open popup programmatically
  const markerRefs = useRef<Record<string, LeafletCircleMarker | null>>({});

  // Store ref for a marker
  const setMarkerRef = useCallback((key: string, ref: LeafletCircleMarker | null) => {
    markerRefs.current[key] = ref;
  }, []);

  // Open popup for focused contact after map is ready
  useEffect(() => {
    if (focusedContact && markerRefs.current[focusedContact.public_key]) {
      // Small delay to ensure map has finished rendering
      const timer = setTimeout(() => {
        markerRefs.current[focusedContact.public_key]?.openPopup();
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [focusedContact]);

  return (
    <div className="flex flex-col h-full">
      {/* Info bar */}
      <div className="px-4 py-2 bg-muted/50 text-xs text-muted-foreground flex items-center justify-between">
        <span>
          Showing {mappableContacts.length} contact{mappableContacts.length !== 1 ? 's' : ''} heard
          in the last 7 days
          {includesFocusedOutsideWindow ? ' plus the focused contact' : ''}
        </span>
        <div className="flex items-center gap-3">
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
          <span className="flex items-center gap-1">
            <span
              className="w-3 h-3 rounded-full border-2"
              style={{ borderColor: MAP_REPEATER_RING, backgroundColor: MAP_RECENCY_COLORS.today }}
              aria-hidden="true"
            />{' '}
            repeater
          </span>
        </div>
      </div>

      {/* Map - z-index constrained to stay below modals/sheets */}
      <div
        className="flex-1 relative"
        style={{ zIndex: 0 }}
        role="img"
        aria-label="Map showing mesh node locations"
      >
        <MapContainer
          center={[20, 0]}
          zoom={2}
          className="h-full w-full"
          style={{ background: '#1a1a2e' }}
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <MapBoundsHandler contacts={mappableContacts} focusedContact={focusedContact} />

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
                        {displayName}
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
        </MapContainer>
      </div>
    </div>
  );
}
