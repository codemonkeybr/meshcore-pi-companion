import {
  useState,
  useCallback,
  useRef,
  useEffect,
  useMemo,
  type FormEvent,
  lazy,
  Suspense,
} from 'react';
import { toast } from './ui/sonner';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Separator } from './ui/separator';
import { RepeaterLogin } from './RepeaterLogin';
import { useRepeaterDashboard } from '../hooks/useRepeaterDashboard';
import { formatTime } from '../utils/messageParser';
import { isFavorite } from '../utils/favorites';
import { cn } from '@/lib/utils';
import type {
  Contact,
  Conversation,
  Favorite,
  LppSensor,
  PaneState,
  RepeaterStatusResponse,
  RepeaterNeighborsResponse,
  RepeaterAclResponse,
  RepeaterRadioSettingsResponse,
  RepeaterAdvertIntervalsResponse,
  RepeaterOwnerInfoResponse,
  RepeaterLppTelemetryResponse,
  NeighborInfo,
} from '../types';
import { isValidLocation, calculateDistance, formatDistance } from '../utils/pathUtils';

// Lazy-load the entire mini-map file so react-leaflet imports are bundled together
// and MapContainer only mounts once (avoids "already initialized" crash).
const NeighborsMiniMap = lazy(() =>
  import('./NeighborsMiniMap').then((m) => ({ default: m.NeighborsMiniMap }))
);

// --- Shared Icons ---

function RefreshIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={2}
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
      />
    </svg>
  );
}

// --- Utility ---

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (days > 0) {
    if (hours > 0 && mins > 0) return `${days}d${hours}h${mins}m`;
    if (hours > 0) return `${days}d${hours}h`;
    if (mins > 0) return `${days}d${mins}m`;
    return `${days}d`;
  }
  if (hours > 0) return mins > 0 ? `${hours}h${mins}m` : `${hours}h`;
  return `${mins}m`;
}

// --- Generic Pane Wrapper ---

function RepeaterPane({
  title,
  state,
  onRefresh,
  disabled,
  children,
  className,
  contentClassName,
}: {
  title: string;
  state: PaneState;
  onRefresh?: () => void;
  disabled?: boolean;
  children: React.ReactNode;
  className?: string;
  contentClassName?: string;
}) {
  return (
    <div className={cn('border border-border rounded-lg overflow-hidden', className)}>
      <div className="flex items-center justify-between px-3 py-2 bg-muted/50 border-b border-border">
        <h3 className="text-sm font-medium">{title}</h3>
        {onRefresh && (
          <button
            type="button"
            onClick={onRefresh}
            disabled={disabled || state.loading}
            className={cn(
              'p-1 rounded transition-colors disabled:opacity-50',
              disabled || state.loading
                ? 'text-muted-foreground'
                : 'text-green-500 hover:bg-accent hover:text-green-400'
            )}
            title="Refresh"
          >
            <RefreshIcon
              className={cn(
                'w-3.5 h-3.5',
                state.loading && 'animate-spin [animation-direction:reverse]'
              )}
            />
          </button>
        )}
      </div>
      {state.error && (
        <div className="px-3 py-1.5 text-xs text-destructive bg-destructive/5 border-b border-border">
          {state.error}
        </div>
      )}
      <div className={cn('p-3', contentClassName)}>
        {state.loading ? (
          <p className="text-sm text-muted-foreground italic">
            Fetching{state.attempt > 1 ? ` (attempt ${state.attempt}/${3})` : ''}...
          </p>
        ) : (
          children
        )}
      </div>
    </div>
  );
}

function NotFetched() {
  return <p className="text-sm text-muted-foreground italic">&lt;not fetched&gt;</p>;
}

function KvRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between items-center text-sm py-0.5">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium text-right">{value}</span>
    </div>
  );
}

// --- Individual Panes ---

