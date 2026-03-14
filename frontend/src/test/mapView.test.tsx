import { forwardRef } from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MapView } from '../components/MapView';
import type { Contact } from '../types';

vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TileLayer: () => null,
  CircleMarker: forwardRef<
    HTMLDivElement,
    { children: React.ReactNode; pathOptions?: { fillColor?: string } }
  >(({ children, pathOptions }, ref) => (
    <div ref={ref} data-fill-color={pathOptions?.fillColor}>
      {children}
    </div>
  )),
  Popup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  useMap: () => ({
    setView: vi.fn(),
    fitBounds: vi.fn(),
  }),
}));

describe('MapView', () => {
  it('renders a never-heard fallback for a focused contact without last_seen', () => {
    const contact: Contact = {
      public_key: 'aa'.repeat(32),
      name: 'Mystery Node',
      type: 1,
      flags: 0,
      last_path: null,
      last_path_len: -1,
      out_path_hash_mode: -1,
      route_override_path: null,
      route_override_len: null,
      route_override_hash_mode: null,
      last_advert: null,
      lat: 40,
      lon: -74,
      last_seen: null,
      on_radio: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };

    render(<MapView contacts={[contact]} focusedKey={contact.public_key} />);

    expect(
      screen.getByText(/showing 1 contact heard in the last 7 days plus the focused contact/i)
    ).toBeInTheDocument();
    expect(screen.getByText('Last heard: Never heard by this server')).toBeInTheDocument();
  });
});
