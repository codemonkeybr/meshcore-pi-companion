import { useState } from 'react';
import { Menu } from 'lucide-react';
import type { HealthStatus, RadioConfig } from '../types';
import { api } from '../api';
import { toast } from './ui/sonner';
import { handleKeyboardActivate } from '../utils/a11y';
import { cn } from '@/lib/utils';

interface StatusBarProps {
  health: HealthStatus | null;
  config: RadioConfig | null;
  settingsMode?: boolean;
  onSettingsClick: () => void;
  onMenuClick?: () => void;
}

export function StatusBar({
  health,
  config,
  settingsMode = false,
  onSettingsClick,
  onMenuClick,
}: StatusBarProps) {
  const connected = health?.radio_connected ?? false;
  const [reconnecting, setReconnecting] = useState(false);

  const handleReconnect = async () => {
    setReconnecting(true);
    try {
      const result = await api.reconnectRadio();
      if (result.connected) {
        toast.success('Reconnected', { description: result.message });
      }
    } catch (err) {
      toast.error('Reconnection failed', {
        description: err instanceof Error ? err.message : 'Check radio connection and power',
      });
    } finally {
      setReconnecting(false);
    }
  };

  return (
    <header className="flex items-center gap-3 px-4 py-2.5 bg-card border-b border-border text-xs">
      {/* Mobile menu button - only visible on small screens */}
      {onMenuClick && (
        <button
          onClick={onMenuClick}
          className="md:hidden p-1 bg-transparent border-none text-muted-foreground hover:text-foreground cursor-pointer transition-colors"
          aria-label="Open menu"
        >
          <Menu className="h-5 w-5" />
        </button>
      )}

      <h1 className="text-base font-semibold tracking-tight mr-auto text-foreground flex items-center gap-1.5">
        <svg
          className="h-5 w-5 shrink-0 text-white"
          viewBox="0 0 512 512"
          fill="currentColor"
          aria-hidden="true"
        >
          <path d="m455.68 85.902c-31.289 0-56.32 25.031-56.32 56.32 0 11.379 3.4141 21.617 8.5352 30.152l-106.38 135.39c12.516 6.2578 23.895 15.359 32.996 25.602l107.52-136.54c4.5508 1.1367 9.1016 1.707 13.652 1.707 31.289 0 56.32-25.031 56.32-56.32 0-30.719-25.031-56.32-56.32-56.32z" />
          <path d="m256 343.04c-5.6875 0-10.809 0.57031-15.93 2.2773l-106.38-135.96c-9.1016 10.809-20.48 19.344-32.996 25.602l106.38 135.96c-5.1211 8.5352-7.3945 18.203-7.3945 28.445 0 31.289 25.031 56.32 56.32 56.32s56.32-25.031 56.32-56.32c0-31.293-25.031-56.324-56.32-56.324z" />
          <path d="m356.69 114.91c3.9805-13.652 10.238-26.738 19.344-37.547-38.113-13.652-78.508-21.047-120.04-21.047-59.164 0-115.48 14.789-166.12 42.668-9.1016-6.8281-21.051-10.809-33.562-10.809-31.289-0.57031-56.32 25.027-56.32 55.75 0 31.289 25.031 56.32 56.32 56.32 31.289 0 56.32-25.031 56.32-56.32 0-3.4141-0.57031-6.8281-1.1367-9.6719 44.371-23.895 93.297-36.41 144.5-36.41 34.703 0 68.836 5.6914 100.69 17.066z" />
        </svg>
        RemoteTerm
      </h1>

      <div
        className="flex items-center gap-1.5"
        role="status"
        aria-label={connected ? 'Connected' : 'Disconnected'}
      >
        <div
          className={cn(
            'w-2 h-2 rounded-full transition-colors',
            connected
              ? 'bg-status-connected shadow-[0_0_6px_hsl(var(--status-connected)/0.5)]'
              : 'bg-status-disconnected'
          )}
          aria-hidden="true"
        />
        <span className="hidden lg:inline text-muted-foreground">
          {connected ? 'Connected' : 'Disconnected'}
        </span>
      </div>

      {config && (
        <div className="hidden lg:flex items-center gap-2 text-muted-foreground">
          <span className="text-foreground font-medium">{config.name || 'Unnamed'}</span>
          <span
            className="font-mono text-[11px] text-muted-foreground cursor-pointer hover:text-primary transition-colors"
            role="button"
            tabIndex={0}
            onKeyDown={handleKeyboardActivate}
            onClick={() => {
              navigator.clipboard.writeText(config.public_key);
              toast.success('Public key copied!');
            }}
            title="Click to copy public key"
            aria-label="Copy public key"
          >
            {config.public_key.toLowerCase()}
          </span>
        </div>
      )}

      {!connected && (
        <button
          onClick={handleReconnect}
          disabled={reconnecting}
          className="px-3 py-1 bg-warning/10 border border-warning/20 text-warning rounded-md text-xs cursor-pointer hover:bg-warning/15 transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {reconnecting ? 'Reconnecting...' : 'Reconnect'}
        </button>
      )}
      <button
        onClick={onSettingsClick}
        className={cn(
          'px-3 py-1.5 rounded-md text-xs cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          settingsMode
            ? 'bg-status-connected/15 border border-status-connected/30 text-status-connected hover:bg-status-connected/25'
            : 'bg-secondary border border-border text-muted-foreground hover:bg-accent hover:text-foreground'
        )}
      >
        {settingsMode ? 'Back to Chat' : 'Settings'}
      </button>
    </header>
  );
}
