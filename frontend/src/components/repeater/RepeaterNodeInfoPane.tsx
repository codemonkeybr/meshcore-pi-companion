import { useMemo } from 'react';
import { cn } from '@/lib/utils';
import { RepeaterPane, NotFetched, KvRow, formatClockDrift } from './repeaterPaneShared';
import type { RepeaterNodeInfoResponse, PaneState } from '../../types';

export function NodeInfoPane({
  data,
  state,
  onRefresh,
  disabled,
}: {
  data: RepeaterNodeInfoResponse | null;
  state: PaneState;
  onRefresh: () => void;
  disabled?: boolean;
}) {
  const clockDrift = useMemo(() => {
    if (!data?.clock_utc) return null;
    return formatClockDrift(data.clock_utc);
  }, [data?.clock_utc]);

  return (
    <RepeaterPane title="Node Info" state={state} onRefresh={onRefresh} disabled={disabled}>
      {!data ? (
        <NotFetched />
      ) : (
        <div>
          <KvRow label="Name" value={data.name ?? '—'} />
          <KvRow
            label="Lat / Lon"
            value={
              data.lat != null || data.lon != null ? `${data.lat ?? '—'}, ${data.lon ?? '—'}` : '—'
            }
          />
          <div className="flex justify-between text-sm py-0.5">
            <span className="text-muted-foreground">Clock (UTC)</span>
            <span>
              {data.clock_utc ?? '—'}
              {clockDrift && (
                <span
                  className={cn(
                    'ml-2 text-xs',
                    clockDrift.isLarge ? 'text-destructive' : 'text-muted-foreground'
                  )}
                >
                  (drift: {clockDrift.text})
                </span>
              )}
            </span>
          </div>
        </div>
      )}
    </RepeaterPane>
  );
}
