import { toast } from './ui/sonner';
import { Button } from './ui/button';
import { Bell, Star, Trash2 } from 'lucide-react';
import { DirectTraceIcon } from './DirectTraceIcon';
import { RepeaterLogin } from './RepeaterLogin';
import { useRepeaterDashboard } from '../hooks/useRepeaterDashboard';
import { isFavorite } from '../utils/favorites';
import { handleKeyboardActivate } from '../utils/a11y';
import { ContactStatusInfo } from './ContactStatusInfo';
import type { Contact, Conversation, Favorite } from '../types';
import { TelemetryPane } from './repeater/RepeaterTelemetryPane';
import { NeighborsPane } from './repeater/RepeaterNeighborsPane';
import { AclPane } from './repeater/RepeaterAclPane';
import { NodeInfoPane } from './repeater/RepeaterNodeInfoPane';
import { RadioSettingsPane } from './repeater/RepeaterRadioSettingsPane';
import { LppTelemetryPane } from './repeater/RepeaterLppTelemetryPane';
import { OwnerInfoPane } from './repeater/RepeaterOwnerInfoPane';
import { ActionsPane } from './repeater/RepeaterActionsPane';
import { ConsolePane } from './repeater/RepeaterConsolePane';

// Re-export for backwards compatibility (used by repeaterFormatters.test.ts)
export { formatDuration, formatClockDrift } from './repeater/repeaterPaneShared';

// --- Main Dashboard ---

interface RepeaterDashboardProps {
  conversation: Conversation;
  contacts: Contact[];
  favorites: Favorite[];
  notificationsSupported: boolean;
  notificationsEnabled: boolean;
  notificationsPermission: NotificationPermission | 'unsupported';
  radioLat: number | null;
  radioLon: number | null;
  radioName: string | null;
  onTrace: () => void;
  onToggleNotifications: () => void;
  onToggleFavorite: (type: 'channel' | 'contact', id: string) => void;
  onDeleteContact: (publicKey: string) => void;
}

