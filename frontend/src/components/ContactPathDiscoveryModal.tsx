import { useMemo, useState } from 'react';

import type { Contact, PathDiscoveryResponse, PathDiscoveryRoute } from '../types';
import {
  findContactsByPrefix,
  formatForcedRouteSummary,
  formatLearnedRouteSummary,
  formatRouteLabel,
  parsePathHops,
} from '../utils/pathUtils';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';

interface ContactPathDiscoveryModalProps {
  open: boolean;
  onClose: () => void;
  contact: Contact;
  contacts: Contact[];
  radioName: string | null;
  onDiscover: (publicKey: string) => Promise<PathDiscoveryResponse>;
}

function formatPathHashMode(mode: number): string {
  if (mode === 0) return '1-byte hops';
  if (mode === 1) return '2-byte hops';
  if (mode === 2) return '3-byte hops';
  return 'Unknown hop width';
}

function renderRouteNodes(
  route: PathDiscoveryRoute,
  startLabel: string,
  endLabel: string,
  contacts: Contact[]
): string {
  if (route.path_len <= 0 || !route.path) {
    return `${startLabel} -> ${endLabel}`;
  }

  const hops = parsePathHops(route.path, route.path_len).map((prefix) => {
    const matches = findContactsByPrefix(prefix, contacts, true);
    if (matches.length === 1) {
      return matches[0].name || `${matches[0].public_key.slice(0, prefix.length)}…`;
    }
    if (matches.length > 1) {
      return `${prefix}…?`;
    }
    return `${prefix}…`;
  });

  return [startLabel, ...hops, endLabel].join(' -> ');
}

function RouteCard({
  label,
  route,
  chain,
}: {
  label: string;
  route: PathDiscoveryRoute;
  chain: string;
}) {
  const rawPath = parsePathHops(route.path, route.path_len).join(' -> ') || 'direct';

  return (
    <div className="rounded-md border border-border bg-muted/20 p-3">
      <div className="flex items-center justify-between gap-3">
        <h4 className="text-sm font-semibold">{label}</h4>
        <span className="text-[0.6875rem] text-muted-foreground">
          {formatRouteLabel(route.path_len, true)}
        </span>
      </div>
      <p className="mt-2 text-sm">{chain}</p>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[0.6875rem] text-muted-foreground">
        <span>Raw: {rawPath}</span>
        <span>{formatPathHashMode(route.path_hash_mode)}</span>
      </div>
    </div>
  );
}

export function ContactPathDiscoveryModal({
  open,
  onClose,
  contact,
  contacts,
  radioName,
  onDiscover,
}: ContactPathDiscoveryModalProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PathDiscoveryResponse | null>(null);

  const learnedRouteSummary = useMemo(() => formatLearnedRouteSummary(contact), [contact]);
  const forcedRouteSummary = useMemo(() => formatForcedRouteSummary(contact), [contact]);
  const hasForcedRoute = forcedRouteSummary !== null;

  const forwardChain = result
    ? renderRouteNodes(
        result.forward_path,
        radioName || 'Local radio',
        contact.name || contact.public_key.slice(0, 12),
        contacts
      )
    : null;
  const returnChain = result
    ? renderRouteNodes(
        result.return_path,
        contact.name || contact.public_key.slice(0, 12),
        radioName || 'Local radio',
        contacts
      )
    : null;

  const handleDiscover = async () => {
    setLoading(true);
    setError(null);
    try {
      const discovered = await onDiscover(contact.public_key);
      setResult(discovered);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>Path Discovery</DialogTitle>
          <DialogDescription>
            Send a routed probe to this contact and wait for the round-trip path response. The
            learned forward route will be saved back onto the contact if a response comes back.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="rounded-md border border-border bg-muted/20 p-3 text-sm">
            <div className="font-medium">{contact.name || contact.public_key.slice(0, 12)}</div>
            <div className="mt-1 text-muted-foreground">
              Current learned route: {learnedRouteSummary}
            </div>
            {forcedRouteSummary && (
              <div className="mt-1 text-destructive">
                Current forced route: {forcedRouteSummary}
              </div>
            )}
          </div>

          {hasForcedRoute && (
            <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning">
              A forced route override is currently set for this contact. Path discovery will update
              the learned route data, but it will not replace the forced path. Clearing the forced
              route afterward is enough to make the newly discovered learned path take effect. You
              only need to rerun path discovery if you want a fresher route sample.
            </div>
          )}

          {error && (
            <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          )}

          {result && forwardChain && returnChain && (
            <div className="space-y-3">
              <RouteCard label="Forward Path" route={result.forward_path} chain={forwardChain} />
              <RouteCard label="Return Path" route={result.return_path} chain={returnChain} />
            </div>
          )}
        </div>

        <DialogFooter className="gap-2 sm:justify-between">
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
          <Button onClick={handleDiscover} disabled={loading}>
            {loading ? 'Running...' : 'Run path discovery'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
