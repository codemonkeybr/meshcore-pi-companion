import { useState, useMemo, useEffect } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  Brush,
} from 'recharts';
import { Download } from 'lucide-react';
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

/** Collect all numeric values for the given keys across a set of chart points. */
function collectValues(data: Array<Record<string, number | undefined>>, keys: string[]): number[] {
  const out: number[] = [];
  for (const d of data) {
    for (const k of keys) {
      const v = d[k];
      if (typeof v === 'number' && Number.isFinite(v)) out.push(v);
    }
  }
  return out;
}

/** Bound a Y axis to the data range padded by 10% on each side.
 *  Returns undefined (recharts auto-domain) when there is nothing to plot. */
function paddedDomain(values: number[]): [number, number] | undefined {
  if (values.length === 0) return undefined;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo;
  // Flat series (single value / no spread): pad relative to magnitude so the
  // line doesn't sit on a degenerate zero-height axis.
  const pad = span === 0 ? Math.abs(lo) * 0.1 || 1 : span * 0.1;
  return [lo - pad, hi + pad];
}

/** Decimal places to render axis ticks at, derived from the axis span so a
 *  tightly-zoomed range (e.g. battery voltage varying in the 5th decimal)
 *  shows distinct, clean labels instead of raw floating-point tick values
 *  like "4.0487999999999996". Aims for ~5 ticks across the span. */
function tickDecimals(span: number | undefined): number {
  if (span == null || !isFinite(span) || span <= 0) return 2;
  const step = span / 5;
  return Math.min(8, Math.max(0, Math.ceil(-Math.log10(step))));
}

/** Round away floating-point noise, then drop trailing zeros so a real data
 *  value reads as e.g. "4.0488" rather than "4.0487999999999996". Integers
 *  pass through unchanged. */
function cleanNumber(value: number): string {
  if (Number.isInteger(value)) return `${value}`;
  return `${Number(value.toFixed(4))}`;
}

// --- CSV export ---

/** Strip float representation noise without clamping small magnitudes the way
 *  a fixed decimal count would (`toFixed(4)` would flatten 0.000012 to 0). */
function csvNumber(value: number): string {
  if (Number.isInteger(value)) return `${value}`;
  return `${Number(value.toPrecision(12))}`;
}

