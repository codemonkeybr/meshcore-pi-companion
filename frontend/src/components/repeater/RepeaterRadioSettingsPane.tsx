import { cn } from '@/lib/utils';
import { Separator } from '../ui/separator';
import {
  RepeaterPane,
  RefreshIcon,
  NotFetched,
  KvRow,
  formatAdvertInterval,
} from './repeaterPaneShared';
import type {
  RepeaterRadioSettingsResponse,
  RepeaterAdvertIntervalsResponse,
  PaneState,
} from '../../types';

function formatRadioTuple(radio: string | null): { display: string; raw: string | null } {
  if (radio == null) {
    return { display: '—', raw: null };
  }

  const trimmed = radio.trim();
  const parts = trimmed.split(',').map((part) => part.trim());
  if (parts.length !== 4) {
    return { display: trimmed || '—', raw: trimmed || null };
  }

  const [freqRaw, bwRaw, sfRaw, crRaw] = parts;
  const freq = Number.parseFloat(freqRaw);
  const bw = Number.parseFloat(bwRaw);
  const sf = Number.parseInt(sfRaw, 10);
  const cr = Number.parseInt(crRaw, 10);

  if (![freq, bw, sf, cr].every(Number.isFinite)) {
    return { display: trimmed || '—', raw: trimmed || null };
  }

  const formattedFreq = Number(freq.toFixed(3)).toString();
  const formattedBw = Number(bw.toFixed(3)).toString();
  return {
    display: `${formattedFreq} MHz, BW ${formattedBw} kHz, SF${sf}, CR${cr}`,
    raw: trimmed,
  };
}

export function RadioSettingsPane({
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
  const formattedRadio = formatRadioTuple(data?.radio ?? null);

  return (
    <RepeaterPane title="Radio Settings" state={state} onRefresh={onRefresh} disabled={disabled}>
      {!data ? (
        <NotFetched />
      ) : (
        <div>
          <KvRow label="Firmware" value={data.firmware_version ?? '—'} />
          <KvRow
            label="Radio"
            value={<span title={formattedRadio.raw ?? undefined}>{formattedRadio.display}</span>}
          />
          <KvRow label="TX Power" value={data.tx_power != null ? `${data.tx_power} dBm` : '—'} />
          <KvRow label="Airtime Factor" value={data.airtime_factor ?? '—'} />
          <KvRow label="Repeat Mode" value={data.repeat_enabled ?? '—'} />
          <KvRow label="Max Flood Hops" value={data.flood_max ?? '—'} />
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
              : 'text-success hover:bg-accent hover:text-success'
          )}
          title="Refresh Advert Intervals"
          aria-label="Refresh Advert Intervals"
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
          <KvRow
            label="Local Advert"
            value={formatAdvertInterval(advertData.advert_interval, 'minutes')}
          />
          <KvRow
            label="Flood Advert"
            value={formatAdvertInterval(advertData.flood_advert_interval)}
          />
        </div>
      )}
    </RepeaterPane>
  );
}
