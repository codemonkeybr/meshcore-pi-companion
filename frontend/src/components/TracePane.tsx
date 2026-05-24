import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ArrowDown, ArrowUp, Plus, X } from 'lucide-react';

import type {
  Contact,
  RadioConfig,
  RadioTraceHopRequest,
  RadioTraceNode,
  RadioTraceResponse,
} from '../types';
import { CONTACT_TYPE_REPEATER } from '../types';
import { calculateDistance, formatDistance, isValidLocation } from '../utils/pathUtils';
import { useDistanceUnit } from '../contexts/DistanceUnitContext';
import { getContactDisplayName } from '../utils/pubkey';
import { handleKeyboardActivate } from '../utils/a11y';
import { ContactAvatar } from './ContactAvatar';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Input } from './ui/input';
import { cn } from '@/lib/utils';

type TraceSortMode = 'alpha' | 'recent' | 'distance';
type CustomHopBytes = 1 | 2 | 4;

const RECENT_TRACES_KEY = 'remoteterm-recent-traces';
const MAX_RECENT_TRACES = 5;

interface SavedTraceHop {
  kind: 'repeater' | 'custom';
  publicKey?: string;
  hopHex?: string;
  hopBytes?: CustomHopBytes;
  displayName: string;
}

interface SavedTrace {
  hops: SavedTraceHop[];
  ranAt: number;
}

