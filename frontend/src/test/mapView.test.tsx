import { forwardRef } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MapView } from '../components/MapView';
import type { Contact } from '../types';

vi.mock('react-leaflet', () => {
  const BaseLayer = ({
    children,
  }: {
    children: React.ReactNode;
    name: string;
    checked?: boolean;
  }) => <div>{children}</div>;
  const LayersControlMock = ({ children }: { children: React.ReactNode }) => <div>{children}</div>;
  (LayersControlMock as unknown as { BaseLayer: typeof BaseLayer }).BaseLayer = BaseLayer;
  return {
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
    Polyline: () => null,
    LayersControl: LayersControlMock,
    useMap: () => ({
      setView: vi.fn(),
      fitBounds: vi.fn(),
      setMaxZoom: vi.fn(),
      setZoom: vi.fn(),
      getZoom: vi.fn(() => 2),
    }),
    useMapEvents: () => null,
  };
});

describe('MapView', () => {
  it('renders a never-heard fallback for a focused contact without last_seen', () => {
    const contact: Contact = {
      public_key: 'aa'.repeat(32),
      name: 'Mystery Node',
      type: 1,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      route_override_path: null,
      route_override_len: null,
      route_override_hash_mode: null,
      last_advert: null,
      lat: 40,
      lon: -74,
      last_seen: null,
      on_radio: false,
      favorite: false,
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

  it('invokes onSelectContact when the popup name is clicked', () => {
    const contact: Contact = {
      public_key: 'cc'.repeat(32),
      name: 'Clickable',
      type: 1,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      route_override_path: null,
      route_override_len: null,
      route_override_hash_mode: null,
      last_advert: null,
      lat: 42,
      lon: -72,
      last_seen: Math.floor(Date.now() / 1000),
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };
    const onSelectContact = vi.fn();

    render(<MapView contacts={[contact]} onSelectContact={onSelectContact} />);

    const link = screen.getByRole('button', { name: 'Clickable' });
    expect(link).toHaveAttribute('title', 'Open conversation with Clickable');
    fireEvent.click(link);

    expect(onSelectContact).toHaveBeenCalledWith(contact);
  });

  it('renders the popup name as plain text when no onSelectContact is provided', () => {
    const contact: Contact = {
      public_key: 'dd'.repeat(32),
      name: 'Static',
      type: 1,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      route_override_path: null,
      route_override_len: null,
      route_override_hash_mode: null,
      last_advert: null,
      lat: 42,
      lon: -72,
      last_seen: Math.floor(Date.now() / 1000),
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };

    render(<MapView contacts={[contact]} />);

    expect(screen.queryByRole('button', { name: /open conversation with static/i })).toBeNull();
    expect(screen.getByText('Static')).toBeInTheDocument();
  });

  it('keeps the relative cutoff stable across re-renders that do not advance the clock', () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-03-15T12:00:00Z'));

      const contact: Contact = {
        public_key: 'bb'.repeat(32),
        name: 'Almost Stale',
        type: 1,
        flags: 0,
        direct_path: null,
        direct_path_len: -1,
        direct_path_hash_mode: -1,
        route_override_path: null,
        route_override_len: null,
        route_override_hash_mode: null,
        last_advert: null,
        lat: 41,
        lon: -73,
        last_seen: Math.floor(Date.now() / 1000) - 7 * 24 * 60 * 60 + 60,
        on_radio: false,
        favorite: false,
        last_contacted: null,
        last_read_at: null,
        first_seen: null,
      };

      const { rerender } = render(<MapView contacts={[contact]} focusedKey={null} />);

      expect(screen.getByText(/showing 1 contact heard in the last 7 days/i)).toBeInTheDocument();

      // Re-rendering alone must not recompute the cutoff — that was the memo
      // thrash this guards against (see "Reduce memo thrash on map update").
      rerender(<MapView contacts={[contact]} focusedKey={null} />);

      expect(screen.getByText(/showing 1 contact heard in the last 7 days/i)).toBeInTheDocument();
      expect(screen.getByText('Almost Stale')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  describe('"heard since" filter', () => {
    function contactLastSeen(name: string, key: string, lastSeen: number | null): Contact {
      return {
        public_key: key.repeat(32),
        name,
        type: 1,
        flags: 0,
        direct_path: null,
        direct_path_len: -1,
        direct_path_hash_mode: -1,
        route_override_path: null,
        route_override_len: null,
        route_override_hash_mode: null,
        last_advert: null,
        lat: 40,
        lon: -74,
        last_seen: lastSeen,
        on_radio: false,
        favorite: false,
        last_contacted: null,
        last_read_at: null,
        first_seen: null,
      };
    }

    const nowSec = () => Math.floor(Date.now() / 1000);

    it('narrows to a relative preset and restores on a wider one', () => {
      const fresh = contactLastSeen('Fresh Node', 'aa', nowSec() - 60);
      const older = contactLastSeen('Older Node', 'bb', nowSec() - 5 * 60 * 60);

      render(<MapView contacts={[fresh, older]} />);

      // Default window is 7 days, so both are visible.
      expect(screen.getByText('Fresh Node')).toBeInTheDocument();
      expect(screen.getByText('Older Node')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: '<1h' }));

      expect(screen.getByText('Fresh Node')).toBeInTheDocument();
      expect(screen.queryByText('Older Node')).toBeNull();
      expect(screen.getByText(/heard in the last 1 hour/i)).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: '<1d' }));

      expect(screen.getByText('Older Node')).toBeInTheDocument();
    });

    it('reveals contacts older than the previous 7-day ceiling under "All"', () => {
      const ancient = contactLastSeen('Ancient Node', 'cc', nowSec() - 30 * 24 * 60 * 60);

      render(<MapView contacts={[ancient]} />);

      // Previously the map capped at 7 days and this node was unreachable.
      expect(screen.queryByText('Ancient Node')).toBeNull();

      fireEvent.click(screen.getByRole('button', { name: 'All' }));

      expect(screen.getByText('Ancient Node')).toBeInTheDocument();
      expect(screen.getByText(/heard at any time/i)).toBeInTheDocument();
    });

    it('treats a custom datetime as local wall-clock time', () => {
      vi.useFakeTimers();
      try {
        vi.setSystemTime(new Date('2026-03-15T12:00:00'));

        // 11:00 and 13:00 local, either side of a 12:30 local cutoff.
        const before = contactLastSeen(
          'Before Cutoff',
          'dd',
          Math.floor(new Date('2026-03-15T11:00:00').getTime() / 1000)
        );
        const after = contactLastSeen(
          'After Cutoff',
          'ee',
          Math.floor(new Date('2026-03-15T13:00:00').getTime() / 1000)
        );

        render(<MapView contacts={[before, after]} />);

        fireEvent.click(screen.getByRole('button', { name: 'Custom' }));
        fireEvent.change(screen.getByLabelText(/heard since \(local time\)/i), {
          target: { value: '2026-03-15T12:30' },
        });

        expect(screen.getByText('After Cutoff')).toBeInTheDocument();
        expect(screen.queryByText('Before Cutoff')).toBeNull();
      } finally {
        vi.useRealTimers();
      }
    });

    it('always shows the focused contact even when it falls outside the window', () => {
      const stale = contactLastSeen('Stale Focus', 'ff', nowSec() - 30 * 24 * 60 * 60);

      render(<MapView contacts={[stale]} focusedKey={stale.public_key} />);

      fireEvent.click(screen.getByRole('button', { name: '<1h' }));

      expect(screen.getByText('Stale Focus')).toBeInTheDocument();
      expect(screen.getByText(/plus the focused contact/i)).toBeInTheDocument();
    });
  });

  it('excludes contacts whose public key is in blockedKeys', () => {
    const visible: Contact = {
      public_key: 'aa'.repeat(32),
      name: 'Visible',
      type: 1,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      route_override_path: null,
      route_override_len: null,
      route_override_hash_mode: null,
      last_advert: null,
      lat: 40,
      lon: -74,
      last_seen: Math.floor(Date.now() / 1000),
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };
    const blocked: Contact = {
      public_key: 'bb'.repeat(32),
      name: 'Blocked',
      type: 2,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      route_override_path: null,
      route_override_len: null,
      route_override_hash_mode: null,
      last_advert: null,
      lat: 41,
      lon: -73,
      last_seen: Math.floor(Date.now() / 1000),
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };

    render(<MapView contacts={[visible, blocked]} blockedKeys={['bb'.repeat(32)]} />);

    expect(screen.getByText('Visible')).toBeInTheDocument();
    expect(screen.queryByText('Blocked')).toBeNull();
  });

  it('excludes contacts whose name is in blockedNames', () => {
    const visible: Contact = {
      public_key: 'aa'.repeat(32),
      name: 'Visible',
      type: 1,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      route_override_path: null,
      route_override_len: null,
      route_override_hash_mode: null,
      last_advert: null,
      lat: 40,
      lon: -74,
      last_seen: Math.floor(Date.now() / 1000),
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };
    const blocked: Contact = {
      public_key: 'cc'.repeat(32),
      name: 'BadActor',
      type: 2,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      route_override_path: null,
      route_override_len: null,
      route_override_hash_mode: null,
      last_advert: null,
      lat: 41,
      lon: -73,
      last_seen: Math.floor(Date.now() / 1000),
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };

    render(<MapView contacts={[visible, blocked]} blockedNames={['BadActor']} />);

    expect(screen.getByText('Visible')).toBeInTheDocument();
    expect(screen.queryByText('BadActor')).toBeNull();
  });
});
