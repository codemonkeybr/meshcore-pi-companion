import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { ChannelCrypto, PayloadType } from '@michaelhart/meshcore-decoder';

import type { Channel, RawPacket } from '../types';
import { cn } from '@/lib/utils';
import {
  createDecoderOptions,
  inspectRawPacketWithOptions,
  type PacketByteField,
} from '../utils/rawPacketInspector';
import { toast } from './ui/sonner';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog';

interface RawPacketDetailModalProps {
  packet: RawPacket | null;
  channels: Channel[];
  onClose: () => void;
}

type RawPacketInspectorDialogSource =
  | {
      kind: 'packet';
      packet: RawPacket;
    }
  | {
      kind: 'paste';
    }
  | {
      kind: 'loading';
      message: string;
    }
  | {
      kind: 'unavailable';
      message: string;
    };

interface SignalOverride {
  rssi: number | null;
  snr: number | null;
}

interface RawPacketInspectorDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  channels: Channel[];
  source: RawPacketInspectorDialogSource;
  title: string;
  description: string;
  notice?: ReactNode;
  signalOverride?: SignalOverride;
}

interface RawPacketInspectionPanelProps {
  packet: RawPacket;
  signalOverride?: SignalOverride;
  channels: Channel[];
}

interface FieldPaletteEntry {
  box: string;
  boxActive: string;
  hex: string;
  hexActive: string;
}

interface GroupTextResolutionCandidate {
  key: string;
  name: string;
  hash: string;
}

const FIELD_PALETTE: FieldPaletteEntry[] = [
  {
    box: 'border-sky-500/30 bg-sky-500/10',
    boxActive: 'border-sky-600 bg-sky-500/20 shadow-sm shadow-sky-500/20',
    hex: 'bg-sky-500/20 ring-1 ring-inset ring-sky-500/35',
    hexActive: 'bg-sky-500/40 ring-1 ring-inset ring-sky-600/70',
  },
  {
    box: 'border-emerald-500/30 bg-emerald-500/10',
    boxActive: 'border-emerald-600 bg-emerald-500/20 shadow-sm shadow-emerald-500/20',
    hex: 'bg-emerald-500/20 ring-1 ring-inset ring-emerald-500/35',
    hexActive: 'bg-emerald-500/40 ring-1 ring-inset ring-emerald-600/70',
  },
  {
    box: 'border-amber-500/30 bg-amber-500/10',
    boxActive: 'border-amber-600 bg-amber-500/20 shadow-sm shadow-amber-500/20',
    hex: 'bg-amber-500/20 ring-1 ring-inset ring-amber-500/35',
    hexActive: 'bg-amber-500/40 ring-1 ring-inset ring-amber-600/70',
  },
  {
    box: 'border-rose-500/30 bg-rose-500/10',
    boxActive: 'border-rose-600 bg-rose-500/20 shadow-sm shadow-rose-500/20',
    hex: 'bg-rose-500/20 ring-1 ring-inset ring-rose-500/35',
    hexActive: 'bg-rose-500/40 ring-1 ring-inset ring-rose-600/70',
  },
  {
    box: 'border-violet-500/30 bg-violet-500/10',
    boxActive: 'border-violet-600 bg-violet-500/20 shadow-sm shadow-violet-500/20',
    hex: 'bg-violet-500/20 ring-1 ring-inset ring-violet-500/35',
    hexActive: 'bg-violet-500/40 ring-1 ring-inset ring-violet-600/70',
  },
  {
    box: 'border-cyan-500/30 bg-cyan-500/10',
    boxActive: 'border-cyan-600 bg-cyan-500/20 shadow-sm shadow-cyan-500/20',
    hex: 'bg-cyan-500/20 ring-1 ring-inset ring-cyan-500/35',
    hexActive: 'bg-cyan-500/40 ring-1 ring-inset ring-cyan-600/70',
  },
  {
    box: 'border-lime-500/30 bg-lime-500/10',
    boxActive: 'border-lime-600 bg-lime-500/20 shadow-sm shadow-lime-500/20',
    hex: 'bg-lime-500/20 ring-1 ring-inset ring-lime-500/35',
    hexActive: 'bg-lime-500/40 ring-1 ring-inset ring-lime-600/70',
  },
  {
    box: 'border-fuchsia-500/30 bg-fuchsia-500/10',
    boxActive: 'border-fuchsia-600 bg-fuchsia-500/20 shadow-sm shadow-fuchsia-500/20',
    hex: 'bg-fuchsia-500/20 ring-1 ring-inset ring-fuchsia-500/35',
    hexActive: 'bg-fuchsia-500/40 ring-1 ring-inset ring-fuchsia-600/70',
  },
];

