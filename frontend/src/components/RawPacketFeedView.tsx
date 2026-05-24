import { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts';

import { MeshCoreDecoder, Utils } from '@michaelhart/meshcore-decoder';

import { RawPacketList } from './RawPacketList';
import { RawPacketInspectorDialog } from './RawPacketDetailModal';
import { Button } from './ui/button';
import type { Channel, Contact, RawPacket } from '../types';
import {
  KNOWN_PAYLOAD_TYPES,
  RAW_PACKET_STATS_WINDOWS,
  buildRawPacketStatsSnapshot,
  type NeighborStat,
  type PacketTimelineBin,
  type RankedPacketStat,
  type RawPacketStatsSessionState,
  type RawPacketStatsWindow,
} from '../utils/rawPacketStats';
import { createDecoderOptions } from '../utils/rawPacketInspector';
import { getContactDisplayName } from '../utils/pubkey';
import { cn } from '@/lib/utils';

const TIMELINE_FILL_COLORS = ['#0ea5e9', '#10b981', '#f59e0b', '#f43f5e', '#8b5cf6'];

/**
 * Build a stable name→color mapping so the same type always gets the same
 * color regardless of sort order or appearance order.
 */
function buildColorMap(names: readonly string[]): Map<string, string> {
  const map = new Map<string, string>();
  for (let i = 0; i < names.length; i++) {
    map.set(names[i], TIMELINE_FILL_COLORS[i % TIMELINE_FILL_COLORS.length]);
  }
  return map;
}

function colorForIndex(index: number, colorMap?: Map<string, string>, name?: string): string {
  if (colorMap && name && colorMap.has(name)) {
    return colorMap.get(name)!;
  }
  return TIMELINE_FILL_COLORS[index % TIMELINE_FILL_COLORS.length];
}

const KNOWN_PAYLOAD_TYPE_SET = new Set<string>(KNOWN_PAYLOAD_TYPES);
const PAYLOAD_TYPE_COLOR_MAP = buildColorMap(KNOWN_PAYLOAD_TYPES);

function getPacketTypeName(
  packet: RawPacket,
  decoderOptions?: ReturnType<typeof createDecoderOptions>
): string {
  try {
    const decoded = MeshCoreDecoder.decode(packet.data, decoderOptions);
    if (!decoded.isValid) return 'Unknown';
    const name = Utils.getPayloadTypeName(decoded.payloadType);
    return KNOWN_PAYLOAD_TYPE_SET.has(name) ? name : 'Unknown';
  } catch {
    return 'Unknown';
  }
}

interface RawPacketFeedViewProps {
  packets: RawPacket[];
  rawPacketStatsSession: RawPacketStatsSessionState;
  contacts: Contact[];
  channels: Channel[];
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

const WINDOW_LABELS: Record<RawPacketStatsWindow, string> = {
  '1m': '1 min',
  '5m': '5 min',
  '10m': '10 min',
  '30m': '30 min',
  session: 'Session',
};

function formatTimestamp(timestampMs: number): string {
  return new Date(timestampMs).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${Math.max(1, Math.round(seconds))} sec`;
  }
  if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    const remainder = Math.round(seconds % 60);
    return remainder > 0 ? `${minutes}m ${remainder}s` : `${minutes}m`;
  }
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
}

function formatRate(value: number): string {
  if (value >= 100) return value.toFixed(0);
  if (value >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formatRssi(value: number | null): string {
  return value === null ? '-' : `${Math.round(value)} dBm`;
}

function normalizeResolvableSourceKey(sourceKey: string): string {
  return sourceKey.startsWith('hash1:') ? sourceKey.slice(6) : sourceKey;
}

function resolveContact(sourceKey: string | null, contacts: Contact[]): Contact | null {
  if (!sourceKey || sourceKey.startsWith('name:')) {
    return null;
  }

  const normalizedSourceKey = normalizeResolvableSourceKey(sourceKey).toLowerCase();
  const matches = contacts.filter((contact) =>
    contact.public_key.toLowerCase().startsWith(normalizedSourceKey)
  );
  if (matches.length !== 1) {
    return null;
  }

  return matches[0];
}

function resolveContactLabel(sourceKey: string | null, contacts: Contact[]): string | null {
  const contact = resolveContact(sourceKey, contacts);
  if (!contact) {
    return null;
  }
  return getContactDisplayName(contact.name, contact.public_key, contact.last_advert);
}

function resolveNeighbor(item: NeighborStat, contacts: Contact[]): NeighborStat {
  return {
    ...item,
    label: resolveContactLabel(item.key, contacts) ?? item.label,
  };
}

function mergeResolvedNeighbors(items: NeighborStat[], contacts: Contact[]): NeighborStat[] {
  const merged = new Map<string, NeighborStat>();

  for (const item of items) {
    const contact = resolveContact(item.key, contacts);
    const canonicalKey = contact?.public_key ?? item.key;
    const resolvedLabel =
      contact != null
        ? getContactDisplayName(contact.name, contact.public_key, contact.last_advert)
        : item.label;
    const existing = merged.get(canonicalKey);

    if (!existing) {
      merged.set(canonicalKey, {
        ...item,
        key: canonicalKey,
        label: resolvedLabel,
      });
      continue;
    }

    existing.count += item.count;
    existing.lastSeen = Math.max(existing.lastSeen, item.lastSeen);
    existing.bestRssi =
      existing.bestRssi === null
        ? item.bestRssi
        : item.bestRssi === null
          ? existing.bestRssi
          : Math.max(existing.bestRssi, item.bestRssi);
    existing.label = resolvedLabel;
  }

  return Array.from(merged.values());
}

function isNeighborIdentityResolvable(item: NeighborStat, contacts: Contact[]): boolean {
  if (item.key.startsWith('name:')) {
    return true;
  }
  return resolveContact(item.key, contacts) !== null;
}

function formatStrongestNeighborDetail(
  stats: ReturnType<typeof buildRawPacketStatsSnapshot>,
  contacts: Contact[]
): string | undefined {
  const strongestNeighbor = stats.strongestNeighbors[0];
  if (!strongestNeighbor || strongestNeighbor.bestRssi === null) {
    return undefined;
  }

  const resolvedNeighbor = resolveNeighbor(strongestNeighbor, contacts);
  return `${formatRssi(resolvedNeighbor.bestRssi)} best heard`;
}

function getCoverageMessage(
  stats: ReturnType<typeof buildRawPacketStatsSnapshot>,
  session: RawPacketStatsSessionState
): { tone: 'default' | 'warning'; message: string } {
  if (session.trimmedObservationCount > 0 && stats.window === 'session') {
    return {
      tone: 'warning',
      message: `Detailed session history was trimmed after ${session.totalObservedPackets.toLocaleString()} observations.`,
    };
  }

  if (!stats.windowFullyCovered) {
    return {
      tone: 'warning',
      message: `This window is only covered for ${formatDuration(stats.coverageSeconds)} of frontend-collected history.`,
    };
  }

  return {
    tone: 'default',
    message: `Tracking ${session.observations.length.toLocaleString()} detailed observations from this browser session.`,
  };
}

function StatTile({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="break-inside-avoid rounded-lg border border-border/70 bg-card/80 p-3">
      <div className="text-[0.625rem] uppercase tracking-wider font-medium text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 text-xl font-semibold tabular-nums text-foreground">{value}</div>
      {detail ? <div className="mt-1 text-xs text-muted-foreground">{detail}</div> : null}
    </div>
  );
}

function RankedBars({
  title,
  items,
  emptyLabel,
  formatter,
  colorMap,
}: {
  title: string;
  items: RankedPacketStat[];
  emptyLabel: string;
  formatter?: (item: RankedPacketStat) => string;
  colorMap?: Map<string, string>;
}) {
  const data = items.map((item) => ({
    name: item.label,
    value: item.count,
    detail: formatter
      ? formatter(item)
      : `${item.count.toLocaleString()} · ${formatPercent(item.share)}`,
  }));

  return (
    <section className="mb-4 break-inside-avoid rounded-lg border border-border/70 bg-card/70 p-3">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {items.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">{emptyLabel}</p>
      ) : (
        <div className="mt-2">
          <ResponsiveContainer width="100%" height={items.length * 28 + 8}>
            <BarChart
              data={data}
              layout="vertical"
              margin={{ top: 0, right: 4, bottom: 0, left: 0 }}
              barCategoryGap="20%"
            >
              <XAxis type="number" hide />
              <YAxis
                type="category"
                dataKey="name"
                tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                tickLine={false}
                axisLine={false}
                width={80}
              />
              <RechartsTooltip
                {...TOOLTIP_STYLE}
                cursor={{ fill: 'hsl(var(--muted))', opacity: 0.5 }}
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                formatter={(_v: any, _n: any, props: any) => [props.payload.detail, null]}
              />
              <Bar dataKey="value" radius={[0, 4, 4, 0]} maxBarSize={16}>
                {data.map((entry, i) => (
                  <Cell key={i} fill={colorForIndex(i, colorMap, entry.name)} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}

function NeighborList({
  title,
  items,
  emptyLabel,
  mode,
  contacts,
}: {
  title: string;
  items: NeighborStat[];
  emptyLabel: string;
  mode: 'heard' | 'signal' | 'recent';
  contacts: Contact[];
}) {
  const mergedItems = mergeResolvedNeighbors(items, contacts);
  const sortedItems = [...mergedItems].sort((a, b) => {
    if (mode === 'heard') {
      return b.count - a.count || b.lastSeen - a.lastSeen || a.label.localeCompare(b.label);
    }
    if (mode === 'signal') {
      return (
        (b.bestRssi ?? Number.NEGATIVE_INFINITY) - (a.bestRssi ?? Number.NEGATIVE_INFINITY) ||
        b.count - a.count ||
        a.label.localeCompare(b.label)
      );
    }
    return b.lastSeen - a.lastSeen || b.count - a.count || a.label.localeCompare(b.label);
  });

  return (
    <section className="mb-4 break-inside-avoid rounded-lg border border-border/70 bg-card/70 p-3">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {sortedItems.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">{emptyLabel}</p>
      ) : (
        <div className="mt-3 space-y-2">
          {sortedItems.map((item) => (
            <div
              key={item.key}
              className="flex items-center justify-between gap-3 rounded-md bg-background/70 px-2 py-1.5"
            >
              <div className="min-w-0">
                <div className="truncate text-sm text-foreground">{item.label}</div>
                <div className="text-xs text-muted-foreground">
                  {mode === 'heard'
                    ? `${item.count.toLocaleString()} packets`
                    : mode === 'signal'
                      ? `${formatRssi(item.bestRssi)} best`
                      : `Last seen ${new Date(item.lastSeen * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`}
                </div>
                {!isNeighborIdentityResolvable(item, contacts) ? (
                  <div className="text-[0.6875rem] text-warning">Identity not resolvable</div>
                ) : null}
              </div>
              {mode !== 'signal' ? (
                <div className="shrink-0 text-xs tabular-nums text-muted-foreground">
                  {mode === 'recent' ? formatRssi(item.bestRssi) : formatRssi(item.bestRssi)}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function TimelineChart({
  bins,
  colorMap,
}: {
  bins: PacketTimelineBin[];
  colorMap: Map<string, string>;
}) {
  const typeOrder = Array.from(new Set(bins.flatMap((bin) => Object.keys(bin.countsByType)))).slice(
    0,
    TIMELINE_FILL_COLORS.length
  );

  const data = bins.map((bin) => {
    const entry: Record<string, string | number> = { label: bin.label };
    for (const type of typeOrder) {
      entry[type] = bin.countsByType[type] ?? 0;
    }
    return entry;
  });

  return (
    <section className="mb-4 break-inside-avoid rounded-lg border border-border/70 bg-card/70 p-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-foreground">Traffic Timeline</h3>
        <div className="flex flex-wrap justify-end gap-2 text-[0.6875rem] text-muted-foreground">
          {typeOrder.map((type) => (
            <span key={type} className="inline-flex items-center gap-1">
              <span
                className="h-2 w-2 rounded-full"
                style={{ backgroundColor: colorMap.get(type) ?? TIMELINE_FILL_COLORS[0] }}
              />
              <span>{type}</span>
            </span>
          ))}
        </div>
      </div>
      <div className="mt-2">
        <ResponsiveContainer width="100%" height={110}>
          <BarChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: -24 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
              tickLine={false}
              axisLine={false}
            />
            <YAxis
              tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
              tickLine={false}
              axisLine={false}
              allowDecimals={false}
            />
            <RechartsTooltip
              {...TOOLTIP_STYLE}
              cursor={{ fill: 'hsl(var(--muted))', opacity: 0.5 }}
            />
            {typeOrder.map((type, i) => (
              <Bar
                key={type}
                dataKey={type}
                stackId="packets"
                fill={colorMap.get(type) ?? TIMELINE_FILL_COLORS[0]}
                radius={i === typeOrder.length - 1 ? [2, 2, 0, 0] : undefined}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

export function RawPacketFeedView({
  packets,
  rawPacketStatsSession,
  contacts,
  channels,
}: RawPacketFeedViewProps) {
  const [statsOpen, setStatsOpen] = useState(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(min-width: 768px)').matches
      : false
  );
  const [selectedWindow, setSelectedWindow] = useState<RawPacketStatsWindow>('10m');
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));
  const [selectedPacket, setSelectedPacket] = useState<RawPacket | null>(null);
  const [analyzeModalOpen, setAnalyzeModalOpen] = useState(false);
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);
  const [enabledTypes, setEnabledTypes] = useState<Set<string>>(() => new Set(KNOWN_PAYLOAD_TYPES));

  const decoderOptions = useMemo(() => createDecoderOptions(channels), [channels]);

  const packetsWithTypes = useMemo(
    () =>
      packets.map((packet) => ({
        packet,
        payloadType: getPacketTypeName(packet, decoderOptions),
      })),
    [packets, decoderOptions]
  );

  const allTypesEnabled = enabledTypes.size === KNOWN_PAYLOAD_TYPES.length;

  const filteredPackets = useMemo(() => {
    if (allTypesEnabled) return packets;
    return packetsWithTypes
      .filter(({ payloadType }) => enabledTypes.has(payloadType))
      .map(({ packet }) => packet);
  }, [packetsWithTypes, enabledTypes, packets, allTypesEnabled]);

  const handleToggleAll = () => {
    setEnabledTypes(allTypesEnabled ? new Set() : new Set(KNOWN_PAYLOAD_TYPES));
  };

  const handleToggleType = (type: string) => {
    setEnabledTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) {
        next.delete(type);
      } else {
        next.add(type);
      }
      return next;
    });
  };

  const handleOnly = (type: string) => {
    setEnabledTypes(new Set([type]));
  };

  useEffect(() => {
    const interval = window.setInterval(() => {
      setNowSec(Math.floor(Date.now() / 1000));
    }, 30000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    setNowSec(Math.floor(Date.now() / 1000));
  }, [packets, rawPacketStatsSession]);

  const stats = useMemo(
    () => buildRawPacketStatsSnapshot(rawPacketStatsSession, selectedWindow, nowSec),
    [nowSec, rawPacketStatsSession, selectedWindow]
  );
  const coverageMessage = getCoverageMessage(stats, rawPacketStatsSession);
  const strongestNeighbor = useMemo(() => {
    const topNeighbor = stats.strongestNeighbors[0];
    return topNeighbor ? resolveNeighbor(topNeighbor, contacts) : null;
  }, [contacts, stats]);

  const strongestNeighborDetail = useMemo(
    () => formatStrongestNeighborDetail(stats, contacts),
    [contacts, stats]
  );
  const strongestNeighbors = useMemo(
    () => stats.strongestNeighbors.map((item) => resolveNeighbor(item, contacts)),
    [contacts, stats.strongestNeighbors]
  );
  const mostActiveNeighbors = useMemo(
    () => stats.mostActiveNeighbors.map((item) => resolveNeighbor(item, contacts)),
    [contacts, stats.mostActiveNeighbors]
  );
  const newestNeighbors = useMemo(
    () => stats.newestNeighbors.map((item) => resolveNeighbor(item, contacts)),
    [contacts, stats.newestNeighbors]
  );
  return (
    <>
      <div className="border-b border-border px-4 py-2.5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold text-base text-foreground">Raw Packet Feed</h2>
            <p className="hidden md:block text-xs text-muted-foreground">
              Collecting stats since {formatTimestamp(rawPacketStatsSession.sessionStartedAt)}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setAnalyzeModalOpen(true)}
            >
              Analyze Packet
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setStatsOpen((current) => !current)}
              aria-expanded={statsOpen}
            >
              {statsOpen ? (
                <ChevronRight className="h-4 w-4" />
              ) : (
                <ChevronLeft className="h-4 w-4" />
              )}
              {statsOpen ? 'Hide Stats' : 'Show Stats'}
            </Button>
          </div>
        </div>
        <p className="md:hidden text-xs text-muted-foreground">
          Collecting stats since {formatTimestamp(rawPacketStatsSession.sessionStartedAt)}
          {!mobileFiltersOpen && (
            <>
              {' · '}
              <button
                type="button"
                className="text-primary hover:text-primary/80 transition-colors"
                onClick={() => setMobileFiltersOpen(true)}
              >
                Show Filters
              </button>
            </>
          )}
        </p>

        {mobileFiltersOpen && (
          <div className="mt-1.5 md:hidden flex flex-wrap items-center gap-x-3 gap-y-1">
            <label className="flex items-center gap-1 text-xs text-muted-foreground cursor-pointer">
              <input
                type="checkbox"
                checked={allTypesEnabled}
                onChange={handleToggleAll}
                className="rounded"
              />
              All
            </label>
            {KNOWN_PAYLOAD_TYPES.map((type) => (
              <span key={type} className="inline-flex items-center gap-1 text-xs">
                <label className="flex items-center gap-1 text-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    checked={enabledTypes.has(type)}
                    onChange={() => handleToggleType(type)}
                    className="rounded"
                  />
                  {type}
                </label>
                <button
                  type="button"
                  className="text-[0.625rem] text-muted-foreground hover:text-primary transition-colors"
                  onClick={() => handleOnly(type)}
                >
                  (only)
                </button>
              </span>
            ))}
          </div>
        )}

        <div className="mt-1.5 hidden md:flex flex-wrap items-center gap-x-3 gap-y-1">
          <label className="flex items-center gap-1 text-xs text-muted-foreground cursor-pointer">
            <input
              type="checkbox"
              checked={allTypesEnabled}
              onChange={handleToggleAll}
              className="rounded"
            />
            All
          </label>
          {KNOWN_PAYLOAD_TYPES.map((type) => (
            <span key={type} className="inline-flex items-center gap-1 text-xs">
              <label className="flex items-center gap-1 text-foreground cursor-pointer">
                <input
                  type="checkbox"
                  checked={enabledTypes.has(type)}
                  onChange={() => handleToggleType(type)}
                  className="rounded"
                />
                {type}
              </label>
              <button
                type="button"
                className="text-[0.625rem] text-muted-foreground hover:text-primary transition-colors"
                onClick={() => handleOnly(type)}
              >
                (only)
              </button>
            </span>
          ))}
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <div className={cn('min-h-0 min-w-0 flex-1', statsOpen && 'md:border-r md:border-border')}>
          <RawPacketList
            packets={filteredPackets}
            channels={channels}
            onPacketClick={setSelectedPacket}
          />
        </div>

        <aside
          className={cn(
            'shrink-0 overflow-hidden border-t border-border transition-all duration-300 md:border-l md:border-t-0',
            statsOpen
              ? 'max-h-[42rem] md:max-h-none md:w-1/2 md:min-w-[30rem]'
              : 'max-h-0 md:w-0 md:min-w-0 border-transparent'
          )}
        >
          {statsOpen ? (
            <div className="h-full overflow-y-auto bg-background p-4 [contain:layout_paint]">
              <div className="break-inside-avoid rounded-lg border border-border/70 bg-card/70 p-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="text-[0.625rem] uppercase tracking-wider font-medium text-muted-foreground">
                      Coverage
                    </div>
                    <div
                      className={cn(
                        'mt-1 text-sm',
                        coverageMessage.tone === 'warning'
                          ? 'text-warning'
                          : 'text-muted-foreground'
                      )}
                    >
                      {coverageMessage.message}
                    </div>
                  </div>
                  <label className="flex items-center gap-2 text-sm text-foreground">
                    <span className="text-muted-foreground">Window</span>
                    <select
                      value={selectedWindow}
                      onChange={(event) =>
                        setSelectedWindow(event.target.value as RawPacketStatsWindow)
                      }
                      className="rounded-md border border-input bg-background px-2 py-1 text-sm"
                      aria-label="Stats window"
                    >
                      {RAW_PACKET_STATS_WINDOWS.map((option) => (
                        <option key={option} value={option}>
                          {WINDOW_LABELS[option]}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <div className="mt-2 text-xs text-muted-foreground">
                  {stats.packetCount.toLocaleString()} packets in{' '}
                  {WINDOW_LABELS[selectedWindow].toLowerCase()} window
                  {' · '}
                  {rawPacketStatsSession.totalObservedPackets.toLocaleString()} observed this
                  session
                </div>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3">
                <StatTile
                  label="Packets / min"
                  value={formatRate(stats.packetsPerMinute)}
                  detail={`${stats.packetCount.toLocaleString()} total in window`}
                />
                <StatTile
                  label="Unique Sources"
                  value={stats.uniqueSources.toLocaleString()}
                  detail="Distinct identified senders"
                />
                <StatTile
                  label="Decrypt Rate"
                  value={formatPercent(stats.decryptRate)}
                  detail={`${stats.decryptedCount.toLocaleString()} decrypted / ${stats.undecryptedCount.toLocaleString()} locked`}
                />
                <StatTile
                  label="Path Diversity"
                  value={stats.distinctPaths.toLocaleString()}
                  detail={`${formatPercent(stats.pathBearingRate)} path-bearing packets`}
                />
                <StatTile
                  label="Strongest Neighbor"
                  value={strongestNeighbor?.label ?? '-'}
                  detail={strongestNeighborDetail ?? 'No neighbor RSSI sample in window'}
                />
                <StatTile
                  label="Median RSSI"
                  value={formatRssi(stats.medianRssi)}
                  detail={
                    stats.averageRssi === null
                      ? 'No signal sample in window'
                      : `Average ${formatRssi(stats.averageRssi)}`
                  }
                />
              </div>

              <div className="mt-4">
                <TimelineChart bins={stats.timeline} colorMap={PAYLOAD_TYPE_COLOR_MAP} />
              </div>

              <div className="md:columns-2 md:gap-4">
                <RankedBars
                  title="Packet Types"
                  items={stats.payloadBreakdown}
                  emptyLabel="No packets in this window yet."
                  colorMap={PAYLOAD_TYPE_COLOR_MAP}
                />

                <RankedBars
                  title="Route Mix"
                  items={stats.routeBreakdown}
                  emptyLabel="No packets in this window yet."
                />

                <RankedBars
                  title="Hop Profile"
                  items={stats.hopProfile}
                  emptyLabel="No packets in this window yet."
                />

                <RankedBars
                  title="Hop Byte Width"
                  items={stats.hopByteWidthProfile}
                  emptyLabel="No packets in this window yet."
                />

                <RankedBars
                  title="Signal Distribution"
                  items={stats.rssiBuckets}
                  emptyLabel="No RSSI samples in this window yet."
                />

                <NeighborList
                  title="Most-Heard Neighbors"
                  items={mostActiveNeighbors}
                  emptyLabel="No sender identities resolved in this window yet."
                  mode="heard"
                  contacts={contacts}
                />

                <NeighborList
                  title="Strongest Recent Neighbors"
                  items={strongestNeighbors}
                  emptyLabel="No RSSI-tagged neighbors in this window yet."
                  mode="signal"
                  contacts={contacts}
                />

                <NeighborList
                  title="Newest Heard Neighbors"
                  items={newestNeighbors}
                  emptyLabel="No newly identified neighbors in this window yet."
                  mode="recent"
                  contacts={contacts}
                />
              </div>
            </div>
          ) : null}
        </aside>
      </div>

      <RawPacketInspectorDialog
        open={selectedPacket !== null}
        onOpenChange={(isOpen) => !isOpen && setSelectedPacket(null)}
        channels={channels}
        source={
          selectedPacket
            ? { kind: 'packet', packet: selectedPacket }
            : { kind: 'loading', message: 'Loading packet...' }
        }
        title="Packet Details"
        description="Detailed byte and field breakdown for the selected raw packet."
      />

      <RawPacketInspectorDialog
        open={analyzeModalOpen}
        onOpenChange={setAnalyzeModalOpen}
        channels={channels}
        source={{ kind: 'paste' }}
        title="Analyze Packet"
        description="Paste and inspect a raw packet hex string."
      />
    </>
  );
}
