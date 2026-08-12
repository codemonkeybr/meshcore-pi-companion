import { useMemo, useState, useCallback, lazy, Suspense } from 'react';
import { cn } from '@/lib/utils';
import { RepeaterPane, NotFetched, formatDuration } from './repeaterPaneShared';
import { isValidLocation, calculateDistance, formatDistance } from '../../utils/pathUtils';
import { useDistanceUnit } from '../../contexts/DistanceUnitContext';
import type {
  Contact,
  RepeaterNeighborsResponse,
  PaneState,
  NeighborInfo,
  RepeaterNodeInfoResponse,
} from '../../types';

const NeighborsMiniMap = lazy(() =>
  import('../NeighborsMiniMap').then((m) => ({ default: m.NeighborsMiniMap }))
);

type SortField = 'name' | 'snr' | 'distance' | 'last_heard';
type SortDir = 'asc' | 'desc';

// Direction applied when a column is first selected. Name reads naturally A→Z
// and nearest-first/most-recent-first are the intuitive starting points; SNR
// leads with the strongest signal to preserve the previous default ordering.
const DEFAULT_DIR: Record<SortField, SortDir> = {
  name: 'asc',
  snr: 'desc',
  distance: 'asc',
  last_heard: 'asc',
};

function SortableHeader({
  label,
  field,
  sortField,
  sortDir,
  onSort,
  className,
}: {
  label: string;
  field: SortField;
  sortField: SortField;
  sortDir: SortDir;
  onSort: (field: SortField) => void;
  className?: string;
}) {
  const active = sortField === field;
  return (
    <th
      className={cn(
        'pb-1 font-medium cursor-pointer select-none hover:text-foreground transition-colors',
        className
      )}
      onClick={() => onSort(field)}
      aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
    >
      {label} {active ? (sortDir === 'asc' ? '▲' : '▼') : ''}
    </th>
  );
}

