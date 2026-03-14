import { useState, useCallback, useEffect } from 'react';
import { Button } from '../ui/button';

export function ActionsPane({
  onSendZeroHopAdvert,
  onSendFloodAdvert,
  onSyncClock,
  onReboot,
  consoleLoading,
}: {
  onSendZeroHopAdvert: () => void;
  onSendFloodAdvert: () => void;
  onSyncClock: () => void;
  onReboot: () => void;
  consoleLoading: boolean;
}) {
  const [confirmReboot, setConfirmReboot] = useState(false);

  const handleReboot = useCallback(() => {
    if (!confirmReboot) {
      setConfirmReboot(true);
      return;
    }
    setConfirmReboot(false);
    onReboot();
  }, [confirmReboot, onReboot]);

  // Reset confirmation after 3 seconds
  useEffect(() => {
    if (!confirmReboot) return;
    const timer = setTimeout(() => setConfirmReboot(false), 3000);
    return () => clearTimeout(timer);
  }, [confirmReboot]);

  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <div className="px-3 py-2 bg-muted/50 border-b border-border">
        <h3 className="text-sm font-medium">Actions</h3>
      </div>
      <div className="p-3 flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={onSendZeroHopAdvert} disabled={consoleLoading}>
          Zero Hop Advert
        </Button>
        <Button
          variant="destructive"
          size="sm"
          onClick={onSendFloodAdvert}
          disabled={consoleLoading}
        >
          Flood Advert
        </Button>
        <Button variant="outline" size="sm" onClick={onSyncClock} disabled={consoleLoading}>
          Sync Clock
        </Button>
        <Button
          variant={confirmReboot ? 'destructive' : 'outline'}
          size="sm"
          onClick={handleReboot}
          disabled={consoleLoading}
        >
          {confirmReboot ? 'Confirm Reboot' : 'Reboot'}
        </Button>
      </div>
    </div>
  );
}