export function RepeaterDashboard({
  conversation,
  contacts,
  favorites,
  notificationsSupported,
  notificationsEnabled,
  notificationsPermission,
  radioLat,
  radioLon,
  radioName: _radioName,
  onTrace,
  onToggleNotifications,
  onToggleFavorite,
  onDeleteContact,
}: RepeaterDashboardProps) {
  const {
    loggedIn,
    loginLoading,
    loginError,
    paneData,
    paneStates,
    consoleHistory,
    consoleLoading,
    login,
    loginAsGuest,
    refreshPane,
    loadAll,
    sendConsoleCommand,
    sendZeroHopAdvert,
    sendFloodAdvert,
    rebootRepeater,
    syncClock,
  } = useRepeaterDashboard(conversation);

  const contact = contacts.find((c) => c.public_key === conversation.id);
  const isFav = isFavorite(favorites, 'contact', conversation.id);

  // Loading all panes indicator
  const anyLoading = Object.values(paneStates).some((s) => s.loading);

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Header */}
      <header className="flex justify-between items-start px-4 py-2.5 border-b border-border gap-2">
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="flex min-w-0 flex-1 items-baseline gap-2">
              <span className="min-w-0 flex-shrink truncate font-semibold text-base">
                {conversation.name}
              </span>
              <span
                className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground transition-colors hover:text-primary"
                role="button"
                tabIndex={0}
                onKeyDown={handleKeyboardActivate}
                onClick={() => {
                  navigator.clipboard.writeText(conversation.id);
                  toast.success('Contact key copied!');
                }}
                title="Click to copy"
              >
                {conversation.id}
              </span>
            </span>
            {contact && (
              <span className="min-w-0 flex-none text-[11px] text-muted-foreground max-sm:basis-full">
                <ContactStatusInfo contact={contact} ourLat={radioLat} ourLon={radioLon} />
              </span>
            )}
          </span>
        </span>
        <div className="flex items-center gap-0.5 flex-shrink-0">
          {loggedIn && (
            <Button
              variant="outline"
              size="sm"
              onClick={loadAll}
              disabled={anyLoading}
              className="h-7 px-2 text-[11px] leading-none border-success text-success hover:bg-success/10 hover:text-success sm:h-8 sm:px-3 sm:text-xs"
            >
              {anyLoading ? 'Loading...' : 'Load All'}
            </Button>
          )}
          <button
            className="p-1 rounded hover:bg-accent text-lg leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={onTrace}
            title="Direct Trace"
            aria-label="Direct Trace"
          >
            <DirectTraceIcon className="h-4 w-4 text-muted-foreground" />
          </button>
          {notificationsSupported && (
            <button
              className="flex items-center gap-1 rounded px-1 py-1 hover:bg-accent text-lg leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              onClick={onToggleNotifications}
              title={
                notificationsEnabled
                  ? 'Disable desktop notifications for this conversation'
                  : notificationsPermission === 'denied'
                    ? 'Notifications blocked by the browser'
                    : 'Enable desktop notifications for this conversation'
              }
              aria-label={
                notificationsEnabled
                  ? 'Disable notifications for this conversation'
                  : 'Enable notifications for this conversation'
              }
            >
              <Bell
                className={`h-4 w-4 ${notificationsEnabled ? 'text-status-connected' : 'text-muted-foreground'}`}
                fill={notificationsEnabled ? 'currentColor' : 'none'}
                aria-hidden="true"
              />
              {notificationsEnabled && (
                <span className="hidden md:inline text-[11px] font-medium text-status-connected">
                  Notifications On
                </span>
              )}
            </button>
          )}
          <button
            className="p-1 rounded hover:bg-accent text-lg leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => onToggleFavorite('contact', conversation.id)}
            title={
              isFav
                ? 'Remove from favorites. Favorite contacts stay loaded on the radio for ACK support.'
                : 'Add to favorites. Favorite contacts stay loaded on the radio for ACK support.'
            }
            aria-label={isFav ? 'Remove from favorites' : 'Add to favorites'}
          >
            {isFav ? (
              <Star className="h-4 w-4 fill-current text-favorite" aria-hidden="true" />
            ) : (
              <Star className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            )}
          </button>
          <button
            className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive text-lg leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => onDeleteContact(conversation.id)}
            title="Delete"
            aria-label="Delete"
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </header>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4">
        {!loggedIn ? (
          <RepeaterLogin
            repeaterName={conversation.name}
            loading={loginLoading}
            error={loginError}
            onLogin={login}
            onLoginAsGuest={loginAsGuest}
          />
        ) : (
          <div className="space-y-4">
            {/* Top row: Telemetry + Radio Settings | Node Info + Neighbors */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="flex flex-col gap-4">
                <NodeInfoPane
                  data={paneData.nodeInfo}
                  state={paneStates.nodeInfo}
                  onRefresh={() => refreshPane('nodeInfo')}
                  disabled={anyLoading}
                />
                <TelemetryPane
                  data={paneData.status}
                  state={paneStates.status}
                  onRefresh={() => refreshPane('status')}
                  disabled={anyLoading}
                />
                <RadioSettingsPane
                  data={paneData.radioSettings}
                  state={paneStates.radioSettings}
                  onRefresh={() => refreshPane('radioSettings')}
                  disabled={anyLoading}
                  advertData={paneData.advertIntervals}
                  advertState={paneStates.advertIntervals}
                  onRefreshAdvert={() => refreshPane('advertIntervals')}
                />
                <LppTelemetryPane
                  data={paneData.lppTelemetry}
                  state={paneStates.lppTelemetry}
                  onRefresh={() => refreshPane('lppTelemetry')}
                  disabled={anyLoading}
                />
              </div>
              <div className="flex flex-col gap-4">
                <NeighborsPane
                  data={paneData.neighbors}
                  state={paneStates.neighbors}
                  onRefresh={() => refreshPane('neighbors')}
                  disabled={anyLoading}
                  contacts={contacts}
                  nodeInfo={paneData.nodeInfo}
                  nodeInfoState={paneStates.nodeInfo}
                  repeaterName={conversation.name}
                />
              </div>
            </div>

            {/* Remaining panes: ACL | Owner Info + Actions */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <AclPane
                data={paneData.acl}
                state={paneStates.acl}
                onRefresh={() => refreshPane('acl')}
                disabled={anyLoading}
              />
              <div className="flex flex-col gap-4">
                <OwnerInfoPane
                  data={paneData.ownerInfo}
                  state={paneStates.ownerInfo}
                  onRefresh={() => refreshPane('ownerInfo')}
                  disabled={anyLoading}
                />
                <ActionsPane
                  onSendZeroHopAdvert={sendZeroHopAdvert}
                  onSendFloodAdvert={sendFloodAdvert}
                  onSyncClock={syncClock}
                  onReboot={rebootRepeater}
                  consoleLoading={consoleLoading}
                />
              </div>
            </div>

            {/* Console — full width */}
            <ConsolePane
              history={consoleHistory}
              loading={consoleLoading}
              onSend={sendConsoleCommand}
            />
          </div>
        )}
      </div>
    </div>
  );
}
