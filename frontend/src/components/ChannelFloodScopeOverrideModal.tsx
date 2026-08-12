import { useEffect, useState } from 'react';

import {
  UNSCOPED_OVERRIDE_MARKER,
  isUnscopedMarker,
  stripRegionScopePrefix,
} from '../utils/regionScope';
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
import { Label } from './ui/label';

interface ChannelFloodScopeOverrideModalProps {
  open: boolean;
  onClose: () => void;
  roomName: string;
  currentOverride: string | null;
  onSetOverride: (value: string) => void;
}

export function ChannelFloodScopeOverrideModal({
  open,
  onClose,
  roomName,
  currentOverride,
  onSetOverride,
}: ChannelFloodScopeOverrideModalProps) {
  const [region, setRegion] = useState('');

  useEffect(() => {
    if (!open) {
      return;
    }
    // The unscoped marker isn't a region name, so start the input blank for it.
    setRegion(isUnscopedMarker(currentOverride) ? '' : stripRegionScopePrefix(currentOverride));
  }, [currentOverride, open]);

  const trimmedRegion = region.trim();

  const currentOverrideLabel = isUnscopedMarker(currentOverride)
    ? 'unscoped (plain flood)'
    : currentOverride
      ? stripRegionScopePrefix(currentOverride)
      : 'inherit global setting';

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>Regional Override</DialogTitle>
          <DialogDescription>
            Channel-level regional routing temporarily changes the radio flood scope before send and
            restores it after. This can noticeably slow channel sends. Choose one of three modes
            below: scope to a region, force unscoped (plain flood, ignoring your global region), or
            inherit the global setting.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="rounded-md border border-border bg-muted/20 p-3 text-sm">
            <div className="font-medium">{roomName}</div>
            <div className="mt-1 text-muted-foreground">
              Current setting: {currentOverrideLabel}
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="channel-region-input">Region</Label>
            <Input
              id="channel-region-input"
              value={region}
              onChange={(event) => setRegion(event.target.value)}
              placeholder="Esperance"
              autoFocus
            />
          </div>
        </div>

        <DialogFooter className="gap-2 sm:block sm:space-x-0">
          <div className="space-y-2">
            <Button
              type="button"
              className="w-full"
              disabled={trimmedRegion.length === 0}
              onClick={() => {
                onSetOverride(trimmedRegion);
                onClose();
              }}
            >
              {trimmedRegion.length > 0
                ? `Scope ${roomName} to ${trimmedRegion}`
                : `Scope ${roomName} to a region`}
            </Button>
            <Button
              type="button"
              variant="outline"
              className="w-full"
              onClick={() => {
                onSetOverride(UNSCOPED_OVERRIDE_MARKER);
                onClose();
              }}
            >
              Always send {roomName} unscoped (ignore global region)
            </Button>
            <Button
              type="button"
              variant="outline"
              className="w-full"
              onClick={() => {
                onSetOverride('');
                onClose();
              }}
            >
              Use global region setting for {roomName}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