export function NeighborsPane({
  data,
  state,
  onRefresh,
  disabled,
  repeaterContact,
  contacts,
  nodeInfo,
  nodeInfoState,
  repeaterName,
}: {
  data: RepeaterNeighborsResponse | null;
  state: PaneState;
  onRefresh: () => void;
  disabled?: boolean;
  repeaterContact: Contact | null;
  contacts: Contact[];
  nodeInfo: RepeaterNodeInfoResponse | null;
  nodeInfoState: PaneState;
  repeaterName: string | null;
}) {
  const { distanceUnit } = useDistanceUnit();
  const advertLat = repeaterContact?.lat ?? null;
  const advertLon = repeaterContact?.lon ?? null;

  const radioLat = useMemo(() => {
    const parsed = nodeInfo?.lat != null ? parseFloat(nodeInfo.lat) : null;
    return Number.isFinite(parsed) ? parsed : null;
  }, [nodeInfo?.lat]);

  const radioLon = useMemo(() => {
    const parsed = nodeInfo?.lon != null ? parseFloat(nodeInfo.lon) : null;
    return Number.isFinite(parsed) ? parsed : null;
  }, [nodeInfo?.lon]);

  const positionSource = useMemo(() => {
    if (isValidLocation(radioLat, radioLon)) {
      return { lat: radioLat, lon: radioLon, source: 'reported' as const };
    }
    if (isValidLocation(advertLat, advertLon)) {
      return { lat: advertLat, lon: advertLon, source: 'advert' as const };
    }
    return { lat: null, lon: null, source: null };
  }, [advertLat, advertLon, radioLat, radioLon]);

  const radioName = nodeInfo?.name || repeaterContact?.name || repeaterName;
  const hasValidRepeaterGps = positionSource.source !== null;
  const headerNote =
    positionSource.source === 'reported'
      ? 'Using repeater-reported position'
      : positionSource.source === 'advert'
        ? 'Using advert position'
        : nodeInfoState.loading
          ? 'Waiting for repeater position'
          : 'No repeater position available';

  const [sortField, setSortField] = useState<SortField>('snr');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const handleSort = useCallback(
    (field: SortField) => {
      if (sortField === field) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
      } else {
        setSortField(field);
        setSortDir(DEFAULT_DIR[field]);
      }
    },
    [sortField]
  );

  // Resolve contact data for each neighbor in a single pass — used for coords
  // (mini-map) and distances (table column + distance sort). The formatted
  // string drives display; the raw km drives numeric distance sorting.
  const { neighborsWithCoords, enriched, hasDistances } = useMemo(() => {
    if (!data) {
      return {
        neighborsWithCoords: [] as Array<NeighborInfo & { lat: number | null; lon: number | null }>,
        enriched: [] as Array<
          NeighborInfo & { distance: string | null; distanceKm: number | null }
        >,
        hasDistances: false,
      };
    }

    const withCoords: Array<NeighborInfo & { lat: number | null; lon: number | null }> = [];
    const list: Array<NeighborInfo & { distance: string | null; distanceKm: number | null }> = [];
    let anyDist = false;

    for (const n of data.neighbors) {
      const contact = contacts.find((c) => c.public_key.startsWith(n.pubkey_prefix));
      const nLat = contact?.lat ?? null;
      const nLon = contact?.lon ?? null;

      let dist: string | null = null;
      let distKm: number | null = null;
      if (hasValidRepeaterGps && isValidLocation(nLat, nLon)) {
        const km = calculateDistance(positionSource.lat, positionSource.lon, nLat, nLon);
        if (km != null) {
          distKm = km;
          dist = formatDistance(km, distanceUnit);
          anyDist = true;
        }
      }
      list.push({ ...n, distance: dist, distanceKm: distKm });

      if (isValidLocation(nLat, nLon)) {
        withCoords.push({ ...n, lat: nLat, lon: nLon });
      }
    }

    return {
      neighborsWithCoords: withCoords,
      enriched: list,
      hasDistances: anyDist,
    };
  }, [contacts, data, distanceUnit, hasValidRepeaterGps, positionSource.lat, positionSource.lon]);

  const sorted = useMemo(() => {
    const dir = sortDir === 'asc' ? 1 : -1;
    return [...enriched].sort((a, b) => {
      switch (sortField) {
        case 'name': {
          const an = (a.name || a.pubkey_prefix).toLowerCase();
          const bn = (b.name || b.pubkey_prefix).toLowerCase();
          return an.localeCompare(bn) * dir;
        }
        case 'distance': {
          // Neighbors without a known distance always sort last, regardless of direction.
          if (a.distanceKm == null && b.distanceKm == null) return 0;
          if (a.distanceKm == null) return 1;
          if (b.distanceKm == null) return -1;
          return (a.distanceKm - b.distanceKm) * dir;
        }
        case 'last_heard':
          return (a.last_heard_seconds - b.last_heard_seconds) * dir;
        case 'snr':
        default:
          return (a.snr - b.snr) * dir;
      }
    });
  }, [enriched, sortField, sortDir]);

  return (
    <RepeaterPane
      title={
        !data
          ? 'Neighbors'
          : data.reported_count != null && data.reported_count !== data.neighbors.length
            ? `Neighbors (${data.neighbors.length} of ${data.reported_count})`
            : `Neighbors (${data.reported_count ?? data.neighbors.length})`
      }
      headerNote={headerNote}
      state={state}
      onRefresh={onRefresh}
      disabled={disabled}
      className="flex min-h-0 flex-1 flex-col"
      contentClassName="flex min-h-0 flex-1 flex-col"
    >
      {!data ? (
        <NotFetched />
      ) : sorted.length === 0 ? (
        <p className="text-sm text-muted-foreground">No neighbors reported</p>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          <div className="shrink-0 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-muted-foreground text-xs">
                  <SortableHeader
                    label="Name"
                    field="name"
                    sortField={sortField}
                    sortDir={sortDir}
                    onSort={handleSort}
                  />
                  <SortableHeader
                    label="SNR"
                    field="snr"
                    sortField={sortField}
                    sortDir={sortDir}
                    onSort={handleSort}
                    className="text-right"
                  />
                  {hasDistances && (
                    <SortableHeader
                      label="Dist"
                      field="distance"
                      sortField={sortField}
                      sortDir={sortDir}
                      onSort={handleSort}
                      className="text-right"
                    />
                  )}
                  <SortableHeader
                    label="Last Heard"
                    field="last_heard"
                    sortField={sortField}
                    sortDir={sortDir}
                    onSort={handleSort}
                    className="text-right"
                  />
                </tr>
              </thead>
              <tbody>
                {sorted.map((n, i) => {
                  const dist = n.distance;
                  const snrStr = n.snr >= 0 ? `+${n.snr.toFixed(1)}` : n.snr.toFixed(1);
                  const snrColor =
                    n.snr >= 6 ? 'text-success' : n.snr >= 0 ? 'text-warning' : 'text-destructive';
                  return (
                    <tr key={i} className="border-t border-border/50">
                      <td className="py-1">
                        {n.name || n.pubkey_prefix}
                        {n.name && (
                          <span className="ml-1 text-muted-foreground font-mono text-[0.6875rem]">
                            {n.pubkey_prefix.substring(0, 6)}
                          </span>
                        )}
                      </td>
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
          {hasValidRepeaterGps && (neighborsWithCoords.length > 0 || hasValidRepeaterGps) ? (
            <Suspense
              fallback={
                <div className="flex min-h-48 flex-1 items-center justify-center text-xs text-muted-foreground">
                  Loading map...
                </div>
              }
            >
              <NeighborsMiniMap
                key={neighborsWithCoords.map((n) => n.pubkey_prefix).join(',')}
                neighbors={neighborsWithCoords}
                radioLat={positionSource.lat}
                radioLon={positionSource.lon}
                radioName={radioName}
              />
            </Suspense>
          ) : (
            <div className="rounded border border-border/70 bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
              Map and distance data are unavailable until this repeater has a valid position from
              either its advert or a Node Info fetch.
            </div>
          )}
        </div>
      )}
    </RepeaterPane>
  );
}