function formatTimestamp(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleString([], {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatSignal(
  packet: RawPacket,
  signalOverride?: SignalOverride
): { lines: string[]; label: string } {
  const rssi = signalOverride?.rssi ?? packet.rssi;
  const snr = signalOverride?.snr ?? packet.snr;
  const lines: string[] = [];
  if (rssi !== null) lines.push(`${rssi} dBm RSSI`);
  if (snr !== null) lines.push(`${snr.toFixed(1)} dB SNR`);
  const isOverride =
    signalOverride != null && (signalOverride.rssi != null || signalOverride.snr != null);
  return {
    lines: lines.length > 0 ? lines : ['No signal sample'],
    label: isOverride ? 'Last Hop Signal' : 'Signal',
  };
}

function formatByteRange(field: PacketByteField): string {
  if (field.absoluteStartByte === field.absoluteEndByte) {
    return `Byte ${field.absoluteStartByte}`;
  }
  return `Bytes ${field.absoluteStartByte}-${field.absoluteEndByte}`;
}

function formatPathMode(hashSize: number | undefined, hopCount: number): string {
  if (hopCount === 0) {
    return 'No path hops';
  }
  if (!hashSize) {
    return `${hopCount} hop${hopCount === 1 ? '' : 's'}`;
  }
  return `${hopCount} hop${hopCount === 1 ? '' : 's'} · ${hashSize} byte hash${hashSize === 1 ? '' : 'es'}`;
}

function formatTransportCodes(codes: [number, number]): string {
  return codes.map((c) => `0x${c.toString(16).padStart(4, '0')}`).join(', ');
}

function buildGroupTextResolutionCandidates(channels: Channel[]): GroupTextResolutionCandidate[] {
  return channels.map((channel) => ({
    key: channel.key,
    name: channel.name,
    hash: ChannelCrypto.calculateChannelHash(channel.key).toUpperCase(),
  }));
}

function resolveGroupTextChannelName(
  payload: {
    channelHash?: string;
    cipherMac?: string;
    ciphertext?: string;
    decrypted?: { message?: string };
  },
  candidates: GroupTextResolutionCandidate[]
): string | null {
  if (!payload.channelHash) {
    return null;
  }

  const hashMatches = candidates.filter(
    (candidate) => candidate.hash === payload.channelHash?.toUpperCase()
  );
  if (hashMatches.length === 1) {
    return hashMatches[0].name;
  }
  if (
    hashMatches.length <= 1 ||
    !payload.cipherMac ||
    !payload.ciphertext ||
    !payload.decrypted?.message
  ) {
    return null;
  }

  const decryptMatches = hashMatches.filter(
    (candidate) =>
      ChannelCrypto.decryptGroupTextMessage(payload.ciphertext!, payload.cipherMac!, candidate.key)
        .success
  );
  return decryptMatches.length === 1 ? decryptMatches[0].name : null;
}

function packetShowsDecryptedState(
  packet: RawPacket,
  inspection: ReturnType<typeof inspectRawPacketWithOptions>
): boolean {
  const payload = inspection.decoded?.payload.decoded as { decrypted?: unknown } | null | undefined;
  return packet.decrypted || Boolean(packet.decrypted_info) || Boolean(payload?.decrypted);
}

function getPacketContext(
  packet: RawPacket,
  inspection: ReturnType<typeof inspectRawPacketWithOptions>,
  groupTextCandidates: GroupTextResolutionCandidate[]
) {
  const fallbackSender = packet.decrypted_info?.sender ?? null;
  const fallbackChannel = packet.decrypted_info?.channel_name ?? null;

  if (!inspection.decoded?.payload.decoded) {
    if (!fallbackSender && !fallbackChannel) {
      return null;
    }
    return {
      title: fallbackChannel ? 'Channel' : 'Context',
      primary: fallbackChannel ?? 'Sender metadata available',
      secondary: fallbackSender ? `Sender: ${fallbackSender}` : null,
    };
  }

  if (inspection.decoded.payloadType === PayloadType.GroupText) {
    const payload = inspection.decoded.payload.decoded as {
      channelHash?: string;
      cipherMac?: string;
      ciphertext?: string;
      decrypted?: { sender?: string; message?: string };
    };
    const channelName =
      fallbackChannel ?? resolveGroupTextChannelName(payload, groupTextCandidates);
    return {
      title: 'Channel',
      primary:
        channelName ?? (payload.channelHash ? `Channel hash ${payload.channelHash}` : 'GroupText'),
      secondary: payload.decrypted?.sender
        ? `Sender: ${payload.decrypted.sender}`
        : fallbackSender
          ? `Sender: ${fallbackSender}`
          : null,
    };
  }

  if (fallbackSender) {
    return {
      title: 'Context',
      primary: fallbackSender,
      secondary: null,
    };
  }

  return null;
}

function buildDisplayFields(inspection: ReturnType<typeof inspectRawPacketWithOptions>) {
  return [
    ...inspection.packetFields.filter((field) => field.name !== 'Payload'),
    ...inspection.payloadFields,
  ];
}

function buildFieldColorMap(fields: PacketByteField[]) {
  return new Map(
    fields.map((field, index) => [field.id, FIELD_PALETTE[index % FIELD_PALETTE.length]])
  );
}

function buildByteOwners(totalBytes: number, fields: PacketByteField[]) {
  const owners = new Array<string | null>(totalBytes).fill(null);
  for (const field of fields) {
    for (let index = field.absoluteStartByte; index <= field.absoluteEndByte; index += 1) {
      if (index >= 0 && index < owners.length) {
        owners[index] = field.id;
      }
    }
  }
  return owners;
}

function buildByteRuns(bytes: string[], owners: Array<string | null>) {
  const runs: Array<{ fieldId: string | null; text: string }> = [];

  for (let index = 0; index < bytes.length; index += 1) {
    const fieldId = owners[index];
    const lastRun = runs[runs.length - 1];
    if (lastRun && lastRun.fieldId === fieldId) {
      lastRun.text += ` ${bytes[index]}`;
      continue;
    }

    runs.push({
      fieldId,
      text: bytes[index],
    });
  }

  return runs;
}

function CompactMetaCard({
  label,
  primary,
  secondary,
}: {
  label: string;
  primary: string;
  secondary?: string | null;
}) {
  return (
    <div className="rounded-lg border border-border/70 bg-card/70 p-2.5">
      <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="mt-1 text-sm font-medium leading-tight text-foreground">{primary}</div>
      {secondary ? (
        <div className="mt-1 text-xs leading-tight text-muted-foreground">{secondary}</div>
      ) : null}
    </div>
  );
}

function FullPacketHex({
  packetHex,
  fields,
  colorMap,
  hoveredFieldId,
  onHoverField,
}: {
  packetHex: string;
  fields: PacketByteField[];
  colorMap: Map<string, FieldPaletteEntry>;
  hoveredFieldId: string | null;
  onHoverField: (fieldId: string | null) => void;
}) {
  const normalized = packetHex.toUpperCase();
  const bytes = useMemo(() => normalized.match(/.{1,2}/g) ?? [], [normalized]);
  const byteOwners = useMemo(() => buildByteOwners(bytes.length, fields), [bytes.length, fields]);
  const byteRuns = useMemo(() => buildByteRuns(bytes, byteOwners), [byteOwners, bytes]);

  return (
    <div className="font-mono text-[0.9375rem] leading-7 text-foreground">
      {byteRuns.map((run, index) => {
        const fieldId = run.fieldId;
        const palette = fieldId ? colorMap.get(fieldId) : null;
        const active = fieldId !== null && hoveredFieldId === fieldId;
        return (
          <span key={`${fieldId ?? 'plain'}-${index}`}>
            <span
              onMouseEnter={() => onHoverField(fieldId)}
              onMouseLeave={() => onHoverField(null)}
              className={cn(
                'inline rounded-sm px-0.5 py-0.5 transition-colors',
                palette ? (active ? palette.hexActive : palette.hex) : ''
              )}
            >
              {run.text}
            </span>
            {index < byteRuns.length - 1 ? ' ' : ''}
          </span>
        );
      })}
    </div>
  );
}

function renderFieldValue(field: PacketByteField) {
  if (field.name !== 'Path Data') {
    return field.value.toUpperCase();
  }

  const parts = field.value
    .toUpperCase()
    .split(' → ')
    .filter((part) => part.length > 0);

  if (parts.length <= 1) {
    return field.value.toUpperCase();
  }

  return (
    <span className="inline-flex flex-wrap justify-start gap-x-1 sm:justify-end">
      {parts.map((part, index) => {
        const isLast = index === parts.length - 1;
        return (
          <span key={`${field.id}-${part}-${index}`} className="whitespace-nowrap">
            {isLast ? part : `${part} →`}
          </span>
        );
      })}
    </span>
  );
}

function normalizePacketHex(input: string): string {
  return input.replace(/\s+/g, '').toUpperCase();
}

function validatePacketHex(input: string): string | null {
  if (!input) {
    return 'Paste a packet hex string to analyze.';
  }
  if (!/^[0-9A-F]+$/.test(input)) {
    return 'Packet hex may only contain 0-9 and A-F characters.';
  }
  if (input.length % 2 !== 0) {
    return 'Packet hex must contain an even number of characters.';
  }
  return null;
}

function buildPastedRawPacket(packetHex: string): RawPacket {
  return {
    id: -1,
    timestamp: Math.floor(Date.now() / 1000),
    data: packetHex,
    payload_type: 'Unknown',
    snr: null,
    rssi: null,
    decrypted: false,
    decrypted_info: null,
  };
}

function FieldBox({
  field,
  palette,
  active,
  onHoverField,
}: {
  field: PacketByteField;
  palette: FieldPaletteEntry;
  active: boolean;
  onHoverField: (fieldId: string | null) => void;
}) {
  return (
    <div
      onMouseEnter={() => onHoverField(field.id)}
      onMouseLeave={() => onHoverField(null)}
      className={cn(
        'rounded-lg border p-2.5 transition-colors',
        active ? palette.boxActive : palette.box
      )}
    >
      <div className="flex flex-col items-start gap-2 sm:flex-row sm:justify-between">
        <div className="min-w-0">
          <div className="text-base font-semibold leading-tight text-foreground">{field.name}</div>
          <div className="mt-0.5 text-[0.6875rem] text-muted-foreground">
            {formatByteRange(field)}
          </div>
        </div>
        <div
          className={cn(
            'w-full font-mono text-sm leading-5 text-foreground sm:max-w-[14rem] sm:text-right',
            field.name === 'Path Data' ? 'break-normal' : 'break-all'
          )}
        >
          {renderFieldValue(field)}
        </div>
      </div>

      <div className="mt-2 whitespace-pre-wrap text-sm leading-5 text-foreground">
        {field.description}
      </div>

      {field.decryptedMessage ? (
        <div className="mt-2 rounded border border-border/50 bg-background/40 p-2">
          <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground">
            {field.name === 'Ciphertext' ? 'Plaintext' : 'Decoded value'}
          </div>
          <PlaintextContent text={field.decryptedMessage} />
        </div>
      ) : null}

      {field.headerBreakdown ? (
        <div className="mt-2 space-y-1.5">
          <div className="font-mono text-xs tracking-[0.16em] text-muted-foreground">
            {field.headerBreakdown.fullBinary}
          </div>
          {field.headerBreakdown.fields.map((part) => (
            <div
              key={`${field.id}-${part.bits}-${part.field}`}
              className="rounded border border-border/50 bg-background/40 p-2"
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-sm font-medium leading-tight text-foreground">
                    {part.field}
                  </div>
                  <div className="mt-0.5 text-[0.6875rem] text-muted-foreground">
                    Bits {part.bits}
                  </div>
                </div>
                <div className="text-right">
                  <div className="font-mono text-sm text-foreground">{part.binary}</div>
                  <div className="mt-0.5 text-[0.6875rem] text-muted-foreground">{part.value}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function PlaintextContent({ text }: { text: string }) {
  const lines = text.split('\n');

  return (
    <div className="mt-1 space-y-1 text-sm leading-5 text-foreground">
      {lines.map((line, index) => {
        const separatorIndex = line.indexOf(': ');
        if (separatorIndex === -1) {
          return (
            <div key={`${line}-${index}`} className="font-mono">
              {line}
            </div>
          );
        }

        const label = line.slice(0, separatorIndex + 1);
        const value = line.slice(separatorIndex + 2);

        return (
          <div key={`${line}-${index}`}>
            <span>{label} </span>
            <span className="font-mono">{value}</span>
          </div>
        );
      })}
    </div>
  );
}

function FieldSection({
  title,
  fields,
  colorMap,
  hoveredFieldId,
  onHoverField,
}: {
  title: string;
  fields: PacketByteField[];
  colorMap: Map<string, FieldPaletteEntry>;
  hoveredFieldId: string | null;
  onHoverField: (fieldId: string | null) => void;
}) {
  return (
    <section className="rounded-lg border border-border/70 bg-card/70 p-3">
      <div className="mb-2 text-sm font-semibold text-foreground">{title}</div>
      {fields.length === 0 ? (
        <div className="text-sm text-muted-foreground">No decoded fields available.</div>
      ) : (
        <div className="grid gap-2">
          {fields.map((field) => (
            <FieldBox
              key={field.id}
              field={field}
              palette={colorMap.get(field.id) ?? FIELD_PALETTE[0]}
              active={hoveredFieldId === field.id}
              onHoverField={onHoverField}
            />
          ))}
        </div>
      )}
    </section>
  );
}

export function RawPacketInspectionPanel({
  packet,
  channels,
  signalOverride,
}: RawPacketInspectionPanelProps) {
  const decoderOptions = useMemo(() => createDecoderOptions(channels), [channels]);
  const groupTextCandidates = useMemo(
    () => buildGroupTextResolutionCandidates(channels),
    [channels]
  );
  const inspection = useMemo(
    () => inspectRawPacketWithOptions(packet, decoderOptions),
    [decoderOptions, packet]
  );
  const [hoveredFieldId, setHoveredFieldId] = useState<string | null>(null);

  const packetDisplayFields = useMemo(
    () => inspection.packetFields.filter((field) => field.name !== 'Payload'),
    [inspection]
  );
  const fullPacketFields = useMemo(() => buildDisplayFields(inspection), [inspection]);
  const colorMap = useMemo(() => buildFieldColorMap(fullPacketFields), [fullPacketFields]);
  const packetContext = useMemo(
    () => getPacketContext(packet, inspection, groupTextCandidates),
    [groupTextCandidates, inspection, packet]
  );
  const packetIsDecrypted = useMemo(
    () => packetShowsDecryptedState(packet, inspection),
    [inspection, packet]
  );

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-3">
      <div className="grid gap-2 lg:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
        <section className="rounded-lg border border-border/70 bg-card/70 p-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground">
                Summary
              </div>
              <div className="mt-1 text-base font-semibold leading-tight text-foreground">
                {inspection.summary.summary}
              </div>
            </div>
            <div className="shrink-0 text-xs text-muted-foreground">
              {formatTimestamp(packet.timestamp)}
            </div>
          </div>
          {packetContext ? (
            <div className="mt-2 rounded-md border border-border/60 bg-background/35 px-2.5 py-2">
              <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground">
                {packetContext.title}
              </div>
              <div className="mt-1 text-sm font-medium leading-tight text-foreground">
                {packetContext.primary}
              </div>
              {packetContext.secondary ? (
                <div className="mt-1 text-xs leading-tight text-muted-foreground">
                  {packetContext.secondary}
                </div>
              ) : null}
            </div>
          ) : null}
        </section>

        <section
          className={cn(
            'grid gap-2 lg:grid-cols-1',
            inspection.decoded?.transportCodes
              ? 'sm:grid-cols-2 xl:grid-cols-4'
              : 'sm:grid-cols-3 xl:grid-cols-3'
          )}
        >
          <CompactMetaCard
            label="Packet"
            primary={`${packet.data.length / 2} bytes · ${packetIsDecrypted ? 'Decrypted' : 'Encrypted'}`}
            secondary={`Storage #${packet.id}${packet.observation_id !== undefined ? ` · Observation #${packet.observation_id}` : ''}`}
          />
          <CompactMetaCard
            label="Transport"
            primary={`${inspection.routeTypeName} · ${inspection.payloadTypeName}`}
            secondary={`${inspection.payloadVersionName} · ${formatPathMode(inspection.decoded?.pathHashSize, inspection.pathTokens.length)}`}
          />
          {inspection.decoded?.transportCodes ? (
            <CompactMetaCard
              label="Scope"
              primary="Regional"
              secondary={formatTransportCodes(inspection.decoded.transportCodes)}
            />
          ) : null}
          {(() => {
            const sig = formatSignal(packet, signalOverride);
            return (
              <div className="rounded-lg border border-border/70 bg-card/70 p-2.5">
                <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground">
                  {sig.label}
                </div>
                {sig.lines.map((line, i) => (
                  <div
                    key={i}
                    className={`${i === 0 ? 'mt-1' : 'mt-0.5'} text-sm font-medium leading-tight text-foreground`}
                  >
                    {line}
                  </div>
                ))}
              </div>
            );
          })()}
        </section>
      </div>

      {inspection.validationErrors.length > 0 ? (
        <div className="mt-3 rounded-lg border border-warning/40 bg-warning/10 p-2.5">
          <div className="text-sm font-semibold text-foreground">Validation notes</div>
          <div className="mt-1.5 space-y-1 text-sm text-foreground">
            {inspection.validationErrors.map((error) => (
              <div key={error}>{error}</div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="mt-3 rounded-lg border border-border/70 bg-card/70 p-3">
        <div className="flex items-center justify-between gap-3">
          <div className="text-xl font-semibold text-foreground">Full packet hex</div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              navigator.clipboard.writeText(packet.data);
              toast.success('Packet hex copied!');
            }}
          >
            Copy
          </Button>
        </div>
        <div className="mt-2.5">
          <FullPacketHex
            packetHex={packet.data}
            fields={fullPacketFields}
            colorMap={colorMap}
            hoveredFieldId={hoveredFieldId}
            onHoverField={setHoveredFieldId}
          />
        </div>
      </div>

      <div className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
        <FieldSection
          title="Packet fields"
          fields={packetDisplayFields}
          colorMap={colorMap}
          hoveredFieldId={hoveredFieldId}
          onHoverField={setHoveredFieldId}
        />

        <FieldSection
          title="Payload fields"
          fields={inspection.payloadFields}
          colorMap={colorMap}
          hoveredFieldId={hoveredFieldId}
          onHoverField={setHoveredFieldId}
        />
      </div>
    </div>
  );
}

export function RawPacketInspectorDialog({
  open,
  onOpenChange,
  channels,
  source,
  title,
  description,
  notice,
  signalOverride,
}: RawPacketInspectorDialogProps) {
  const [packetInput, setPacketInput] = useState('');

  useEffect(() => {
    if (!open || source.kind !== 'paste') {
      setPacketInput('');
    }
  }, [open, source.kind]);

  const normalizedPacketInput = useMemo(() => normalizePacketHex(packetInput), [packetInput]);
  const packetInputError = useMemo(
    () => (normalizedPacketInput.length > 0 ? validatePacketHex(normalizedPacketInput) : null),
    [normalizedPacketInput]
  );
  const analyzedPacket = useMemo(
    () =>
      normalizedPacketInput.length > 0 && packetInputError === null
        ? buildPastedRawPacket(normalizedPacketInput)
        : null,
    [normalizedPacketInput, packetInputError]
  );

  let body: ReactNode;
  if (source.kind === 'packet') {
    body = (
      <RawPacketInspectionPanel
        packet={source.packet}
        channels={channels}
        signalOverride={signalOverride}
      />
    );
  } else if (source.kind === 'paste') {
    body = (
      <>
        <div className="border-b border-border px-4 py-3 pr-14">
          <div className="flex flex-col gap-3">
            <label className="text-sm font-medium text-foreground" htmlFor="raw-packet-input">
              Packet Hex
            </label>
            <textarea
              id="raw-packet-input"
              value={packetInput}
              onChange={(event) => setPacketInput(event.target.value)}
              placeholder="Paste raw packet hex here..."
              className="min-h-14 w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm text-foreground outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
              spellCheck={false}
            />
            {packetInputError ? (
              <div className="text-sm text-destructive">{packetInputError}</div>
            ) : null}
          </div>
        </div>
        {analyzedPacket ? (
          <RawPacketInspectionPanel packet={analyzedPacket} channels={channels} />
        ) : (
          <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
            Paste a packet above to inspect it.
          </div>
        )}
      </>
    );
  } else if (source.kind === 'loading') {
    body = (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        {source.message}
      </div>
    );
  } else {
    body = (
      <div className="flex flex-1 items-center justify-center p-6">
        <div className="max-w-xl rounded-lg border border-warning/40 bg-warning/10 p-4 text-sm text-foreground">
          {source.message}
        </div>
      </div>
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[92dvh] max-w-[min(96vw,82rem)] flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-border px-5 py-3">
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className="sr-only">{description}</DialogDescription>
        </DialogHeader>
        {notice ? (
          <div className="border-b border-border px-3 py-3 text-sm text-foreground">
            <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-destructive">
              {notice}
            </div>
          </div>
        ) : null}
        {body}
      </DialogContent>
    </Dialog>
  );
}

export function RawPacketDetailModal({ packet, channels, onClose }: RawPacketDetailModalProps) {
  if (!packet) {
    return null;
  }

  return (
    <RawPacketInspectorDialog
      open={packet !== null}
      onOpenChange={(isOpen) => !isOpen && onClose()}
      channels={channels}
      source={{ kind: 'packet', packet }}
      title="Packet Details"
      description="Detailed byte and field breakdown for the selected raw packet."
    />
  );
}
