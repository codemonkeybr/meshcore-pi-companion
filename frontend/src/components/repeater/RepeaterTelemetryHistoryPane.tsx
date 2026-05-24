import { useState, useMemo } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
} from 'recharts';
import { cn } from '@/lib/utils';
import { Button } from '../ui/button';
import { Separator } from '../ui/separator';
import { lppDisplayUnit } from './repeaterPaneShared';
import { useDistanceUnit } from '../../contexts/DistanceUnitContext';
import type { TelemetryHistoryEntry, TelemetryLppSensor, Contact } from '../../types';

const MAX_TRACKED = 8;

type BuiltinMetric =
  | 'battery_volts'
  | 'noise_floor_dbm'
  | 'packets'
  | 'recv_errors'
  | 'uptime_seconds';

interface MetricConfig {
  label: string;
  unit: string;
  color: string;
}

const BUILTIN_METRIC_CONFIG: Record<BuiltinMetric, MetricConfig> = {
  battery_volts: { label: 'Voltage', unit: 'V', color: '#22c55e' },
  noise_floor_dbm: { label: 'Noise Floor', unit: 'dBm', color: '#8b5cf6' },
  packets: { label: 'Packets', unit: '', color: '#0ea5e9' },
  recv_errors: { label: 'RX Errors', unit: '', color: '#ef4444' },
  uptime_seconds: { label: 'Uptime', unit: 's', color: '#f59e0b' },
};

const BUILTIN_METRICS: BuiltinMetric[] = Object.keys(BUILTIN_METRIC_CONFIG) as BuiltinMetric[];

// Stable color rotation for dynamic LPP sensors
const LPP_COLORS = ['#ec4899', '#14b8a6', '#f97316', '#6366f1', '#84cc16', '#e11d48'];

/** Assign disambiguated flat keys to an array of LPP sensors.
 *  First occurrence keeps the base key; duplicates of the same (type, channel) get _2, _3, etc. */
function assignLppKeys(
  sensors: TelemetryLppSensor[]
): { sensor: TelemetryLppSensor; key: string; occurrence: number }[] {
  const counts = new Map<string, number>();
  return sensors.map((s) => {
    const base = `lpp_${s.type_name}_ch${s.channel}`;
    const n = (counts.get(base) ?? 0) + 1;
    counts.set(base, n);
    return { sensor: s, key: n === 1 ? base : `${base}_${n}`, occurrence: n };
  });
}

