import { useEffect, useRef, useMemo } from 'react';
import type { Channel, RawPacket } from '../types';
import { getRawPacketObservationKey } from '../utils/rawPacketIdentity';
import { createDecoderOptions, decodePacketSummary } from '../utils/rawPacketInspector';
import { cn } from '@/lib/utils';

interface RawPacketListProps {
  packets: RawPacket[];
  channels?: Channel[];
  onPacketClick?: (packet: RawPacket) => void;
}

function formatTime(timestamp: number): string {
  const date = new Date(timestamp * 1000);
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function formatSignalInfo(packet: RawPacket): string {
  const parts: string[] = [];
  if (packet.snr !== null && packet.snr !== undefined) {
    parts.push(`SNR: ${packet.snr.toFixed(1)} dB`);
  }
  if (packet.rssi !== null && packet.rssi !== undefined) {
    parts.push(`RSSI: ${packet.rssi} dBm`);
  }
  return parts.join(' | ');
}

// Get route type badge color
function getRouteTypeColor(routeType: string): string {
  switch (routeType) {
    case 'Flood':
      return 'bg-info/20 text-info';
    case 'Direct':
      return 'bg-success/20 text-success';
    case 'TransportFlood':
      return 'bg-purple-500/20 text-purple-400';
    case 'TransportDirect':
      return 'bg-orange-500/20 text-orange-400';
    default:
      return 'bg-muted text-muted-foreground';
  }
}

// Get short route type label
function getRouteTypeLabel(routeType: string): string {
  switch (routeType) {
    case 'Flood':
      return 'F';
    case 'Direct':
      return 'D';
    case 'TransportFlood':
      return 'TF';
    case 'TransportDirect':
      return 'TD';
    default:
      return '?';
  }
}

export function RawPacketList({ packets, channels, onPacketClick }: RawPacketListProps) {
  const listRef = useRef<HTMLDivElement>(null);
  const decoderOptions = useMemo(() => createDecoderOptions(channels), [channels]);

  // Decode all packets (memoized to avoid re-decoding on every render)
  const decodedPackets = useMemo(() => {
    return packets.map((packet) => ({
      packet,
      decoded: decodePacketSummary(packet, decoderOptions),
    }));
  }, [decoderOptions, packets]);

  // Sort packets by timestamp ascending (oldest first)
  const sortedPackets = useMemo(
    () => [...decodedPackets].sort((a, b) => a.packet.timestamp - b.packet.timestamp),
    [decodedPackets]
  );

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [packets]);

  if (packets.length === 0) {
    return (
      <div className="h-full overflow-y-auto p-5 text-center text-muted-foreground [contain:layout_paint]">
        No packets received yet. Packets will appear here in real-time.
      </div>
    );
  }

  return (
    <div
      className="h-full overflow-y-auto p-4 flex flex-col gap-2 [contain:layout_paint]"
      ref={listRef}
    >
      {sortedPackets.map(({ packet, decoded }) => {
        const cardContent = (
          <>
            <div className="flex items-center gap-2">
              {/* Route type badge */}
              <span
                className={`text-[0.625rem] font-mono px-1.5 py-0.5 rounded ${getRouteTypeColor(decoded.routeType)}`}
                title={decoded.routeType}
              >
                {getRouteTypeLabel(decoded.routeType)}
              </span>

              {/* Encryption status */}
              {!packet.decrypted && (
                <>
                  <span aria-hidden="true">🔒</span>
                  <span className="sr-only">Encrypted</span>
                </>
              )}

              {/* Summary */}
              <span
                className={cn(
                  'text-[0.8125rem]',
                  packet.decrypted ? 'text-primary' : 'text-foreground'
                )}
              >
                {decoded.summary}
              </span>

              {/* Time */}
              <span className="text-muted-foreground ml-auto text-xs tabular-nums">
                {formatTime(packet.timestamp)}
              </span>
            </div>

            {/* Signal info */}
            {(packet.snr !== null || packet.rssi !== null) && (
              <div className="text-[0.6875rem] text-muted-foreground mt-0.5 tabular-nums">
                {formatSignalInfo(packet)}
              </div>
            )}

            {/* Raw hex data (always visible) */}
            <div className="font-mono text-[0.625rem] break-all text-muted-foreground mt-1.5 p-1.5 bg-background/60 rounded">
              {packet.data.toUpperCase()}
            </div>
          </>
        );

        const className = cn(
          'rounded-md border border-border/50 bg-card px-3 py-2 text-left',
          onPacketClick &&
            'cursor-pointer transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
        );

        if (onPacketClick) {
          return (
            <button
              key={getRawPacketObservationKey(packet)}
              type="button"
              onClick={() => onPacketClick(packet)}
              className={className}
            >
              {cardContent}
            </button>
          );
        }

        return (
          <div key={getRawPacketObservationKey(packet)} className={className}>
            {cardContent}
          </div>
        );
      })}
    </div>
  );
}
