import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RepeaterDashboard } from '../components/RepeaterDashboard';
import type { UseRepeaterDashboardResult } from '../hooks/useRepeaterDashboard';
import type { Contact, Conversation, Favorite } from '../types';

// Mock the hook — typed as mutable version of the return type
const mockHook: {
  -readonly [K in keyof UseRepeaterDashboardResult]: UseRepeaterDashboardResult[K];
} = {
  loggedIn: false,
  loginLoading: false,
  loginError: null,
  paneData: {
    status: null,
    nodeInfo: null,
    neighbors: null,
    acl: null,
    radioSettings: null,
    advertIntervals: null,
    ownerInfo: null,

    lppTelemetry: null,
  },
  paneStates: {
    status: { loading: false, attempt: 0, error: null },
    nodeInfo: { loading: false, attempt: 0, error: null },
    neighbors: { loading: false, attempt: 0, error: null },
    acl: { loading: false, attempt: 0, error: null },
    radioSettings: { loading: false, attempt: 0, error: null },
    advertIntervals: { loading: false, attempt: 0, error: null },
    ownerInfo: { loading: false, attempt: 0, error: null },

    lppTelemetry: { loading: false, attempt: 0, error: null },
  },
  consoleHistory: [],
  consoleLoading: false,
  login: vi.fn(),
  loginAsGuest: vi.fn(),
  refreshPane: vi.fn(),
  loadAll: vi.fn(),
  sendConsoleCommand: vi.fn(),
  sendZeroHopAdvert: vi.fn(),
  sendFloodAdvert: vi.fn(),
  rebootRepeater: vi.fn(),
  syncClock: vi.fn(),
};

vi.mock('../hooks/useRepeaterDashboard', () => ({
  useRepeaterDashboard: () => mockHook,
}));

// Mock sonner toast
vi.mock('../components/ui/sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
}));

// Mock leaflet imports (not needed in test)
vi.mock('react-leaflet', () => ({
  MapContainer: () => null,
  TileLayer: () => null,
  CircleMarker: () => null,
  Popup: () => null,
  Polyline: () => null,
}));

const REPEATER_KEY = 'aa'.repeat(32);

const conversation: Conversation = {
  type: 'contact',
  id: REPEATER_KEY,
  name: 'TestRepeater',
};

const contacts: Contact[] = [
  {
    public_key: REPEATER_KEY,
    name: 'TestRepeater',
    type: 2,
    flags: 0,
    last_path: null,
    last_path_len: -1,
    out_path_hash_mode: 0,
    last_advert: null,
    lat: null,
    lon: null,
    last_seen: null,
    on_radio: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: null,
  },
];

const favorites: Favorite[] = [];

const defaultProps = {
  conversation,
  contacts,
  favorites,
  notificationsSupported: true,
  notificationsEnabled: false,
  notificationsPermission: 'granted' as const,
  radioLat: null,
  radioLon: null,
  radioName: null,
  onTrace: vi.fn(),
  onToggleNotifications: vi.fn(),
  onToggleFavorite: vi.fn(),
  onDeleteContact: vi.fn(),
};

