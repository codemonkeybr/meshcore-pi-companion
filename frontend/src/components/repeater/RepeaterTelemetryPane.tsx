import type { ReactNode } from 'react';
import { Separator } from '../ui/separator';
import { RepeaterPane, NotFetched, KvRow, formatDuration } from './repeaterPaneShared';
import type { RepeaterStatusResponse, PaneState } from '../../types';

function Secondary({ children }: { children: ReactNode }) {
  return <span className="ml-1.5 font-normal text-muted-foreground">{children}</span>;
}

function formatAirtimePercent(airtimeSec: number, uptimeSec: number): string | null {
  if (uptimeSec <= 0) return null;
  return `${((airtimeSec / uptimeSec) * 100).toFixed(2)}%`;
}

function formatPerMinute(count: number, uptimeSec: number): string | null {
  if (uptimeSec <= 0) return null;
  const rate = (count * 60) / uptimeSec;
  return rate >= 10 ? rate.toFixed(0) : rate.toFixed(1);
}

export function TelemetryPane({
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
  const txPct = data ? formatAirtimePercent(data.airtime_seconds, data.uptime_seconds) : null;
  const rxPct = data ? formatAirtimePercent(data.rx_airtime_seconds, data.uptime_seconds) : null;
  const rxPerMin = data ? formatPerMinute(data.packets_received, data.uptime_seconds) : null;
  const txPerMin = data ? formatPerMinute(data.packets_sent, data.uptime_seconds) : null;

  return (
    <RepeaterPane title="Telemetry" state={state} onRefresh={onRefresh} disabled={disabled}>
      {!data ? (
        <NotFetched />
      ) : (
        <div className="space-y-2">
          <KvRow label="Battery" value={`${data.battery_volts.toFixed(3)}V`} />
          <KvRow label="Uptime" value={formatDuration(data.uptime_seconds)} />
          <KvRow
            label="TX Airtime"
            value={
              <>
                {formatDuration(data.airtime_seconds)}
                {txPct && <Secondary>({txPct})</Secondary>}
              </>
            }
          />
          <KvRow
            label="RX Airtime"
            value={
              <>
                {formatDuration(data.rx_airtime_seconds)}
                {rxPct && <Secondary>({rxPct})</Secondary>}
              </>
            }
          />
          <Separator className="my-1" />
          <KvRow label="Noise Floor" value={`${data.noise_floor_dbm} dBm`} />
          <KvRow label="Last RSSI" value={`${data.last_rssi_dbm} dBm`} />
          <KvRow label="Last SNR" value={`${data.last_snr_db.toFixed(1)} dB`} />
          <Separator className="my-1" />
          <KvRow
            label="Packets"
            value={
              <>
                {data.packets_received.toLocaleString()} rx / {data.packets_sent.toLocaleString()}{' '}
                tx
                {rxPerMin && txPerMin && (
                  <Secondary>
                    (avg {rxPerMin} rx/min / {txPerMin} tx/min)
                  </Secondary>
                )}
              </>
            }
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
          {data.recv_errors != null && (
            <KvRow
              label="RX Errors"
              value={
                <>
                  {data.recv_errors.toLocaleString()}
                  {data.packets_received > 0 && (
                    <Secondary>
                      (
                      {(
                        (data.recv_errors / (data.packets_received + data.recv_errors)) *
                        100
                      ).toFixed(2)}
                      %)
                    </Secondary>
                  )}
                </>
              }
            />
          )}
          <Separator className="my-1" />
          <KvRow label="TX Queue" value={data.tx_queue_len} />
          <KvRow label="Debug Flags" value={data.full_events} />
        </div>
      )}
    </RepeaterPane>
  );
}