function escapeCsvValue(value: string): string {
  return /[",\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

const pad2 = (n: number) => String(n).padStart(2, '0');

/** Local-time ISO 8601 with offset, so a sample's wall-clock time survives the
 *  trip into a spreadsheet without the reader having to know our timezone. */
export function toLocalIsoString(date: Date): string {
  const offsetMin = -date.getTimezoneOffset();
  const sign = offsetMin >= 0 ? '+' : '-';
  const abs = Math.abs(offsetMin);
  return (
    `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}` +
    `T${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}` +
    `${sign}${pad2(Math.floor(abs / 60))}:${pad2(abs % 60)}`
  );
}

/** `<repeaterName>_data_YYYYMMDD_HHMMSS.csv`, reduced to filesystem-safe
 *  characters. Falls back to "repeater" when a name sanitizes away entirely. */
export function telemetryCsvFilename(repeaterName: string, at: Date): string {
  const safeName =
    repeaterName
      .replace(/[^a-zA-Z0-9_-]+/g, '_')
      .replace(/_{2,}/g, '_')
      .replace(/^_|_$/g, '') || 'repeater';
  const stamp =
    `${at.getFullYear()}${pad2(at.getMonth() + 1)}${pad2(at.getDate())}` +
    `_${pad2(at.getHours())}${pad2(at.getMinutes())}${pad2(at.getSeconds())}`;
  return `${safeName}_data_${stamp}.csv`;
}

interface CsvColumn {
  key: string;
  header: string;
}

/** Columns mirror the flattened point shape built by `chartData` below, so every
 *  metric the pane can plot — builtins, their derived series, and each
 *  discovered LPP sensor — gets a column. Keep the two in step. */
function buildCsvColumns(lppMetrics: { key: string; config: MetricConfig }[]): CsvColumn[] {
  const withUnit = (label: string, unit: string) => (unit ? `${label} (${unit})` : label);
  return [
    { key: 'timestamp_iso', header: 'Timestamp (ISO 8601)' },
    { key: 'timestamp', header: 'Unix Timestamp' },
    { key: 'battery_volts', header: withUnit('Voltage', BUILTIN_METRIC_CONFIG.battery_volts.unit) },
    {
      key: 'noise_floor_dbm',
      header: withUnit('Noise Floor', BUILTIN_METRIC_CONFIG.noise_floor_dbm.unit),
    },
    { key: 'packets_received', header: 'Packets Received' },
    { key: 'packets_sent', header: 'Packets Sent' },
    { key: 'packets_received_delta', header: 'Packets Received Delta' },
    { key: 'packets_sent_delta', header: 'Packets Sent Delta' },
    { key: 'recv_errors', header: 'RX Errors' },
    { key: 'recv_error_pct', header: 'RX Error Rate (%)' },
    {
      key: 'uptime_seconds',
      header: withUnit('Uptime', BUILTIN_METRIC_CONFIG.uptime_seconds.unit),
    },
    ...lppMetrics.map((m) => ({
      key: m.key,
      // Unit already reflects the active distance-unit preference, as the chart does.
      header: withUnit(m.config.label, m.config.unit),
    })),
  ];
}

/** Serialize chart points to CSV. Missing samples become empty cells rather
 *  than zeros, so gaps stay distinguishable from real readings. */
export function buildTelemetryCsv(
  rows: Array<Record<string, number | undefined>>,
  columns: CsvColumn[]
): string {
  const lines = [columns.map((c) => escapeCsvValue(c.header)).join(',')];
  for (const row of rows) {
    lines.push(
      columns
        .map((c) => {
          if (c.key === 'timestamp_iso') {
            const ts = row.timestamp;
            return ts == null ? '' : escapeCsvValue(toLocalIsoString(new Date(ts * 1000)));
          }
          const v = row[c.key];
          return typeof v === 'number' && Number.isFinite(v) ? csvNumber(v) : '';
        })
        .join(',')
    );
  }
  return lines.join('\r\n');
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
  const [brushRange, setBrushRange] = useState<{ start: number; end: number } | null>(null);

  // Reset the zoom window when switching to a different repeater.
  useEffect(() => {
    setBrushRange(null);
  }, [publicKey]);

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
  const activeConfig: MetricConfig = useMemo(
    () =>
      isBuiltin
        ? BUILTIN_METRIC_CONFIG[activeMetric as BuiltinMetric]
        : (lppMetrics.find((m) => m.key === activeMetric)?.config ?? {
            label: activeMetric,
            unit: '',
            color: '#888',
          }),
    [isBuiltin, activeMetric, lppMetrics]
  );

  const chartData = useMemo(() => {
    // Sort chronologically so per-sample deltas compare against the true
    // predecessor (entries are not guaranteed ordered by the API).
    const ordered = [...entries].sort((a, b) => a.timestamp - b.timestamp);
    let prevRecv: number | undefined;
    let prevSent: number | undefined;
    return ordered.map((e) => {
      const d = e.data;
      const recvErrors = d.recv_errors ?? undefined;
      const packetsReceived = d.packets_received;
      const packetsSent = d.packets_sent;
      // Per-sample deltas off the cumulative lifetime counters. A drop
      // (counter < previous) means the repeater rebooted and reset its
      // counters, so we emit no delta for that sample rather than a large
      // negative spike. The first sample has no predecessor, so no delta.
      const recvDelta =
        prevRecv != null && packetsReceived != null && packetsReceived >= prevRecv
          ? packetsReceived - prevRecv
          : undefined;
      const sentDelta =
        prevSent != null && packetsSent != null && packetsSent >= prevSent
          ? packetsSent - prevSent
          : undefined;
      if (packetsReceived != null) prevRecv = packetsReceived;
      if (packetsSent != null) prevSent = packetsSent;
      const point: Record<string, number | undefined> = {
        timestamp: e.timestamp,
        battery_volts: d.battery_volts,
        noise_floor_dbm: d.noise_floor_dbm,
        packets_received: packetsReceived,
        packets_sent: packetsSent,
        packets_received_delta: recvDelta,
        packets_sent_delta: sentDelta,
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

  // Series descriptors drive axes, colors, labels, and tooltip formatting.
  // Cumulative counters render as filled areas on the left axis; derived
  // per-sample deltas render as gapped lines on a secondary right axis.
  const series = useMemo(() => {
    if (activeMetric === 'packets') {
      return [
        {
          key: 'packets_received',
          color: '#0ea5e9',
          axis: 'left' as const,
          line: false,
          label: 'Received',
        },
        {
          key: 'packets_sent',
          color: '#f43f5e',
          axis: 'left' as const,
          line: false,
          label: 'Sent',
        },
        {
          key: 'packets_received_delta',
          color: '#14b8a6',
          axis: 'right' as const,
          line: true,
          label: 'Received Δ',
        },
        {
          key: 'packets_sent_delta',
          color: '#f59e0b',
          axis: 'right' as const,
          line: true,
          label: 'Sent Δ',
        },
      ];
    }
    if (activeMetric === 'recv_errors') {
      return [
        {
          key: 'recv_errors',
          color: '#ef4444',
          axis: 'left' as const,
          line: false,
          label: 'RX Errors',
        },
        {
          key: 'recv_error_pct',
          color: '#f59e0b',
          axis: 'right' as const,
          line: false,
          label: 'Error Rate',
        },
      ];
    }
    return [
      {
        key: activeMetric,
        color: activeConfig.color,
        axis: 'left' as const,
        line: false,
        label: activeConfig.label,
      },
    ];
  }, [activeMetric, activeConfig]);

  const leftKeys = useMemo(
    () => series.filter((s) => s.axis === 'left').map((s) => s.key),
    [series]
  );
  const rightKeys = useMemo(
    () => series.filter((s) => s.axis === 'right').map((s) => s.key),
    [series]
  );

  // Brush-controlled viewport. Indices are clamped to the current data length
  // so a stale range from a previous repeater can never index out of bounds.
  const lastIndex = Math.max(0, chartData.length - 1);
  const brushStart = brushRange ? Math.min(brushRange.start, lastIndex) : 0;
  const brushEnd = brushRange ? Math.min(brushRange.end, lastIndex) : lastIndex;

  const visibleData = useMemo(
    () => chartData.slice(brushStart, brushEnd + 1),
    [chartData, brushStart, brushEnd]
  );

  // Y extents bound to the visible window so zooming re-tightens the axis.
  const leftDomain = useMemo(
    () => paddedDomain(collectValues(visibleData, leftKeys)),
    [visibleData, leftKeys]
  );
  const rightDomain = useMemo(
    () => (rightKeys.length ? paddedDomain(collectValues(visibleData, rightKeys)) : undefined),
    [visibleData, rightKeys]
  );

  // Tick precision tracks each axis's current span so zooming into a flat
  // series (e.g. battery voltage) keeps labels clean instead of leaking
  // floating-point noise into the rendered tick text.
  const leftTickDecimals = useMemo(
    () => tickDecimals(leftDomain ? leftDomain[1] - leftDomain[0] : undefined),
    [leftDomain]
  );
  const rightTickDecimals = useMemo(
    () => tickDecimals(rightDomain ? rightDomain[1] - rightDomain[0] : undefined),
    [rightDomain]
  );

  const handleBrushChange = (range: { startIndex?: number; endIndex?: number }) => {
    if (typeof range.startIndex === 'number' && typeof range.endIndex === 'number') {
      setBrushRange({ start: range.startIndex, end: range.endIndex });
    }
  };

  const formatSeriesValue = (key: string, value: number): string => {
    if (key === 'recv_error_pct') return `${cleanNumber(value)}%`;
    if (activeMetric === 'uptime_seconds') return formatUptime(value);
    const suffix =
      activeConfig.unit && activeMetric !== 'packets' && activeMetric !== 'recv_errors'
        ? ` ${activeConfig.unit}`
        : '';
    return `${cleanNumber(value)}${suffix}`;
  };

  // Custom tooltip so each row carries a color swatch matching its line —
  // essential for the multi-series packets view where four values overlap.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const renderTooltip = ({ active, payload, label }: any) => {
    if (!active || !Array.isArray(payload) || payload.length === 0) return null;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const rows = (payload as any[]).filter((p) => p.value != null);
    if (rows.length === 0) return null;
    return (
      <div style={{ ...TOOLTIP_STYLE.contentStyle, padding: '6px 9px' }}>
        <div style={{ ...TOOLTIP_STYLE.labelStyle, marginBottom: 4 }}>
          {formatTime(Number(label))}
        </div>
        {rows.map((p) => {
          const key = String(p.dataKey ?? p.name);
          const s = series.find((x) => x.key === key);
          const color = s?.color ?? (p.color as string);
          const numVal = typeof p.value === 'number' ? p.value : Number(p.value);
          return (
            <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: 2,
                  backgroundColor: color,
                  flexShrink: 0,
                }}
              />
              <span style={TOOLTIP_STYLE.labelStyle}>{s?.label ?? key}:</span>
              <span style={{ color: 'hsl(var(--popover-foreground))' }}>
                {formatSeriesValue(key, numVal)}
              </span>
            </div>
          );
        })}
      </div>
    );
  };

  const handleToggle = async () => {
    setToggling(true);
    try {
      await onToggleTrackedTelemetry(publicKey);
    } finally {
      setToggling(false);
    }
  };

  const repeaterName = useMemo(
    () => contacts.find((c) => c.public_key === publicKey)?.name ?? publicKey.slice(0, 12),
    [contacts, publicKey]
  );

  // Exports the full stored history, not the brushed viewport — the button is
  // about archiving the data, while the brush is a chart-reading aid.
  const handleDownloadCsv = () => {
    const csv = buildTelemetryCsv(chartData, buildCsvColumns(lppMetrics));
    // Excel only detects UTF-8 in a CSV via the BOM, and LPP units include
    // non-ASCII characters such as "°C".
    const blob = new Blob(['\ufeff', csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = telemetryCsvFilename(repeaterName, new Date());
    a.click();
    URL.revokeObjectURL(url);
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
        {entries.length > 0 && (
          <Button
            variant="outline"
            size="sm"
            onClick={handleDownloadCsv}
            title="Download all telemetry history as CSV"
          >
            <Download className="h-3.5 w-3.5 mr-1.5" aria-hidden="true" />
            Download CSV
          </Button>
        )}
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
          <ResponsiveContainer width="100%" height={210}>
            <AreaChart
              data={chartData}
              margin={{
                top: 4,
                right: rightKeys.length ? 8 : 4,
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
                domain={leftDomain}
                tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v) =>
                  activeMetric === 'uptime_seconds' ? formatUptime(v) : v.toFixed(leftTickDecimals)
                }
              />
              {rightKeys.length > 0 && (
                <YAxis
                  yAxisId="right"
                  orientation="right"
                  domain={rightDomain}
                  tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v) =>
                    activeMetric === 'recv_errors'
                      ? `${v.toFixed(rightTickDecimals)}%`
                      : v.toFixed(rightTickDecimals)
                  }
                />
              )}
              <RechartsTooltip
                cursor={{
                  stroke: 'hsl(var(--muted-foreground))',
                  strokeWidth: 1,
                  strokeDasharray: '3 3',
                }}
                content={renderTooltip}
              />
              {series.map((s) => (
                <Area
                  key={s.key}
                  type="linear"
                  dataKey={s.key}
                  yAxisId={s.axis}
                  connectNulls={false}
                  stroke={s.color}
                  fill={s.color}
                  fillOpacity={s.line ? 0 : 0.15}
                  strokeWidth={1.5}
                  dot={{
                    r: 4,
                    fill: s.color,
                    strokeWidth: 1.5,
                    stroke: 'hsl(var(--popover))',
                  }}
                  activeDot={{
                    r: 6,
                    fill: s.color,
                    strokeWidth: 2,
                    stroke: 'hsl(var(--popover))',
                  }}
                />
              ))}
              {chartData.length > 2 && (
                <Brush
                  dataKey="timestamp"
                  height={22}
                  travellerWidth={8}
                  stroke="hsl(var(--muted-foreground))"
                  fill="hsl(var(--muted))"
                  tickFormatter={(ts) => formatTime(Number(ts))}
                  startIndex={brushStart}
                  endIndex={brushEnd}
                  onChange={handleBrushChange}
                />
              )}
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
