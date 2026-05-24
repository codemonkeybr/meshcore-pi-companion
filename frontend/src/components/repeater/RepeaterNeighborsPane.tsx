import { useMemo, lazy, Suspense } from 'react';
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
      if (hasValidRepeaterGps && isValidLocation(nLat, nLon)) {
        const distKm = calculateDistance(positionSource.lat, positionSource.lon, nLat, nLon);
        if (distKm != null) {
          dist = formatDistance(distKm, distanceUnit);
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
  }, [contacts, data, distanceUnit, hasValidRepeaterGps, positionSource.lat, positionSource.lon]);

  return (
    <RepeaterPane
      title="Neighbors"
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
                    n.snr >= 6 ? 'text-success' : n.snr >= 0 ? 'text-warning' : 'text-destructive';
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