function loadRecentTraces(): SavedTrace[] {
  try {
    const raw = localStorage.getItem(RECENT_TRACES_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.slice(0, MAX_RECENT_TRACES) : [];
  } catch {
    return [];
  }
}

function saveRecentTrace(trace: SavedTrace): void {
  try {
    const existing = loadRecentTraces();
    // Dedupe by hop signature
    const sig = trace.hops.map((h) => h.publicKey ?? h.hopHex ?? '').join(',');
    const deduped = existing.filter(
      (t) => t.hops.map((h) => h.publicKey ?? h.hopHex ?? '').join(',') !== sig
    );
    const updated = [trace, ...deduped].slice(0, MAX_RECENT_TRACES);
    localStorage.setItem(RECENT_TRACES_KEY, JSON.stringify(updated));
  } catch {
    // localStorage may be disabled
  }
}

type TraceDraftHop =
  | { id: string; kind: 'repeater'; publicKey: string }
  | { id: string; kind: 'custom'; hopHex: string; hopBytes: CustomHopBytes };

interface TracePaneProps {
  contacts: Contact[];
  config: RadioConfig | null;
  onRunTracePath: (
    hopHashBytes: CustomHopBytes,
    hops: RadioTraceHopRequest[]
  ) => Promise<RadioTraceResponse>;
}

function getHeardTimestamp(contact: Contact): number {
  return Math.max(contact.last_seen ?? 0, contact.last_advert ?? 0);
}

function getDistanceKm(contact: Contact, config: RadioConfig | null): number | null {
  if (
    !config ||
    !isValidLocation(config.lat, config.lon) ||
    !isValidLocation(contact.lat, contact.lon)
  ) {
    return null;
  }
  return calculateDistance(config.lat, config.lon, contact.lat, contact.lon);
}

function getShortKey(publicKey: string | null | undefined): string {
  if (!publicKey) return 'unknown';
  return publicKey.slice(0, 12);
}

function formatSNR(snr: number | null | undefined): string {
  if (typeof snr !== 'number' || Number.isNaN(snr)) {
    return '—';
  }
  return `${snr >= 0 ? '+' : ''}${snr.toFixed(1)} dB`;
}

function moveHop(hops: TraceDraftHop[], index: number, direction: -1 | 1): TraceDraftHop[] {
  const nextIndex = index + direction;
  if (nextIndex < 0 || nextIndex >= hops.length) {
    return hops;
  }
  const next = [...hops];
  const [item] = next.splice(index, 1);
  next.splice(nextIndex, 0, item);
  return next;
}

function normalizeCustomHopHex(value: string): string {
  return value.replace(/[^a-fA-F0-9]/g, '').toLowerCase();
}

function nextDraftHopId(prefix: string, currentLength: number): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${currentLength}`;
}

function TraceNodeRow({
  title,
  subtitle,
  meta,
  note,
  fixed = false,
  compact = false,
  actions,
  snr,
}: {
  title: string;
  subtitle: string;
  meta?: string | null;
  note?: string | null;
  fixed?: boolean;
  compact?: boolean;
  actions?: ReactNode;
  snr?: string | null;
}) {
  return (
    <div
      className={cn(
        'flex items-center rounded-md border border-border bg-background',
        compact ? 'gap-2 px-2.5 py-2' : 'gap-3 px-3 py-3'
      )}
    >
      <div
        className={cn(
          'flex h-9 w-9 items-center justify-center rounded-full border text-[0.6875rem] font-semibold uppercase tracking-wide',
          fixed
            ? 'border-primary/30 bg-primary/10 text-primary'
            : 'border-border bg-muted text-muted-foreground'
        )}
      >
        {fixed ? 'Self' : 'Hop'}
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{title}</div>
        <div className="truncate text-xs text-muted-foreground">{subtitle}</div>
        {meta ? <div className="mt-1 text-[0.6875rem] text-muted-foreground">{meta}</div> : null}
        {note ? <div className="mt-1 text-[0.6875rem] text-muted-foreground">{note}</div> : null}
      </div>
      {snr ? (
        <div className="shrink-0 text-right">
          <div className="text-[0.6875rem] text-muted-foreground">SNR</div>
          <div className="font-mono text-sm">{snr}</div>
        </div>
      ) : null}
      {actions ? <div className="ml-1 flex items-center gap-1">{actions}</div> : null}
    </div>
  );
}

export function TracePane({ contacts, config, onRunTracePath }: TracePaneProps) {
  const { distanceUnit } = useDistanceUnit();
  const [searchQuery, setSearchQuery] = useState('');
  const [sortMode, setSortMode] = useState<TraceSortMode>('alpha');
  const [draftHops, setDraftHops] = useState<TraceDraftHop[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RadioTraceResponse | null>(null);
  const [customDialogOpen, setCustomDialogOpen] = useState(false);
  const [customHopBytesDraft, setCustomHopBytesDraft] = useState<CustomHopBytes>(1);
  const [customHopHexDraft, setCustomHopHexDraft] = useState('');
  const [customHopError, setCustomHopError] = useState<string | null>(null);
  const [recentTraces, setRecentTraces] = useState<SavedTrace[]>(loadRecentTraces);
  const activeRunTokenRef = useRef(0);

  const repeaters = useMemo(() => {
    const deduped = new Map<string, Contact>();
    for (const contact of contacts) {
      if (contact.type !== CONTACT_TYPE_REPEATER || contact.public_key.length !== 64) {
        continue;
      }
      if (!deduped.has(contact.public_key)) {
        deduped.set(contact.public_key, contact);
      }
    }
    return [...deduped.values()];
  }, [contacts]);

  const repeatersByKey = useMemo(
    () => new Map(repeaters.map((contact) => [contact.public_key, contact])),
    [repeaters]
  );

  const filteredRepeaters = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    const matching = query
      ? repeaters.filter(
          (contact) =>
            (contact.name ?? '').toLowerCase().includes(query) ||
            contact.public_key.toLowerCase().startsWith(query)
        )
      : repeaters;

    return [...matching].sort((left, right) => {
      if (sortMode === 'recent') {
        const leftTs = getHeardTimestamp(left);
        const rightTs = getHeardTimestamp(right);
        if (leftTs !== rightTs) {
          return rightTs - leftTs;
        }
      }
      if (sortMode === 'distance') {
        const leftDistance = getDistanceKm(left, config);
        const rightDistance = getDistanceKm(right, config);
        if (leftDistance !== null && rightDistance !== null && leftDistance !== rightDistance) {
          return leftDistance - rightDistance;
        }
        if (leftDistance !== null && rightDistance === null) return -1;
        if (leftDistance === null && rightDistance !== null) return 1;
      }
      return getContactDisplayName(left.name, left.public_key, left.last_advert).localeCompare(
        getContactDisplayName(right.name, right.public_key, right.last_advert)
      );
    });
  }, [config, repeaters, searchQuery, sortMode]);

  const localRadioName = config?.name || 'Local radio';
  const localRadioKey = config?.public_key ?? null;
  const canSortByDistance = !!config && isValidLocation(config.lat, config.lon);
  const customHopBytesLocked = useMemo(
    () => draftHops.find((hop) => hop.kind === 'custom')?.hopBytes ?? null,
    [draftHops]
  );
  const effectiveHopHashBytes: CustomHopBytes = customHopBytesLocked ?? 4;

  useEffect(() => {
    if (!customDialogOpen) return;
    setCustomHopBytesDraft(customHopBytesLocked ?? 1);
    setCustomHopHexDraft('');
    setCustomHopError(null);
  }, [customDialogOpen, customHopBytesLocked]);

  const clearPendingResult = () => {
    activeRunTokenRef.current += 1;
    setLoading(false);
    if (result) setResult(null);
    if (error) setError(null);
  };

  const handleAddRepeater = (publicKey: string) => {
    setDraftHops((current) => [
      ...current,
      {
        id: nextDraftHopId('repeater', current.length),
        kind: 'repeater',
        publicKey,
      },
    ]);
    clearPendingResult();
  };

  const handleAddCustomHop = () => {
    const hopBytes = customHopBytesLocked ?? customHopBytesDraft;
    const hopHex = normalizeCustomHopHex(customHopHexDraft);
    if (hopHex.length !== hopBytes * 2) {
      setCustomHopError(`Custom hop must be exactly ${hopBytes * 2} hex characters.`);
      return;
    }
    setDraftHops((current) => [
      ...current,
      {
        id: nextDraftHopId('custom', current.length),
        kind: 'custom',
        hopHex,
        hopBytes,
      },
    ]);
    clearPendingResult();
    setCustomDialogOpen(false);
  };

  const handleRemoveHop = (id: string) => {
    setDraftHops((current) => current.filter((hop) => hop.id !== id));
    clearPendingResult();
  };

  const handleMoveHop = (index: number, direction: -1 | 1) => {
    setDraftHops((current) => moveHop(current, index, direction));
    clearPendingResult();
  };

  const handleLoadRecentTrace = async (trace: SavedTrace) => {
    const hops: TraceDraftHop[] = trace.hops.map((h, i) => {
      if (h.kind === 'repeater' && h.publicKey) {
        return {
          id: nextDraftHopId('repeater', i),
          kind: 'repeater' as const,
          publicKey: h.publicKey,
        };
      }
      return {
        id: nextDraftHopId('custom', i),
        kind: 'custom' as const,
        hopHex: h.hopHex ?? '',
        hopBytes: h.hopBytes ?? (1 as CustomHopBytes),
      };
    });
    setDraftHops(hops);

    // Determine hop hash bytes from the loaded hops
    const customHop = hops.find((h) => h.kind === 'custom');
    const hopHashBytes: CustomHopBytes = customHop?.hopBytes ?? 4;

    // Run the trace immediately
    const runToken = activeRunTokenRef.current + 1;
    activeRunTokenRef.current = runToken;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const traceResult = await onRunTracePath(
        hopHashBytes,
        hops.map((hop) =>
          hop.kind === 'repeater' ? { public_key: hop.publicKey } : { hop_hex: hop.hopHex }
        )
      );
      if (activeRunTokenRef.current !== runToken) return;
      setResult(traceResult);

      // Re-save to bump this trace to the top of recents
      const savedTrace: SavedTrace = { hops: trace.hops, ranAt: Date.now() };
      saveRecentTrace(savedTrace);
      setRecentTraces(loadRecentTraces());
    } catch (err) {
      if (activeRunTokenRef.current !== runToken) return;
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      if (activeRunTokenRef.current === runToken) setLoading(false);
    }
  };

  const handleRunTrace = async () => {
    if (draftHops.length === 0) {
      return;
    }
    const runToken = activeRunTokenRef.current + 1;
    activeRunTokenRef.current = runToken;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const traceResult = await onRunTracePath(
        effectiveHopHashBytes,
        draftHops.map((hop) =>
          hop.kind === 'repeater' ? { public_key: hop.publicKey } : { hop_hex: hop.hopHex }
        )
      );
      if (activeRunTokenRef.current !== runToken) {
        return;
      }
      setResult(traceResult);

      // Persist to recent traces
      const savedHops: SavedTraceHop[] = draftHops.map((hop) => {
        if (hop.kind === 'repeater') {
          const c = repeatersByKey.get(hop.publicKey);
          return {
            kind: 'repeater',
            publicKey: hop.publicKey,
            displayName: getContactDisplayName(c?.name, hop.publicKey, c?.last_advert ?? null),
          };
        }
        return {
          kind: 'custom',
          hopHex: hop.hopHex,
          hopBytes: hop.hopBytes,
          displayName: `${hop.hopHex.toUpperCase()} (${hop.hopBytes}B)`,
        };
      });
      const trace: SavedTrace = { hops: savedHops, ranAt: Date.now() };
      saveRecentTrace(trace);
      setRecentTraces(loadRecentTraces());
    } catch (err) {
      if (activeRunTokenRef.current !== runToken) {
        return;
      }
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      if (activeRunTokenRef.current === runToken) {
        setLoading(false);
      }
    }
  };

  const resultNodes: RadioTraceNode[] = result
    ? [
        {
          role: 'local',
          public_key: localRadioKey,
          name: localRadioName,
          observed_hash: null,
          snr: null,
        },
        ...result.nodes,
      ]
    : [];

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto lg:overflow-hidden">
      <div className="shrink-0 border-b border-border px-4 py-3">
        <h2 className="text-base font-semibold">Trace</h2>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Build a repeater loop and trace it back to the local radio. The selectable hop list only
          includes known full-key repeaters, but you can also add custom repeater prefixes.
        </p>
      </div>

      <div className="flex flex-1 flex-col gap-4 p-4 lg:min-h-0 lg:flex-row lg:overflow-hidden">
        <section className="flex w-full flex-col rounded-lg border border-border bg-card lg:min-h-0 lg:max-w-[24rem]">
          <div className="shrink-0 border-b border-border p-4">
            <h3 className="text-sm font-semibold">Repeater Hops</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Search by name or key, then add repeaters in the order you want to traverse them.
            </p>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="mt-3"
              onClick={() => setCustomDialogOpen(true)}
            >
              Custom path
            </Button>
            <Input
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Search name or public key"
              aria-label="Search repeaters"
              className="mt-3"
            />
            <div className="mt-3 flex flex-wrap gap-2">
              {(
                [
                  ['alpha', 'Alpha'],
                  ['recent', 'Recent Heard'],
                  ['distance', 'Distance'],
                ] as const
              ).map(([value, label]) => (
                <Button
                  key={value}
                  type="button"
                  size="sm"
                  variant={sortMode === value ? 'default' : 'outline'}
                  onClick={() => setSortMode(value)}
                >
                  {label}
                </Button>
              ))}
            </div>
            {sortMode === 'distance' && !canSortByDistance ? (
              <p className="mt-2 text-[0.6875rem] text-muted-foreground">
                Distance sorting is using known repeater coordinates, but the local radio does not
                currently have a valid location.
              </p>
            ) : null}
          </div>

          <div className="max-h-[40vh] overflow-y-auto p-2 lg:min-h-0 lg:max-h-none lg:flex-1">
            {filteredRepeaters.length === 0 ? (
              <div className="rounded-md border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
                No repeaters matched this search.
              </div>
            ) : (
              <div className="space-y-2">
                {filteredRepeaters.map((contact) => {
                  const displayName = getContactDisplayName(
                    contact.name,
                    contact.public_key,
                    contact.last_advert
                  );
                  const distanceKm = getDistanceKm(contact, config);
                  const selectedCount = draftHops.filter(
                    (hop) => hop.kind === 'repeater' && hop.publicKey === contact.public_key
                  ).length;
                  return (
                    <div
                      key={contact.public_key}
                      role="button"
                      tabIndex={0}
                      aria-label={`Add repeater ${displayName}`}
                      className={cn(
                        'flex w-full items-center gap-3 rounded-md border px-3 py-3 text-left transition-colors',
                        selectedCount > 0
                          ? 'border-primary/30 bg-primary/5'
                          : 'border-border bg-background hover:bg-accent'
                      )}
                      onClick={() => handleAddRepeater(contact.public_key)}
                      onKeyDown={handleKeyboardActivate}
                    >
                      <ContactAvatar
                        name={contact.name}
                        publicKey={contact.public_key}
                        size={28}
                        contactType={contact.type}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">{displayName}</div>
                        <div className="truncate text-xs text-muted-foreground">
                          {getShortKey(contact.public_key)}
                        </div>
                        {sortMode === 'distance' && distanceKm !== null ? (
                          <div className="mt-1 text-[0.6875rem] text-muted-foreground">
                            {formatDistance(distanceKm, distanceUnit)} away
                          </div>
                        ) : null}
                        {selectedCount > 0 ? (
                          <div className="mt-1 text-[0.6875rem] text-muted-foreground">
                            Added {selectedCount} time{selectedCount === 1 ? '' : 's'}
                          </div>
                        ) : null}
                      </div>
                      <span
                        className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-input bg-background text-muted-foreground"
                        aria-hidden="true"
                      >
                        <Plus className="h-4 w-4" />
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </section>

        <section className="flex flex-1 flex-col gap-4 lg:min-h-0 lg:overflow-hidden">
          <div className="flex flex-col rounded-lg border border-border bg-card lg:min-h-0 lg:max-h-[50%]">
            <div className="shrink-0 flex items-start justify-between gap-3 border-b border-border px-4 py-3">
              <div>
                <h3 className="text-sm font-semibold">Trace Path</h3>
                <p className="mt-1 text-xs text-muted-foreground">
                  The first node is display-only. The terminal node is the local radio.
                </p>
                {recentTraces.length > 0 && (
                  <div className="mt-2">
                    <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium mb-1">
                      Rerun a recent trace:
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {recentTraces.map((trace, i) => {
                        const label = trace.hops
                          .map((h) => {
                            if (h.kind === 'repeater' && h.publicKey) {
                              const shortKey = h.publicKey.slice(0, 12);
                              return h.displayName !== shortKey
                                ? `${h.displayName} (${shortKey})`
                                : shortKey;
                            }
                            return h.displayName;
                          })
                          .join(' → ');
                        return (
                          <button
                            key={i}
                            type="button"
                            className="rounded-md border border-border px-2.5 py-1.5 text-xs hover:bg-accent transition-colors truncate max-w-full disabled:opacity-50 disabled:cursor-not-allowed"
                            disabled={loading}
                            onClick={() => handleLoadRecentTrace(trace)}
                          >
                            {label}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
              {draftHops.length > 0 ? (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="shrink-0 text-muted-foreground"
                  onClick={() => {
                    setDraftHops([]);
                    clearPendingResult();
                  }}
                >
                  Clear
                </Button>
              ) : null}
            </div>
            <div className="space-y-2 p-4 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
              <TraceNodeRow
                title={localRadioName}
                subtitle={getShortKey(localRadioKey)}
                meta="Origin"
                fixed
                compact
              />
              {draftHops.length === 0 ? (
                <div className="rounded-md border border-dashed border-border px-4 py-6 text-sm text-muted-foreground">
                  Add at least one hop to build a trace loop.
                </div>
              ) : (
                draftHops.map((hop, index) => {
                  const contact =
                    hop.kind === 'repeater' ? (repeatersByKey.get(hop.publicKey) ?? null) : null;
                  const displayName =
                    hop.kind === 'repeater'
                      ? getContactDisplayName(
                          contact?.name,
                          hop.publicKey,
                          contact?.last_advert ?? null
                        )
                      : 'Custom hop';
                  const subtitle =
                    hop.kind === 'repeater'
                      ? getShortKey(hop.publicKey)
                      : `${hop.hopHex.toUpperCase()} (${hop.hopBytes}-byte)`;
                  return (
                    <div key={hop.id}>
                      <TraceNodeRow
                        title={displayName}
                        subtitle={subtitle}
                        meta={`Hop ${index + 1}`}
                        note={
                          index === draftHops.length - 1
                            ? 'Note: you must be able to hear the final repeater in the trace for trace success.'
                            : null
                        }
                        compact
                        actions={
                          <>
                            <Button
                              type="button"
                              size="icon"
                              variant="outline"
                              className="h-8 w-8"
                              aria-label={`Move ${displayName} up`}
                              onClick={() => handleMoveHop(index, -1)}
                              disabled={index === 0}
                            >
                              <ArrowUp className="h-4 w-4" />
                            </Button>
                            <Button
                              type="button"
                              size="icon"
                              variant="outline"
                              className="h-8 w-8"
                              aria-label={`Move ${displayName} down`}
                              onClick={() => handleMoveHop(index, 1)}
                              disabled={index === draftHops.length - 1}
                            >
                              <ArrowDown className="h-4 w-4" />
                            </Button>
                            <Button
                              type="button"
                              size="icon"
                              variant="outline"
                              className="h-8 w-8"
                              aria-label={`Remove ${displayName}`}
                              onClick={() => handleRemoveHop(hop.id)}
                            >
                              <X className="h-4 w-4" />
                            </Button>
                          </>
                        }
                      />
                    </div>
                  );
                })
              )}
              <TraceNodeRow
                title={localRadioName}
                subtitle={getShortKey(localRadioKey)}
                meta="Terminal"
                fixed
                compact
              />
            </div>
            <div className="shrink-0 flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-3">
              <div className="text-xs text-muted-foreground">
                {draftHops.length === 0
                  ? 'No hops selected'
                  : `${draftHops.length} hop${draftHops.length === 1 ? '' : 's'} selected · ${effectiveHopHashBytes}-byte trace`}
              </div>
              <Button onClick={handleRunTrace} disabled={loading || draftHops.length === 0}>
                {loading ? 'Tracing...' : 'Send trace'}
              </Button>
            </div>
          </div>

          <div className="flex flex-col rounded-lg border border-border bg-card lg:min-h-0 lg:flex-1">
            <div className="shrink-0 flex items-center justify-between gap-3 border-b border-border px-4 py-3">
              <h3 className="text-sm font-semibold">
                Results{result ? ` (${result.timeout_seconds.toFixed(1)}s)` : ''}
              </h3>
              {result || error ? (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="shrink-0 text-muted-foreground"
                  onClick={() => {
                    setResult(null);
                    setError(null);
                  }}
                >
                  Clear
                </Button>
              ) : null}
            </div>
            <div className="min-h-0 flex-1 space-y-3 p-4 lg:overflow-y-auto">
              {error ? (
                <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {error}
                </div>
              ) : null}
              {!error && !result ? (
                <div className="rounded-md border border-dashed border-border px-4 py-6 text-sm text-muted-foreground">
                  Send a trace to see the returned hop-by-hop SNR values.
                </div>
              ) : null}
              {result
                ? resultNodes.map((node, index) => {
                    const title =
                      node.name ||
                      (node.role === 'custom'
                        ? 'Custom hop'
                        : node.role === 'local'
                          ? localRadioName
                          : getShortKey(node.public_key));
                    const subtitle =
                      node.role === 'custom'
                        ? `Key prefix ${node.observed_hash?.toUpperCase() ?? 'unknown'}`
                        : node.observed_hash &&
                            node.public_key &&
                            node.observed_hash.toLowerCase() !==
                              getShortKey(node.public_key).toLowerCase()
                          ? `${getShortKey(node.public_key)} · key prefix ${node.observed_hash.toUpperCase()}`
                          : getShortKey(node.public_key);
                    return (
                      <div
                        key={`${node.role}-${node.public_key ?? node.observed_hash ?? 'local'}-${index}`}
                      >
                        <TraceNodeRow
                          title={title}
                          subtitle={subtitle}
                          meta={
                            index === 0
                              ? 'Origin'
                              : node.role === 'local'
                                ? 'Terminal'
                                : `Hop ${index}`
                          }
                          fixed={node.role === 'local'}
                          snr={index === 0 ? null : formatSNR(node.snr)}
                        />
                      </div>
                    );
                  })
                : null}
            </div>
          </div>
        </section>
      </div>

      <Dialog open={customDialogOpen} onOpenChange={setCustomDialogOpen}>
        <DialogContent className="sm:max-w-[440px]">
          <DialogHeader>
            <DialogTitle>Custom path hop</DialogTitle>
            <DialogDescription>
              Add a raw repeater prefix as a 1-byte, 2-byte, or 4-byte hop. Once you add a custom
              hop, all later custom hops must use the same byte width.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-2">
              <div className="text-sm font-medium">Hop width</div>
              <div className="flex flex-wrap gap-2">
                {([1, 2, 4] as const).map((value) => {
                  const locked = customHopBytesLocked !== null && customHopBytesLocked !== value;
                  const active = (customHopBytesLocked ?? customHopBytesDraft) === value;
                  return (
                    <Button
                      key={value}
                      type="button"
                      size="sm"
                      variant={active ? 'default' : 'outline'}
                      disabled={locked}
                      onClick={() => setCustomHopBytesDraft(value)}
                    >
                      {value}-byte
                    </Button>
                  );
                })}
              </div>
              {customHopBytesLocked !== null ? (
                <p className="text-xs text-muted-foreground">
                  Custom hops are locked to {customHopBytesLocked}-byte prefixes for this trace.
                </p>
              ) : null}
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium" htmlFor="custom-hop-hex">
                Repeater prefix
              </label>
              <Input
                id="custom-hop-hex"
                value={customHopHexDraft}
                onChange={(event) =>
                  setCustomHopHexDraft(normalizeCustomHopHex(event.target.value))
                }
                placeholder={`${(customHopBytesLocked ?? customHopBytesDraft) * 2} hex chars`}
              />
              <p className="text-xs text-muted-foreground">
                Enter exactly {(customHopBytesLocked ?? customHopBytesDraft) * 2} hex characters.
              </p>
              {customHopError ? (
                <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {customHopError}
                </div>
              ) : null}
            </div>
          </div>

          <DialogFooter className="gap-2 sm:justify-between">
            <Button type="button" variant="secondary" onClick={() => setCustomDialogOpen(false)}>
              Cancel
            </Button>
            <Button type="button" onClick={handleAddCustomHop}>
              Add custom hop
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