const TOOLTIP_STYLE = {
  contentStyle: {
    backgroundColor: 'hsl(var(--popover))',
    border: '1px solid hsl(var(--border))',
    borderRadius: '6px',
    fontSize: '11px',
    color: 'hsl(var(--popover-foreground))',
  },
  itemStyle: { color: 'hsl(var(--popover-foreground))' },
  labelStyle: { color: 'hsl(var(--muted-foreground))' },
} as const;

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatUptime(seconds: number): string {
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

interface TelemetryHistoryPaneProps {
  entries: TelemetryHistoryEntry[];
  publicKey: string;
  contacts: Contact[];
  trackedTelemetryRepeaters: string[];
  onToggleTrackedTelemetry: (publicKey: string) => Promise<void>;
}

export function TelemetryHistoryPane({
  entries,
  publicKey,
  contacts,
  trackedTelemetryRepeaters,
  onToggleTrackedTelemetry,
}: TelemetryHistoryPaneProps) {
  const { distanceUnit } = useDistanceUnit();
  const [metric, setMetric] = useState<string>('battery_volts');
  const [toggling, setToggling] = useState(false);

  const isTracked = trackedTelemetryRepeaters.includes(publicKey);
  const slotsFull = trackedTelemetryRepeaters.length >= MAX_TRACKED && !isTracked;

  // Discover unique LPP sensors across all history entries
  const lppMetrics = useMemo(() => {
    const seen = new Map<string, { type_name: string; channel: number; occurrence: number }>();
    for (const e of entries) {
      for (const { sensor: s, key: k, occurrence } of assignLppKeys(e.data.lpp_sensors ?? [])) {
        if (!seen.has(k)) seen.set(k, { type_name: s.type_name, channel: s.channel, occurrence });
      }
    }
    const result: { key: string; config: MetricConfig; type_name: string; channel: number }[] = [];
    let colorIdx = 0;
    for (const [k, info] of seen) {
      const label =
        info.type_name.charAt(0).toUpperCase() +
        info.type_name.slice(1).replace(/_/g, ' ') +
        ` Ch${info.channel}` +
        (info.occurrence > 1 ? ` (${info.occurrence})` : '');
      const { unit } = lppDisplayUnit(info.type_name, 0, distanceUnit);
      result.push({
        key: k,
        config: { label, unit, color: LPP_COLORS[colorIdx % LPP_COLORS.length] },
        type_name: info.type_name,
        channel: info.channel,
      });
      colorIdx++;
    }
    return result;
  }, [entries, distanceUnit]);

  const allMetricKeys = useMemo(
    () => [...BUILTIN_METRICS, ...lppMetrics.map((m) => m.key)],
    [lppMetrics]
  );

  // If the selected metric disappears (e.g. different repeater), reset to default
  const activeMetric = allMetricKeys.includes(metric) ? metric : 'battery_volts';

  const isBuiltin = BUILTIN_METRICS.includes(activeMetric as BuiltinMetric);
  const activeConfig: MetricConfig = isBuiltin
    ? BUILTIN_METRIC_CONFIG[activeMetric as BuiltinMetric]
    : (lppMetrics.find((m) => m.key === activeMetric)?.config ?? {
        label: activeMetric,
        unit: '',
        color: '#888',
      });

  const chartData = useMemo(() => {
    return entries.map((e) => {
      const d = e.data;
      const recvErrors = d.recv_errors ?? undefined;
      const packetsReceived = d.packets_received;
      const point: Record<string, number | undefined> = {
        timestamp: e.timestamp,
        battery_volts: d.battery_volts,
        noise_floor_dbm: d.noise_floor_dbm,
        packets_received: packetsReceived,
        packets_sent: d.packets_sent,
        recv_errors: recvErrors,
        recv_error_pct:
          recvErrors != null && packetsReceived != null && packetsReceived + recvErrors > 0
            ? +((recvErrors / (packetsReceived + recvErrors)) * 100).toFixed(2)
            : undefined,
        uptime_seconds: d.uptime_seconds,
      };
      // Flatten LPP sensors into the point, converting units as needed
      for (const { sensor: s, key } of assignLppKeys(d.lpp_sensors ?? [])) {
        if (typeof s.value === 'number') {
          point[key] = lppDisplayUnit(s.type_name, s.value, distanceUnit).value;
        }
      }
      return point;
    });
  }, [entries, distanceUnit]);

  const dataKeys =
    activeMetric === 'packets'
      ? ['packets_received', 'packets_sent']
      : activeMetric === 'recv_errors'
        ? ['recv_errors', 'recv_error_pct']
        : [activeMetric];

  const yDomain = useMemo<[number, number] | undefined>(() => {
    if (activeMetric !== 'battery_volts' || chartData.length === 0) return undefined;
    const values = chartData.map((d) => d.battery_volts).filter((v) => v != null) as number[];
    if (values.length === 0) return [3, 5];
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    return [Math.min(3, Math.floor(lo) - 1), Math.max(5, Math.ceil(hi) + 1)];
  }, [activeMetric, chartData]);

  const yDomainPct = useMemo<[number, number]>(() => {
    const MIN_SPAN = 5;
    const values = chartData.map((d) => d.recv_error_pct).filter((v) => v != null) as number[];
    if (values.length === 0) return [0, MIN_SPAN];
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const span = hi - lo;
    if (span >= MIN_SPAN)
      return [Math.max(0, Math.floor(lo - span * 0.1)), Math.ceil(hi + span * 0.1)];
    const pad = (MIN_SPAN - span) / 2;
    const bottom = Math.max(0, Math.floor(lo - pad));
    return [bottom, Math.ceil(bottom + MIN_SPAN)];
  }, [chartData]);

  const handleToggle = async () => {
    setToggling(true);
    try {
      await onToggleTrackedTelemetry(publicKey);
    } finally {
      setToggling(false);
    }
  };

  const trackedNames = useMemo(() => {
    if (!slotsFull) return [];
    return trackedTelemetryRepeaters.map((key) => {
      const contact = contacts.find((c) => c.public_key === key);
      return { key, name: contact?.name ?? key.slice(0, 12) };
    });
  }, [slotsFull, trackedTelemetryRepeaters, contacts]);

  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 bg-muted/50 border-b border-border">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-medium">Telemetry History</h3>
          {entries.length > 0 && (
            <span className="text-[0.625rem] text-muted-foreground">{entries.length} samples</span>
          )}
        </div>
      </div>
      <div className="p-3">
        {/* Explanation + tracking toggle */}
        <div className="mb-3 space-y-3">
          <p className="text-xs text-muted-foreground leading-relaxed">
            Any time repeater telemetry is fetched, the metrics are stored for 30 days (or 1,000
            samples, whichever comes first). This telemetry is stored on normal interactive fetches
            via the repeater pane, API calls to the endpoint (
            <code className="text-[0.6875rem]">POST /api/contacts/&lt;key&gt;/repeater/status</code>
            ), or when the repeater is opted into interval telemetry polling, in which case the
            repeater will be polled for metrics automatically. Fetch frequency can be configured in{' '}
            <a
              href="#settings/radio-app"
              className="underline text-primary hover:text-primary/80 transition-colors"
            >
              Settings &rarr; Radio-App Management
            </a>
            , where you can also see which repeaters are currently opted in. A maximum of{' '}
            {MAX_TRACKED} repeaters may be opted into this for the sake of keeping mesh congestion
            reasonable.
          </p>

          {isTracked ? (
            <Button
              variant="outline"
              onClick={handleToggle}
              disabled={toggling}
              className="border-destructive/50 text-destructive hover:bg-destructive/10"
            >
              {toggling ? 'Updating...' : 'Remove Repeater from Interval Metrics Tracking'}
            </Button>
          ) : slotsFull ? (
            <div className="space-y-2">
              <Button variant="outline" disabled>
                Tracking Full ({trackedTelemetryRepeaters.length}/{MAX_TRACKED} slots used)
              </Button>
              <p className="text-xs text-muted-foreground">
                Disable tracking on another repeater to free a slot:{' '}
                {trackedNames.map((t) => t.name).join(', ')}
              </p>
            </div>
          ) : (
            <Button
              variant="outline"
              onClick={handleToggle}
              disabled={toggling}
              className="border-green-600/50 text-green-600 hover:bg-green-600/10"
            >
              {toggling ? 'Updating...' : 'Opt Repeater into Interval Metrics Tracking'}
            </Button>
          )}
        </div>

        <Separator className="mb-3" />

        {/* Metric selector */}
        <div className="flex flex-wrap gap-1 mb-2">
          {BUILTIN_METRICS.map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMetric(m)}
              className={cn(
                'text-[0.6875rem] px-2 py-0.5 rounded transition-colors',
                activeMetric === m
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:text-foreground hover:bg-accent'
              )}
            >
              {BUILTIN_METRIC_CONFIG[m].label}
            </button>
          ))}
          {lppMetrics.map((m) => (
            <button
              key={m.key}
              type="button"
              onClick={() => setMetric(m.key)}
              className={cn(
                'text-[0.6875rem] px-2 py-0.5 rounded transition-colors',
                activeMetric === m.key
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:text-foreground hover:bg-accent'
              )}
            >
              {m.config.label}
            </button>
          ))}
        </div>

        {entries.length === 0 ? (
          <p className="text-sm text-muted-foreground italic">
            No history yet. Fetch status above to record data points.
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart
              data={chartData}
              margin={{
                top: 4,
                right: activeMetric === 'recv_errors' ? 8 : 4,
                bottom: 0,
                left: -8,
              }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
              <XAxis
                dataKey="timestamp"
                type="number"
                domain={['dataMin', 'dataMax']}
                tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
                tickLine={false}
                axisLine={false}
                tickFormatter={formatTime}
              />
              <YAxis
                yAxisId="left"
                domain={yDomain}
                tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v) =>
                  activeMetric === 'uptime_seconds' ? formatUptime(v) : `${v}`
                }
              />
              {activeMetric === 'recv_errors' && (
                <YAxis
                  yAxisId="right"
                  orientation="right"
                  domain={yDomainPct}
                  tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v) => `${v}%`}
                />
              )}
              <RechartsTooltip
                {...TOOLTIP_STYLE}
                cursor={{
                  stroke: 'hsl(var(--muted-foreground))',
                  strokeWidth: 1,
                  strokeDasharray: '3 3',
                }}
                labelFormatter={(ts) => formatTime(Number(ts))}
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                formatter={(value: any, name: any) => {
                  const numVal = typeof value === 'number' ? value : Number(value);
                  if (activeMetric === 'recv_errors') {
                    if (name === 'recv_error_pct') return [`${numVal}%`, 'Error Rate'];
                    return [`${value}`, 'RX Errors'];
                  }
                  const display =
                    activeMetric === 'uptime_seconds' ? formatUptime(numVal) : `${value}`;
                  const suffix =
                    activeMetric === 'uptime_seconds'
                      ? ''
                      : activeConfig.unit
                        ? ` ${activeConfig.unit}`
                        : '';
                  const label =
                    activeMetric === 'packets'
                      ? name === 'packets_received'
                        ? 'Received'
                        : 'Sent'
                      : activeConfig.label;
                  return [`${display}${suffix}`, label];
                }}
              />
              {dataKeys.map((key, i) => {
                const color =
                  activeMetric === 'packets'
                    ? i === 0
                      ? '#0ea5e9'
                      : '#f43f5e'
                    : activeMetric === 'recv_errors'
                      ? i === 0
                        ? '#ef4444'
                        : '#f59e0b'
                      : activeConfig.color;
                return (
                  <Area
                    key={key}
                    type="linear"
                    dataKey={key}
                    yAxisId={
                      activeMetric === 'recv_errors' && key === 'recv_error_pct' ? 'right' : 'left'
                    }
                    stroke={color}
                    fill={color}
                    fillOpacity={0.15}
                    strokeWidth={1.5}
                    dot={{
                      r: 4,
                      fill: color,
                      strokeWidth: 1.5,
                      stroke: 'hsl(var(--popover))',
                    }}
                    activeDot={{
                      r: 6,
                      fill: color,
                      strokeWidth: 2,
                      stroke: 'hsl(var(--popover))',
                    }}
                  />
                );
              })}
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