function TelemetryPane({
  data,
  state,
  onRefresh,
  disabled,
}: {
  data: RepeaterStatusResponse | null;
  state: PaneState;
  onRefresh: () => void;
  disabled?: boolean;
}) {
  return (
    <RepeaterPane title="Telemetry" state={state} onRefresh={onRefresh} disabled={disabled}>
      {!data ? (
        <NotFetched />
      ) : (
        <div className="space-y-2">
          <KvRow label="Battery" value={`${data.battery_volts.toFixed(3)}V`} />
          <KvRow label="Uptime" value={formatDuration(data.uptime_seconds)} />
          <KvRow label="TX Airtime" value={formatDuration(data.airtime_seconds)} />
          <KvRow label="RX Airtime" value={formatDuration(data.rx_airtime_seconds)} />
          <Separator className="my-1" />
          <KvRow label="Noise Floor" value={`${data.noise_floor_dbm} dBm`} />
          <KvRow label="Last RSSI" value={`${data.last_rssi_dbm} dBm`} />
          <KvRow label="Last SNR" value={`${data.last_snr_db.toFixed(1)} dB`} />
          <Separator className="my-1" />
          <KvRow
            label="Packets"
            value={`${data.packets_received.toLocaleString()} rx / ${data.packets_sent.toLocaleString()} tx`}
          />
          <KvRow
            label="Flood"
            value={`${data.recv_flood.toLocaleString()} rx / ${data.sent_flood.toLocaleString()} tx`}
          />
          <KvRow
            label="Direct"
            value={`${data.recv_direct.toLocaleString()} rx / ${data.sent_direct.toLocaleString()} tx`}
          />
          <KvRow
            label="Duplicates"
            value={`${data.flood_dups.toLocaleString()} flood / ${data.direct_dups.toLocaleString()} direct`}
          />
          <Separator className="my-1" />
          <KvRow label="TX Queue" value={data.tx_queue_len} />
          <KvRow label="Debug Flags" value={data.full_events} />
        </div>
      )}
    </RepeaterPane>
  );
}

