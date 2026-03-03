import { toast } from './ui/sonner';
import { Button } from './ui/button';
import { RepeaterLogin } from './RepeaterLogin';
import { useRepeaterDashboard } from '../hooks/useRepeaterDashboard';
import { isFavorite } from '../utils/favorites';
import { ContactStatusInfo } from './ContactStatusInfo';
import type { Contact, Conversation, Favorite } from '../types';
import { TelemetryPane } from './repeater/RepeaterTelemetryPane';
import { NeighborsPane } from './repeater/RepeaterNeighborsPane';
import { AclPane } from './repeater/RepeaterAclPane';
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
  radioLat: number | null;
  radioLon: number | null;
  radioName: string | null;
  onTrace: () => void;
  onToggleFavorite: (type: 'channel' | 'contact', id: string) => void;
  onDeleteContact: (publicKey: string) => void;
}

export function RepeaterDashboard({
  conversation,
  contacts,
  favorites,
  radioLat,
  radioLon,
  radioName,
  onTrace,
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
    sendAdvert,
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
      <div className="flex justify-between items-center px-4 py-2.5 border-b border-border gap-2">
        <span className="flex flex-wrap items-baseline gap-x-2 min-w-0 flex-1">
          <span className="flex-shrink-0 font-semibold text-base">{conversation.name}</span>
          <span
            className="font-normal text-[11px] text-muted-foreground font-mono truncate cursor-pointer hover:text-primary transition-colors"
            onClick={() => {
              navigator.clipboard.writeText(conversation.id);
              toast.success('Contact key copied!');
            }}
            title="Click to copy"
          >
            {conversation.id}
          </span>
          {contact && <ContactStatusInfo contact={contact} ourLat={radioLat} ourLon={radioLon} />}
        </span>
        <div className="flex items-center gap-0.5 flex-shrink-0">
          {loggedIn && (
            <Button
              variant="outline"
              size="sm"
              onClick={loadAll}
              disabled={anyLoading}
              className="text-xs border-green-600 text-green-600 hover:bg-green-600/10 hover:text-green-600"
            >
              {anyLoading ? 'Loading...' : 'Load All'}
            </Button>
          )}
          <button
            className="p-1.5 rounded hover:bg-accent text-lg leading-none transition-colors"
            onClick={onTrace}
            title="Direct Trace"
          >
            &#x1F6CE;
          </button>
          <button
            className="p-1.5 rounded hover:bg-accent text-lg leading-none transition-colors"
            onClick={() => onToggleFavorite('contact', conversation.id)}
            title={isFav ? 'Remove from favorites' : 'Add to favorites'}
          >
            {isFav ? (
              <span className="text-amber-400">&#9733;</span>
            ) : (
              <span className="text-muted-foreground">&#9734;</span>
            )}
          </button>
          <button
            className="p-1.5 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive text-lg leading-none transition-colors"
            onClick={() => onDeleteContact(conversation.id)}
            title="Delete"
          >
            &#128465;
          </button>
        </div>
      </div>

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
            {/* Top row: Telemetry + Radio Settings | Neighbors (with expanding map) */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="flex flex-col gap-4">
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
              <NeighborsPane
                data={paneData.neighbors}
                state={paneStates.neighbors}
                onRefresh={() => refreshPane('neighbors')}
                disabled={anyLoading}
                contacts={contacts}
                radioLat={radioLat}
                radioLon={radioLon}
                radioName={radioName}
              />
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
                  onSendAdvert={sendAdvert}
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
