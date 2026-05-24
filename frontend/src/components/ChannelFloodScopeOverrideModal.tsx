import { useEffect, useState } from 'react';

import { stripRegionScopePrefix } from '../utils/regionScope';
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
    setRegion(stripRegionScopePrefix(currentOverride));
  }, [currentOverride, open]);

  const trimmedRegion = region.trim();

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>Regional Override</DialogTitle>
          <DialogDescription>
            Channel-level regional routing temporarily changes the radio flood scope before send and
            restores it after. This can noticeably slow channel sends.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="rounded-md border border-border bg-muted/20 p-3 text-sm">
            <div className="font-medium">{roomName}</div>
            <div className="mt-1 text-muted-foreground">
              Current regional override:{' '}
              {currentOverride ? stripRegionScopePrefix(currentOverride) : 'none'}
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
                ? `Use ${trimmedRegion} region for ${roomName}`
                : `Use region for ${roomName}`}
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
              Do not use region routing for {roomName}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