describe('RepeaterDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Reset mock hook state
    mockHook.loggedIn = false;
    mockHook.loginLoading = false;
    mockHook.loginError = null;
    mockHook.paneData = {
      status: null,
      nodeInfo: null,
      neighbors: null,
      acl: null,
      radioSettings: null,
      advertIntervals: null,
      ownerInfo: null,

      lppTelemetry: null,
    };
    mockHook.paneStates = {
      status: { loading: false, attempt: 0, error: null },
      nodeInfo: { loading: false, attempt: 0, error: null },
      neighbors: { loading: false, attempt: 0, error: null },
      acl: { loading: false, attempt: 0, error: null },
      radioSettings: { loading: false, attempt: 0, error: null },
      advertIntervals: { loading: false, attempt: 0, error: null },
      ownerInfo: { loading: false, attempt: 0, error: null },

      lppTelemetry: { loading: false, attempt: 0, error: null },
    };
    mockHook.consoleHistory = [];
    mockHook.consoleLoading = false;
  });

  it('renders login form when not logged in', () => {
    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText('Login with Password')).toBeInTheDocument();
    expect(screen.getByText('Login as Guest / ACLs')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Repeater password...')).toBeInTheDocument();
    expect(screen.getByText('Log in to access repeater dashboard')).toBeInTheDocument();
  });

  it('renders dashboard panes when logged in', () => {
    mockHook.loggedIn = true;

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText('Telemetry')).toBeInTheDocument();
    expect(screen.getByText('Node Info')).toBeInTheDocument();
    expect(screen.getByText('Neighbors')).toBeInTheDocument();
    expect(screen.getByText('ACL')).toBeInTheDocument();
    expect(screen.getByText('Radio Settings')).toBeInTheDocument();
    expect(screen.getByText('Advert Intervals')).toBeInTheDocument(); // sub-section inside Radio Settings
    expect(screen.getByText('LPP Sensors')).toBeInTheDocument();
    expect(screen.getByText('Owner Info')).toBeInTheDocument();
    expect(screen.getByText('Actions')).toBeInTheDocument();
    expect(screen.getByText('Console')).toBeInTheDocument();
  });

  it('shows not fetched placeholder for empty panes', () => {
    mockHook.loggedIn = true;

    render(<RepeaterDashboard {...defaultProps} />);

    // All panes should show <not fetched> since data is null
    const notFetched = screen.getAllByText('<not fetched>');
    expect(notFetched.length).toBeGreaterThanOrEqual(7); // At least 7 data panes (incl. LPP Sensors)
  });

  it('shows Load All button when logged in', () => {
    mockHook.loggedIn = true;

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText('Load All')).toBeInTheDocument();
  });

  it('calls loadAll when Load All button is clicked', () => {
    mockHook.loggedIn = true;

    render(<RepeaterDashboard {...defaultProps} />);

    fireEvent.click(screen.getByText('Load All'));
    expect(mockHook.loadAll).toHaveBeenCalledTimes(1);
  });

  it('shows enabled notification state and toggles when clicked', () => {
    render(
      <RepeaterDashboard
        {...defaultProps}
        notificationsEnabled
        onToggleNotifications={defaultProps.onToggleNotifications}
      />
    );

    fireEvent.click(screen.getByText('Notifications On'));

    expect(screen.getByText('Notifications On')).toBeInTheDocument();
    expect(defaultProps.onToggleNotifications).toHaveBeenCalledTimes(1);
  });

  it('shows login error when present', () => {
    mockHook.loginError = 'Invalid password';

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText('Invalid password')).toBeInTheDocument();
  });

  it('shows pane error when fetch fails', () => {
    mockHook.loggedIn = true;
    mockHook.paneStates.status = { loading: false, attempt: 3, error: 'Timeout' };

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText('Timeout')).toBeInTheDocument();
  });

  it('shows GPS unavailable message for neighbors when repeater coords are missing', () => {
    mockHook.loggedIn = true;
    mockHook.paneData.neighbors = {
      neighbors: [
        { pubkey_prefix: 'bbbbbbbbbbbb', name: 'Neighbor', snr: 7.2, last_heard_seconds: 9 },
      ],
    };
    mockHook.paneData.nodeInfo = {
      name: 'TestRepeater',
      lat: '0',
      lon: '0',
      clock_utc: null,
    };
    mockHook.paneStates.neighbors = {
      loading: false,
      attempt: 1,
      error: null,
      fetched_at: Date.now(),
    };
    mockHook.paneStates.nodeInfo = {
      loading: false,
      attempt: 1,
      error: null,
      fetched_at: Date.now(),
    };

    render(<RepeaterDashboard {...defaultProps} />);

    expect(
      screen.getByText(
        'GPS info failed to fetch; map and distance data not available. This may be due to missing or zero-zero GPS data on the repeater, or due to transient fetch failure. Try refreshing.'
      )
    ).toBeInTheDocument();
    expect(screen.queryByText('Dist')).not.toBeInTheDocument();
  });

  it('shows neighbor distance when repeater radio settings include valid coords', () => {
    mockHook.loggedIn = true;
    mockHook.paneData.neighbors = {
      neighbors: [
        { pubkey_prefix: 'bbbbbbbbbbbb', name: 'Neighbor', snr: 7.2, last_heard_seconds: 9 },
      ],
    };
    mockHook.paneData.nodeInfo = {
      name: 'TestRepeater',
      lat: '-31.9500',
      lon: '115.8600',
      clock_utc: null,
    };
    mockHook.paneStates.neighbors = {
      loading: false,
      attempt: 1,
      error: null,
      fetched_at: Date.now(),
    };
    mockHook.paneStates.nodeInfo = {
      loading: false,
      attempt: 1,
      error: null,
      fetched_at: Date.now(),
    };

    const contactsWithNeighbor = [
      ...contacts,
      {
        public_key: 'bbbbbbbbbbbb0000000000000000000000000000000000000000000000000000',
        name: 'Neighbor',
        type: 1,
        flags: 0,
        last_path: null,
        last_path_len: 0,
        out_path_hash_mode: 0,
        route_override_path: null,
        route_override_len: null,
        route_override_hash_mode: null,
        last_advert: null,
        lat: -31.94,
        lon: 115.87,
        last_seen: null,
        on_radio: false,
        last_contacted: null,
        last_read_at: null,
        first_seen: null,
      },
    ];

    render(<RepeaterDashboard {...defaultProps} contacts={contactsWithNeighbor} />);

    expect(screen.getByText('Dist')).toBeInTheDocument();
    expect(
      screen.queryByText(
        'GPS info failed to fetch; map and distance data not available. This may be due to missing or zero-zero GPS data on the repeater, or due to transient fetch failure. Try refreshing.'
      )
    ).not.toBeInTheDocument();
  });

  it('shows fetching state with attempt counter', () => {
    mockHook.loggedIn = true;
    mockHook.paneStates.status = { loading: true, attempt: 2, error: null };

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText('Fetching (attempt 2/3)...')).toBeInTheDocument();
  });

  it('renders telemetry data when available', () => {
    mockHook.loggedIn = true;
    mockHook.paneData.status = {
      battery_volts: 4.2,
      tx_queue_len: 0,
      noise_floor_dbm: -120,
      last_rssi_dbm: -85,
      last_snr_db: 7.5,
      packets_received: 100,
      packets_sent: 50,
      airtime_seconds: 600,
      rx_airtime_seconds: 1200,
      uptime_seconds: 86400,
      sent_flood: 10,
      sent_direct: 40,
      recv_flood: 30,
      recv_direct: 70,
      flood_dups: 1,
      direct_dups: 0,
      full_events: 0,
    };

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText('4.200V')).toBeInTheDocument();
    expect(screen.getByText('-120 dBm')).toBeInTheDocument();
    expect(screen.getByText('7.5 dB')).toBeInTheDocument();
  });

  it('formats the radio tuple and preserves the raw tuple in a tooltip', () => {
    mockHook.loggedIn = true;
    mockHook.paneData.radioSettings = {
      firmware_version: 'v1.0',
      radio: '910.5250244,62.5,7,5',
      tx_power: '20',
      airtime_factor: '0',
      repeat_enabled: '1',
      flood_max: '3',
    };

    render(<RepeaterDashboard {...defaultProps} />);

    const formatted = screen.getByText('910.525 MHz, BW 62.5 kHz, SF7, CR5');
    expect(formatted).toBeInTheDocument();
    expect(formatted).toHaveAttribute('title', '910.5250244,62.5,7,5');
  });

  it('shows fetched time and relative age when pane data has been loaded', () => {
    mockHook.loggedIn = true;
    mockHook.paneStates.status = {
      loading: false,
      attempt: 1,
      error: null,
      fetched_at: Date.now(),
    };

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText(/Fetched .*Just now/)).toBeInTheDocument();
  });

  it('renders action buttons', () => {
    mockHook.loggedIn = true;

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText('Zero Hop Advert')).toBeInTheDocument();
    expect(screen.getByText('Flood Advert')).toBeInTheDocument();
    expect(screen.getByText('Sync Clock')).toBeInTheDocument();
    expect(screen.getByText('Reboot')).toBeInTheDocument();
  });

  it('calls onTrace when trace button clicked', () => {
    render(<RepeaterDashboard {...defaultProps} />);

    // The trace button has title "Direct Trace"
    fireEvent.click(screen.getByTitle('Direct Trace'));
    expect(defaultProps.onTrace).toHaveBeenCalledTimes(1);
  });

  it('console shows placeholder when empty', () => {
    mockHook.loggedIn = true;

    render(<RepeaterDashboard {...defaultProps} />);

    expect(screen.getByText('Type a CLI command below...')).toBeInTheDocument();
  });

  describe('path type display and reset', () => {
    it('shows flood when last_path_len is -1', () => {
      render(<RepeaterDashboard {...defaultProps} />);

      expect(screen.getByText('flood')).toBeInTheDocument();
    });

    it('shows direct when last_path_len is 0', () => {
      const directContacts: Contact[] = [
        { ...contacts[0], last_path_len: 0, last_seen: 1700000000 },
      ];

      render(<RepeaterDashboard {...defaultProps} contacts={directContacts} />);

      expect(screen.getByText('direct')).toBeInTheDocument();
    });

    it('shows N hops when last_path_len > 0', () => {
      const hoppedContacts: Contact[] = [
        { ...contacts[0], last_path_len: 3, last_seen: 1700000000 },
      ];

      render(<RepeaterDashboard {...defaultProps} contacts={hoppedContacts} />);

      expect(screen.getByText('3 hops')).toBeInTheDocument();
    });

    it('shows 1 hop (singular) for single hop', () => {
      const oneHopContacts: Contact[] = [
        { ...contacts[0], last_path_len: 1, last_seen: 1700000000 },
      ];

      render(<RepeaterDashboard {...defaultProps} contacts={oneHopContacts} />);

      expect(screen.getByText('1 hop')).toBeInTheDocument();
    });

    it('direct path is clickable with routing override title', () => {
      const directContacts: Contact[] = [
        { ...contacts[0], last_path_len: 0, last_seen: 1700000000 },
      ];

      render(<RepeaterDashboard {...defaultProps} contacts={directContacts} />);

      const directEl = screen.getByTitle('Click to edit routing override');
      expect(directEl).toBeInTheDocument();
      expect(directEl.textContent).toBe('direct');
    });

    it('shows forced decorator when a routing override is active', () => {
      const forcedContacts: Contact[] = [
        {
          ...contacts[0],
          last_path_len: 1,
          last_seen: 1700000000,
          route_override_path: 'ae92f13e',
          route_override_len: 2,
          route_override_hash_mode: 1,
        },
      ];

      render(<RepeaterDashboard {...defaultProps} contacts={forcedContacts} />);

      expect(screen.getByText('2 hops')).toBeInTheDocument();
      expect(screen.getByText('(forced)')).toBeInTheDocument();
    });

    it('clicking direct path opens prompt and updates routing override', async () => {
      const directContacts: Contact[] = [
        { ...contacts[0], last_path_len: 0, last_seen: 1700000000 },
      ];

      const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue('0');

      const { api } = await import('../api');
      const overrideSpy = vi.spyOn(api, 'setContactRoutingOverride').mockResolvedValue({
        status: 'ok',
        public_key: REPEATER_KEY,
      });

      render(<RepeaterDashboard {...defaultProps} contacts={directContacts} />);

      fireEvent.click(screen.getByTitle('Click to edit routing override'));

      expect(promptSpy).toHaveBeenCalled();
      expect(overrideSpy).toHaveBeenCalledWith(REPEATER_KEY, '0');

      promptSpy.mockRestore();
      overrideSpy.mockRestore();
    });

    it('clicking path does not call API when prompt is cancelled', async () => {
      const directContacts: Contact[] = [
        { ...contacts[0], last_path_len: 0, last_seen: 1700000000 },
      ];

      const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue(null);

      const { api } = await import('../api');
      const overrideSpy = vi.spyOn(api, 'setContactRoutingOverride').mockResolvedValue({
        status: 'ok',
        public_key: REPEATER_KEY,
      });

      render(<RepeaterDashboard {...defaultProps} contacts={directContacts} />);

      fireEvent.click(screen.getByTitle('Click to edit routing override'));

      expect(promptSpy).toHaveBeenCalled();
      expect(overrideSpy).not.toHaveBeenCalled();

      promptSpy.mockRestore();
      overrideSpy.mockRestore();
    });
  });
});