function NeighborsPane({
  data,
  state,
  onRefresh,
  disabled,
  contacts,
  radioLat,
  radioLon,
  radioName,
}: {
  data: RepeaterNeighborsResponse | null;
  state: PaneState;
  onRefresh: () => void;
  disabled?: boolean;
  contacts: Contact[];
  radioLat: number | null;
  radioLon: number | null;
  radioName: string | null;
}) {
  // Resolve contact data for each neighbor in a single pass — used for
  // coords (mini-map), distances (table column), and sorted display order.
  const { neighborsWithCoords, sorted, hasDistances } = useMemo(() => {
    if (!data) {
      return {
        neighborsWithCoords: [] as Array<NeighborInfo & { lat: number | null; lon: number | null }>,
        sorted: [] as Array<NeighborInfo & { distance: string | null }>,
        hasDistances: false,
      };
    }

    const withCoords: Array<NeighborInfo & { lat: number | null; lon: number | null }> = [];
    const enriched: Array<NeighborInfo & { distance: string | null }> = [];
    let anyDist = false;

    for (const n of data.neighbors) {
      const contact = contacts.find((c) => c.public_key.startsWith(n.pubkey_prefix));
      const nLat = contact?.lat ?? null;
      const nLon = contact?.lon ?? null;

      let dist: string | null = null;
      if (isValidLocation(radioLat, radioLon) && isValidLocation(nLat, nLon)) {
        const distKm = calculateDistance(radioLat, radioLon, nLat, nLon);
        if (distKm != null) {
          dist = formatDistance(distKm);
          anyDist = true;
        }
      }
      enriched.push({ ...n, distance: dist });

      if (isValidLocation(nLat, nLon)) {
        withCoords.push({ ...n, lat: nLat, lon: nLon });
      }
    }

    enriched.sort((a, b) => b.snr - a.snr);

    return {
      neighborsWithCoords: withCoords,
      sorted: enriched,
      hasDistances: anyDist,
    };
  }, [data, contacts, radioLat, radioLon]);

  return (
    <RepeaterPane
      title="Neighbors"
      state={state}
      onRefresh={onRefresh}
      disabled={disabled}
      className="flex flex-col"
      contentClassName="flex-1 flex flex-col"
    >
      {!data ? (
        <NotFetched />
      ) : sorted.length === 0 ? (
        <p className="text-sm text-muted-foreground">No neighbors reported</p>
      ) : (
        <div className="flex-1 flex flex-col gap-2">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-muted-foreground text-xs">
                  <th className="pb-1 font-medium">Name</th>
                  <th className="pb-1 font-medium text-right">SNR</th>
                  {hasDistances && <th className="pb-1 font-medium text-right">Dist</th>}
                  <th className="pb-1 font-medium text-right">Last Heard</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((n, i) => {
                  const dist = n.distance;
                  const snrStr = n.snr >= 0 ? `+${n.snr.toFixed(1)}` : n.snr.toFixed(1);
                  const snrColor =
                    n.snr >= 6 ? 'text-green-500' : n.snr >= 0 ? 'text-yellow-500' : 'text-red-500';
                  return (
                    <tr key={i} className="border-t border-border/50">
                      <td className="py-1">{n.name || n.pubkey_prefix}</td>
                      <td className={cn('py-1 text-right font-mono', snrColor)}>{snrStr} dB</td>
                      {hasDistances && (
                        <td className="py-1 text-right text-muted-foreground font-mono">
                          {dist ?? '—'}
                        </td>
                      )}
                      <td className="py-1 text-right text-muted-foreground">
                        {formatDuration(n.last_heard_seconds)} ago
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {(neighborsWithCoords.length > 0 || isValidLocation(radioLat, radioLon)) && (
            <Suspense
              fallback={
                <div className="h-48 flex items-center justify-center text-xs text-muted-foreground">
                  Loading map...
                </div>
              }
            >
              <NeighborsMiniMap
                key={neighborsWithCoords.map((n) => n.pubkey_prefix).join(',')}
                neighbors={neighborsWithCoords}
                radioLat={radioLat}
                radioLon={radioLon}
                radioName={radioName}
              />
            </Suspense>
          )}
        </div>
      )}
    </RepeaterPane>
  );
}

function AclPane({
  data,
  state,
  onRefresh,
  disabled,
}: {
  data: RepeaterAclResponse | null;
  state: PaneState;
  onRefresh: () => void;
  disabled?: boolean;
}) {
  const permColor: Record<number, string> = {
    0: 'bg-muted text-muted-foreground',
    1: 'bg-blue-500/10 text-blue-500',
    2: 'bg-green-500/10 text-green-500',
    3: 'bg-amber-500/10 text-amber-500',
  };

  return (
    <RepeaterPane title="ACL" state={state} onRefresh={onRefresh} disabled={disabled}>
      {!data ? (
        <NotFetched />
      ) : data.acl.length === 0 ? (
        <p className="text-sm text-muted-foreground">No ACL entries</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-muted-foreground text-xs">
              <th className="pb-1 font-medium">Name</th>
              <th className="pb-1 font-medium text-right">Permission</th>
            </tr>
          </thead>
          <tbody>
            {data.acl.map((entry, i) => (
              <tr key={i} className="border-t border-border/50">
                <td className="py-1">{entry.name || entry.pubkey_prefix}</td>
                <td className="py-1 text-right">
                  <span
                    className={cn(
                      'text-xs px-1.5 py-0.5 rounded',
                      permColor[entry.permission] ?? 'bg-muted text-muted-foreground'
                    )}
                  >
                    {entry.permission_name}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </RepeaterPane>
  );
}

export function formatClockDrift(clockUtc: string): { text: string; isLarge: boolean } {
  // Firmware format: "HH:MM - D/M/YYYY UTC" or "HH:MM:SS - D/M/YYYY UTC"
  // Also handle ISO-like: "YYYY-MM-DD HH:MM:SS"
  let parsed: Date;
  const fwMatch = clockUtc.match(
    /^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*-\s*(\d{1,2})\/(\d{1,2})\/(\d{4})/
  );
  if (fwMatch) {
    const [, hh, mm, ss, dd, mo, yyyy] = fwMatch;
    parsed = new Date(Date.UTC(+yyyy, +mo - 1, +dd, +hh, +mm, +(ss ?? 0)));
  } else {
    parsed = new Date(
      clockUtc.replace(' ', 'T') + (clockUtc.includes('Z') || clockUtc.includes('UTC') ? '' : 'Z')
    );
  }
  if (isNaN(parsed.getTime())) return { text: '(invalid)', isLarge: false };

  const driftMs = Math.abs(Date.now() - parsed.getTime());
  const driftSec = Math.floor(driftMs / 1000);

  if (driftSec >= 86400) return { text: '>24 hours!', isLarge: true };

  const h = Math.floor(driftSec / 3600);
  const m = Math.floor((driftSec % 3600) / 60);
  const s = driftSec % 60;

  const parts: string[] = [];
  if (h > 0) parts.push(`${h}h`);
  if (m > 0) parts.push(`${m}m`);
  parts.push(`${s}s`);

  return { text: parts.join(''), isLarge: false };
}

function RadioSettingsPane({
  data,
  state,
  onRefresh,
  disabled,
  advertData,
  advertState,
  onRefreshAdvert,
}: {
  data: RepeaterRadioSettingsResponse | null;
  state: PaneState;
  onRefresh: () => void;
  disabled?: boolean;
  advertData: RepeaterAdvertIntervalsResponse | null;
  advertState: PaneState;
  onRefreshAdvert: () => void;
}) {
  const clockDrift = useMemo(() => {
    if (!data?.clock_utc) return null;
    return formatClockDrift(data.clock_utc);
  }, [data?.clock_utc]);

  return (
    <RepeaterPane title="Radio Settings" state={state} onRefresh={onRefresh} disabled={disabled}>
      {!data ? (
        <NotFetched />
      ) : (
        <div>
          <KvRow label="Firmware" value={data.firmware_version ?? '—'} />
          <KvRow label="Radio" value={data.radio ?? '—'} />
          <KvRow label="TX Power" value={data.tx_power != null ? `${data.tx_power} dBm` : '—'} />
          <KvRow label="Airtime Factor" value={data.airtime_factor ?? '—'} />
          <KvRow label="Repeat Mode" value={data.repeat_enabled ?? '—'} />
          <KvRow label="Max Flood Hops" value={data.flood_max ?? '—'} />
          <Separator className="my-1" />
          <KvRow label="Name" value={data.name ?? '—'} />
          <KvRow
            label="Lat / Lon"
            value={
              data.lat != null || data.lon != null ? `${data.lat ?? '—'}, ${data.lon ?? '—'}` : '—'
            }
          />
          <Separator className="my-1" />
          <div className="flex justify-between text-sm py-0.5">
            <span className="text-muted-foreground">Clock (UTC)</span>
            <span>
              {data.clock_utc ?? '—'}
              {clockDrift && (
                <span
                  className={cn(
                    'ml-2 text-xs',
                    clockDrift.isLarge ? 'text-red-500' : 'text-muted-foreground'
                  )}
                >
                  (drift: {clockDrift.text})
                </span>
              )}
            </span>
          </div>
        </div>
      )}
      {/* Advert Intervals sub-section */}
      <Separator className="my-2" />
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-medium text-muted-foreground">Advert Intervals</span>
        <button
          type="button"
          onClick={onRefreshAdvert}
          disabled={disabled || advertState.loading}
          className={cn(
            'p-1 rounded transition-colors disabled:opacity-50',
            disabled || advertState.loading
              ? 'text-muted-foreground'
              : 'text-green-500 hover:bg-accent hover:text-green-400'
          )}
          title="Refresh Advert Intervals"
        >
          <RefreshIcon
            className={cn(
              'w-3 h-3',
              advertState.loading && 'animate-spin [animation-direction:reverse]'
            )}
          />
        </button>
      </div>
      {advertState.error && <p className="text-xs text-destructive mb-1">{advertState.error}</p>}
      {advertState.loading ? (
        <p className="text-sm text-muted-foreground italic">
          Fetching{advertState.attempt > 1 ? ` (attempt ${advertState.attempt}/3)` : ''}...
        </p>
      ) : !advertData ? (
        <NotFetched />
      ) : (
        <div>
          <KvRow label="Local Advert" value={formatAdvertInterval(advertData.advert_interval)} />
          <KvRow
            label="Flood Advert"
            value={formatAdvertInterval(advertData.flood_advert_interval)}
          />
        </div>
      )}
    </RepeaterPane>
  );
}

function formatAdvertInterval(val: string | null): string {
  if (val == null) return '—';
  const trimmed = val.trim();
  if (trimmed === '0') return '<disabled>';
  return `${trimmed}h`;
}

const LPP_UNIT_MAP: Record<string, string> = {
  temperature: '°C',
  humidity: '%',
  barometer: 'hPa',
  voltage: 'V',
  current: 'mA',
  luminosity: 'lux',
  altitude: 'm',
  power: 'W',
  distance: 'mm',
  energy: 'kWh',
  direction: '°',
  concentration: 'ppm',
  colour: '',
};

function formatLppLabel(typeName: string): string {
  return typeName.charAt(0).toUpperCase() + typeName.slice(1).replace(/_/g, ' ');
}

function LppSensorRow({ sensor }: { sensor: LppSensor }) {
  const label = formatLppLabel(sensor.type_name);

  if (typeof sensor.value === 'object' && sensor.value !== null) {
    // Multi-value sensor (GPS, accelerometer, etc.)
    return (
      <div className="py-0.5">
        <span className="text-sm text-muted-foreground">{label}</span>
        <div className="pl-3">
          {Object.entries(sensor.value).map(([k, v]) => (
            <KvRow
              key={k}
              label={k.charAt(0).toUpperCase() + k.slice(1)}
              value={typeof v === 'number' ? v.toFixed(2) : String(v)}
            />
          ))}
        </div>
      </div>
    );
  }

  const unit = LPP_UNIT_MAP[sensor.type_name] ?? '';
  const formatted =
    typeof sensor.value === 'number'
      ? `${sensor.value % 1 === 0 ? sensor.value : sensor.value.toFixed(2)}${unit ? ` ${unit}` : ''}`
      : String(sensor.value);

  return <KvRow label={label} value={formatted} />;
}

function LppTelemetryPane({
  data,
  state,
  onRefresh,
  disabled,
}: {
  data: RepeaterLppTelemetryResponse | null;
  state: PaneState;
  onRefresh: () => void;
  disabled?: boolean;
}) {
  return (
    <RepeaterPane title="LPP Sensors" state={state} onRefresh={onRefresh} disabled={disabled}>
      {!data ? (
        <NotFetched />
      ) : data.sensors.length === 0 ? (
        <p className="text-sm text-muted-foreground">No sensor data available</p>
      ) : (
        <div className="space-y-0.5">
          {data.sensors.map((sensor, i) => (
            <LppSensorRow key={i} sensor={sensor} />
          ))}
        </div>
      )}
    </RepeaterPane>
  );
}

function OwnerInfoPane({
  data,
  state,
  onRefresh,
  disabled,
}: {
  data: RepeaterOwnerInfoResponse | null;
  state: PaneState;
  onRefresh: () => void;
  disabled?: boolean;
}) {
  return (
    <RepeaterPane title="Owner Info" state={state} onRefresh={onRefresh} disabled={disabled}>
      {!data ? (
        <NotFetched />
      ) : (
        <div className="break-all">
          <KvRow label="Owner Info" value={data.owner_info ?? '—'} />
          <KvRow label="Guest Password" value={data.guest_password ?? '—'} />
        </div>
      )}
    </RepeaterPane>
  );
}

function ActionsPane({
  onSendAdvert,
  onSyncClock,
  onReboot,
  consoleLoading,
}: {
  onSendAdvert: () => void;
  onSyncClock: () => void;
  onReboot: () => void;
  consoleLoading: boolean;
}) {
  const [confirmReboot, setConfirmReboot] = useState(false);

  const handleReboot = useCallback(() => {
    if (!confirmReboot) {
      setConfirmReboot(true);
      return;
    }
    setConfirmReboot(false);
    onReboot();
  }, [confirmReboot, onReboot]);

  // Reset confirmation after 3 seconds
  useEffect(() => {
    if (!confirmReboot) return;
    const timer = setTimeout(() => setConfirmReboot(false), 3000);
    return () => clearTimeout(timer);
  }, [confirmReboot]);

  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <div className="px-3 py-2 bg-muted/50 border-b border-border">
        <h3 className="text-sm font-medium">Actions</h3>
      </div>
      <div className="p-3 flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={onSendAdvert} disabled={consoleLoading}>
          Send Advert
        </Button>
        <Button variant="outline" size="sm" onClick={onSyncClock} disabled={consoleLoading}>
          Sync Clock
        </Button>
        <Button
          variant={confirmReboot ? 'destructive' : 'outline'}
          size="sm"
          onClick={handleReboot}
          disabled={consoleLoading}
        >
          {confirmReboot ? 'Confirm Reboot' : 'Reboot'}
        </Button>
      </div>
    </div>
  );
}

function ConsolePane({
  history,
  loading,
  onSend,
}: {
  history: Array<{ command: string; response: string; timestamp: number; outgoing: boolean }>;
  loading: boolean;
  onSend: (command: string) => Promise<void>;
}) {
  const [input, setInput] = useState('');
  const outputRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new entries
  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [history]);

  const handleSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      const trimmed = input.trim();
      if (!trimmed || loading) return;
      setInput('');
      await onSend(trimmed);
    },
    [input, loading, onSend]
  );

  return (
    <div className="border border-border rounded-lg overflow-hidden col-span-full">
      <div className="px-3 py-2 bg-muted/50 border-b border-border">
        <h3 className="text-sm font-medium">Console</h3>
      </div>
      <div
        ref={outputRef}
        className="h-48 overflow-y-auto p-3 font-mono text-xs bg-black/50 text-green-400 space-y-1"
      >
        {history.length === 0 && (
          <p className="text-muted-foreground italic">Type a CLI command below...</p>
        )}
        {history.map((entry, i) =>
          entry.outgoing ? (
            <div key={i} className="text-green-300">
              &gt; {entry.command}
            </div>
          ) : (
            <div key={i} className="text-green-400/80 whitespace-pre-wrap">
              {entry.response}
            </div>
          )
        )}
        {loading && <div className="text-muted-foreground animate-pulse">...</div>}
      </div>
      <form onSubmit={handleSubmit} className="flex gap-2 p-2 border-t border-border">
        <Input
          type="text"
          autoComplete="off"
          name="console-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="CLI command..."
          disabled={loading}
          className="flex-1 font-mono text-sm"
        />
        <Button type="submit" size="sm" disabled={loading || !input.trim()}>
          Send
        </Button>
      </form>
    </div>
  );
}

// --- Main Dashboard ---

interface RepeaterDashboardProps {
  conversation: Conversation;
  contacts: Contact[];
  favorites: Favorite[];
  radioLat: number | null;
  radioLon: number | null;
  radioName: string | null;
  onTrace: () => void;
  onToggleFavorite: (type: 'channel' | 'contact', id: string) => void;
  onDeleteContact: (publicKey: string) => void;
}

export function RepeaterDashboard({
  conversation,
  contacts,
  favorites,
  radioLat,
  radioLon,
  radioName,
  onTrace,
  onToggleFavorite,
  onDeleteContact,
}: RepeaterDashboardProps) {
  const {
    loggedIn,
    loginLoading,
    loginError,
    paneData,
    paneStates,
    consoleHistory,
    consoleLoading,
    login,
    loginAsGuest,
    refreshPane,
    loadAll,
    sendConsoleCommand,
    sendAdvert,
    rebootRepeater,
    syncClock,
  } = useRepeaterDashboard(conversation);

  const contact = contacts.find((c) => c.public_key === conversation.id);
  const isFav = isFavorite(favorites, 'contact', conversation.id);

  // Loading all panes indicator
  const anyLoading = Object.values(paneStates).some((s) => s.loading);

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Header */}
      <div className="flex justify-between items-center px-4 py-2.5 border-b border-border gap-2">
        <span className="flex flex-wrap items-baseline gap-x-2 min-w-0 flex-1">
          <span className="flex-shrink-0 font-semibold text-base">{conversation.name}</span>
          <span
            className="font-normal text-[11px] text-muted-foreground font-mono truncate cursor-pointer hover:text-primary transition-colors"
            onClick={() => {
              navigator.clipboard.writeText(conversation.id);
              toast.success('Contact key copied!');
            }}
            title="Click to copy"
          >
            {conversation.id}
          </span>
          {contact?.last_seen && (
            <span className="font-normal text-sm text-muted-foreground flex-shrink-0">
              (Last heard: {formatTime(contact.last_seen)})
            </span>
          )}
        </span>
        <div className="flex items-center gap-0.5 flex-shrink-0">
          {loggedIn && (
            <Button
              variant="outline"
              size="sm"
              onClick={loadAll}
              disabled={anyLoading}
              className="text-xs border-green-600 text-green-600 hover:bg-green-600/10 hover:text-green-600"
            >
              {anyLoading ? 'Loading...' : 'Load All'}
            </Button>
          )}
          <button
            className="p-1.5 rounded hover:bg-accent text-lg leading-none transition-colors"
            onClick={onTrace}
            title="Direct Trace"
          >
            &#x1F6CE;
          </button>
          <button
            className="p-1.5 rounded hover:bg-accent text-lg leading-none transition-colors"
            onClick={() => onToggleFavorite('contact', conversation.id)}
            title={isFav ? 'Remove from favorites' : 'Add to favorites'}
          >
            {isFav ? (
              <span className="text-amber-400">&#9733;</span>
            ) : (
              <span className="text-muted-foreground">&#9734;</span>
            )}
          </button>
          <button
            className="p-1.5 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive text-lg leading-none transition-colors"
            onClick={() => onDeleteContact(conversation.id)}
            title="Delete"
          >
            &#128465;
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4">
        {!loggedIn ? (
          <RepeaterLogin
            repeaterName={conversation.name}
            loading={loginLoading}
            error={loginError}
            onLogin={login}
            onLoginAsGuest={loginAsGuest}
          />
        ) : (
          <div className="space-y-4">
            {/* Top row: Telemetry + Radio Settings | Neighbors (with expanding map) */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="flex flex-col gap-4">
                <TelemetryPane
                  data={paneData.status}
                  state={paneStates.status}
                  onRefresh={() => refreshPane('status')}
                  disabled={anyLoading}
                />
                <RadioSettingsPane
                  data={paneData.radioSettings}
                  state={paneStates.radioSettings}
                  onRefresh={() => refreshPane('radioSettings')}
                  disabled={anyLoading}
                  advertData={paneData.advertIntervals}
                  advertState={paneStates.advertIntervals}
                  onRefreshAdvert={() => refreshPane('advertIntervals')}
                />
                <LppTelemetryPane
                  data={paneData.lppTelemetry}
                  state={paneStates.lppTelemetry}
                  onRefresh={() => refreshPane('lppTelemetry')}
                  disabled={anyLoading}
                />
              </div>
              <NeighborsPane
                data={paneData.neighbors}
                state={paneStates.neighbors}
                onRefresh={() => refreshPane('neighbors')}
                disabled={anyLoading}
                contacts={contacts}
                radioLat={radioLat}
                radioLon={radioLon}
                radioName={radioName}
              />
            </div>

            {/* Remaining panes: ACL | Owner Info + Actions */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <AclPane
                data={paneData.acl}
                state={paneStates.acl}
                onRefresh={() => refreshPane('acl')}
                disabled={anyLoading}
              />
              <div className="flex flex-col gap-4">
                <OwnerInfoPane
                  data={paneData.ownerInfo}
                  state={paneStates.ownerInfo}
                  onRefresh={() => refreshPane('ownerInfo')}
                  disabled={anyLoading}
                />
                <ActionsPane
                  onSendAdvert={sendAdvert}
                  onSyncClock={syncClock}
                  onReboot={rebootRepeater}
                  consoleLoading={consoleLoading}
                />
              </div>
            </div>

            {/* Console — full width */}
            <ConsolePane
              history={consoleHistory}
              loading={consoleLoading}
              onSend={sendConsoleCommand}
            />
          </div>
        )}
      </div>
    </div>
  );
}
