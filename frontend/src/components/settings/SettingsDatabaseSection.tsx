import { useState, useEffect } from 'react';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Button } from '../ui/button';
import { Separator } from '../ui/separator';
import { toast } from '../ui/sonner';
import { api } from '../../api';
import { formatTime } from '../../utils/messageParser';
import {
  captureLastViewedConversationFromHash,
  getReopenLastConversationEnabled,
  setReopenLastConversationEnabled,
} from '../../utils/lastViewedConversation';
import { getLocalLabel, setLocalLabel, type LocalLabel } from '../../utils/localLabel';
import type { AppSettings, AppSettingsUpdate, HealthStatus } from '../../types';

export function SettingsDatabaseSection({
  appSettings,
  health,
  onSaveAppSettings,
  onHealthRefresh,
  onLocalLabelChange,
  className,
}: {
  appSettings: AppSettings;
  health: HealthStatus | null;
  onSaveAppSettings: (update: AppSettingsUpdate) => Promise<void>;
  onHealthRefresh: () => Promise<void>;
  onLocalLabelChange?: (label: LocalLabel) => void;
  className?: string;
}) {
  const [retentionDays, setRetentionDays] = useState('14');
  const [cleaning, setCleaning] = useState(false);
  const [purgingDecryptedRaw, setPurgingDecryptedRaw] = useState(false);
  const [autoDecryptOnAdvert, setAutoDecryptOnAdvert] = useState(false);
  const [reopenLastConversation, setReopenLastConversation] = useState(
    getReopenLastConversationEnabled
  );
  const [localLabelText, setLocalLabelText] = useState(() => getLocalLabel().text);
  const [localLabelColor, setLocalLabelColor] = useState(() => getLocalLabel().color);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setAutoDecryptOnAdvert(appSettings.auto_decrypt_dm_on_advert);
  }, [appSettings]);

  const handleCleanup = async () => {
    const days = parseInt(retentionDays, 10);
    if (isNaN(days) || days < 1) {
      toast.error('Invalid retention days', {
        description: 'Retention days must be at least 1',
      });
      return;
    }

    setCleaning(true);

    try {
      const result = await api.runMaintenance({ pruneUndecryptedDays: days });
      toast.success('Database cleanup complete', {
        description: `Deleted ${result.packets_deleted} old packet${result.packets_deleted === 1 ? '' : 's'}`,
      });
      await onHealthRefresh();
    } catch (err) {
      console.error('Failed to run maintenance:', err);
      toast.error('Database cleanup failed', {
        description: err instanceof Error ? err.message : 'Unknown error',
      });
    } finally {
      setCleaning(false);
    }
  };

  const handlePurgeDecryptedRawPackets = async () => {
    setPurgingDecryptedRaw(true);

    try {
      const result = await api.runMaintenance({ purgeLinkedRawPackets: true });
      toast.success('Decrypted raw packets purged', {
        description: `Deleted ${result.packets_deleted} raw packet${result.packets_deleted === 1 ? '' : 's'}`,
      });
      await onHealthRefresh();
    } catch (err) {
      console.error('Failed to purge decrypted raw packets:', err);
      toast.error('Failed to purge decrypted raw packets', {
        description: err instanceof Error ? err.message : 'Unknown error',
      });
    } finally {
      setPurgingDecryptedRaw(false);
    }
  };

  const handleSave = async () => {
    setBusy(true);
    setError(null);

    try {
      await onSaveAppSettings({ auto_decrypt_dm_on_advert: autoDecryptOnAdvert });
      toast.success('Database settings saved');
    } catch (err) {
      console.error('Failed to save database settings:', err);
      setError(err instanceof Error ? err.message : 'Failed to save');
      toast.error('Failed to save settings');
    } finally {
      setBusy(false);
    }
  };

  const handleToggleReopenLastConversation = (enabled: boolean) => {
    setReopenLastConversation(enabled);
    setReopenLastConversationEnabled(enabled);
    if (enabled) {
      captureLastViewedConversationFromHash();
    }
  };

  return (
    <div className={className}>
      <div className="space-y-3">
        <div className="flex justify-between items-center">
          <span className="text-sm text-muted-foreground">Database size</span>
          <span className="font-medium">{health?.database_size_mb ?? '?'} MB</span>
        </div>

        {health?.oldest_undecrypted_timestamp ? (
          <div className="flex justify-between items-center">
            <span className="text-sm text-muted-foreground">Oldest undecrypted packet</span>
            <span className="font-medium">
              {formatTime(health.oldest_undecrypted_timestamp)}
              <span className="text-muted-foreground ml-1">
                ({Math.floor((Date.now() / 1000 - health.oldest_undecrypted_timestamp) / 86400)}{' '}
                days old)
              </span>
            </span>
          </div>
        ) : (
          <div className="flex justify-between items-center">
            <span className="text-sm text-muted-foreground">Oldest undecrypted packet</span>
            <span className="text-muted-foreground">None</span>
          </div>
        )}
      </div>

      <Separator />

      <div className="space-y-3">
        <Label>Delete Undecrypted Packets</Label>
        <p className="text-xs text-muted-foreground">
          Permanently deletes stored raw packets containing DMs and channel messages that have not
          yet been decrypted. These packets are retained in case you later obtain the correct key —
          once deleted, these messages can never be recovered or decrypted.
        </p>
        <div className="flex gap-2 items-end">
          <div className="space-y-1">
            <Label htmlFor="retention-days" className="text-xs">
              Older than (days)
            </Label>
            <Input
              id="retention-days"
              type="number"
              min="1"
              max="365"
              value={retentionDays}
              onChange={(e) => setRetentionDays(e.target.value)}
              className="w-24"
            />
          </div>
          <Button
            variant="outline"
            onClick={handleCleanup}
            disabled={cleaning}
            className="border-red-500/50 text-red-400 hover:bg-red-500/10"
          >
            {cleaning ? 'Deleting...' : 'Permanently Delete'}
          </Button>
        </div>
      </div>

      <Separator />

      <div className="space-y-3">
        <Label>Purge Archival Raw Packets</Label>
        <p className="text-xs text-muted-foreground">
          Deletes archival copies of raw packet bytes for messages that are already decrypted and
          visible in your chat history.{' '}
          <em className="text-muted-foreground/80">
            This will not affect any displayed messages or app functionality.
          </em>{' '}
          The raw bytes are only useful for manual packet analysis.
        </p>
        <Button
          variant="outline"
          onClick={handlePurgeDecryptedRawPackets}
          disabled={purgingDecryptedRaw}
          className="w-full border-yellow-500/50 text-yellow-400 hover:bg-yellow-500/10"
        >
          {purgingDecryptedRaw ? 'Purging Archival Raw Packets...' : 'Purge Archival Raw Packets'}
        </Button>
      </div>

      <Separator />

      <div className="space-y-3">
        <Label>DM Decryption</Label>
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={autoDecryptOnAdvert}
            onChange={(e) => setAutoDecryptOnAdvert(e.target.checked)}
            className="w-4 h-4 rounded border-input accent-primary"
          />
          <span className="text-sm">Auto-decrypt historical DMs when new contact advertises</span>
        </label>
        <p className="text-xs text-muted-foreground">
          When enabled, the server will automatically try to decrypt stored DM packets when a new
          contact sends an advertisement. This may cause brief delays on large packet backlogs.
        </p>
      </div>

      <Separator />

      <div className="space-y-3">
        <Label>Interface</Label>
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={reopenLastConversation}
            onChange={(e) => handleToggleReopenLastConversation(e.target.checked)}
            className="w-4 h-4 rounded border-input accent-primary"
          />
          <span className="text-sm">Reopen to last viewed channel/conversation</span>
        </label>
        <p className="text-xs text-muted-foreground">
          This applies only to this device/browser. It does not sync to server settings.
        </p>
      </div>

      <Separator />

      <div className="space-y-3">
        <Label>Local Label</Label>
        <div className="flex items-center gap-2">
          <Input
            value={localLabelText}
            onChange={(e) => {
              const text = e.target.value;
              setLocalLabelText(text);
              setLocalLabel(text, localLabelColor);
              onLocalLabelChange?.({ text, color: localLabelColor });
            }}
            placeholder="e.g. Home Base, Field Radio 2"
            className="flex-1"
          />
          <input
            type="color"
            value={localLabelColor}
            onChange={(e) => {
              const color = e.target.value;
              setLocalLabelColor(color);
              setLocalLabel(localLabelText, color);
              onLocalLabelChange?.({ text: localLabelText, color });
            }}
            className="w-10 h-9 rounded border border-input cursor-pointer bg-transparent p-0.5"
          />
        </div>
        <p className="text-xs text-muted-foreground">
          Display a colored banner at the top of the page to identify this instance. This applies
          only to this device/browser.
        </p>
      </div>

      {error && <div className="text-sm text-destructive">{error}</div>}

      <Button onClick={handleSave} disabled={busy} className="w-full">
        {busy ? 'Saving...' : 'Save Settings'}
      </Button>
    </div>
  );
}
