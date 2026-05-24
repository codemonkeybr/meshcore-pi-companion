import { useCallback, useEffect, useMemo, useState } from 'react';

import { api } from '../api';
import { toast } from './ui/sonner';
import { Button } from './ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from './ui/sheet';
import type {
  Contact,
  PaneState,
  RepeaterAclResponse,
  RepeaterLppTelemetryResponse,
  RepeaterStatusResponse,
} from '../types';
import { TelemetryPane } from './repeater/RepeaterTelemetryPane';
import { AclPane } from './repeater/RepeaterAclPane';
import { LppTelemetryPane } from './repeater/RepeaterLppTelemetryPane';
import { ConsolePane } from './repeater/RepeaterConsolePane';
import { RepeaterLogin } from './RepeaterLogin';
import { ServerLoginStatusBanner } from './ServerLoginStatusBanner';
import { useRememberedServerPassword } from '../hooks/useRememberedServerPassword';
import {
  buildServerLoginAttemptFromError,
  buildServerLoginAttemptFromResponse,
  type ServerLoginAttemptState,
} from '../utils/serverLoginState';

interface RoomServerPanelProps {
  contact: Contact;
  onAuthenticatedChange?: (authenticated: boolean) => void;
}

type RoomPaneKey = 'status' | 'acl' | 'lppTelemetry';

type RoomPaneData = {
  status: RepeaterStatusResponse | null;
  acl: RepeaterAclResponse | null;
  lppTelemetry: RepeaterLppTelemetryResponse | null;
};

type RoomPaneStates = Record<RoomPaneKey, PaneState>;

type ConsoleEntry = {
  command: string;
  response: string;
  timestamp: number;
  outgoing: boolean;
};

const INITIAL_PANE_STATE: PaneState = {
  loading: false,
  attempt: 0,
  error: null,
  fetched_at: null,
};

function createInitialPaneStates(): RoomPaneStates {
  return {
    status: { ...INITIAL_PANE_STATE },
    acl: { ...INITIAL_PANE_STATE },
    lppTelemetry: { ...INITIAL_PANE_STATE },
  };
}

function createInitialPaneData(): RoomPaneData {
  return { status: null, acl: null, lppTelemetry: null };
}

// ---------------------------------------------------------------------------
// In-memory LRU cache so room login state survives conversation switches
// ---------------------------------------------------------------------------

interface RoomCacheEntry {
  authenticated: boolean;
  loginError: string | null;
  lastLoginAttempt: ServerLoginAttemptState | null;
  paneData: RoomPaneData;
  paneStates: RoomPaneStates;
  consoleHistory: ConsoleEntry[];
}

const MAX_CACHED_ROOMS = 8;
const roomCache = new Map<string, RoomCacheEntry>();

function getCachedRoom(publicKey: string): RoomCacheEntry | null {
  const cached = roomCache.get(publicKey);
  if (!cached) return null;
  // Touch for LRU
  roomCache.delete(publicKey);
  roomCache.set(publicKey, cached);
  return {
    ...cached,
    paneData: { ...cached.paneData },
    paneStates: {
      status: { ...cached.paneStates.status, loading: false },
      acl: { ...cached.paneStates.acl, loading: false },
      lppTelemetry: { ...cached.paneStates.lppTelemetry, loading: false },
    },
    consoleHistory: cached.consoleHistory.map((e) => ({ ...e })),
  };
}

function setCachedRoom(publicKey: string, entry: RoomCacheEntry) {
  roomCache.delete(publicKey);
  roomCache.set(publicKey, {
    ...entry,
    paneData: { ...entry.paneData },
    paneStates: {
      status: { ...entry.paneStates.status, loading: false },
      acl: { ...entry.paneStates.acl, loading: false },
      lppTelemetry: { ...entry.paneStates.lppTelemetry, loading: false },
    },
    consoleHistory: entry.consoleHistory.map((e) => ({ ...e })),
  });
  if (roomCache.size > MAX_CACHED_ROOMS) {
    const lruKey = roomCache.keys().next().value as string | undefined;
    if (lruKey) roomCache.delete(lruKey);
  }
}

export function resetRoomCacheForTests() {
  roomCache.clear();
}

export function RoomServerPanel({ contact, onAuthenticatedChange }: RoomServerPanelProps) {
  const { password, setPassword, rememberPassword, setRememberPassword, persistAfterLogin } =
    useRememberedServerPassword('room', contact.public_key);

  const cached = useMemo(() => getCachedRoom(contact.public_key), [contact.public_key]);

  const [loginLoading, setLoginLoading] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(cached?.loginError ?? null);
  const [authenticated, setAuthenticated] = useState(cached?.authenticated ?? false);
  const [lastLoginAttempt, setLastLoginAttempt] = useState<ServerLoginAttemptState | null>(
    cached?.lastLoginAttempt ?? null
  );
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [paneData, setPaneData] = useState<RoomPaneData>(cached?.paneData ?? createInitialPaneData);
  const [paneStates, setPaneStates] = useState<RoomPaneStates>(
    cached?.paneStates ?? createInitialPaneStates
  );
  const [consoleHistory, setConsoleHistory] = useState<ConsoleEntry[]>(
    cached?.consoleHistory ?? []
  );
  const [consoleLoading, setConsoleLoading] = useState(false);

  // Persist to cache on every state change
  useEffect(() => {
    setCachedRoom(contact.public_key, {
      authenticated,
      loginError,
      lastLoginAttempt,
      paneData,
      paneStates,
      consoleHistory,
    });
  }, [
    contact.public_key,
    authenticated,
    loginError,
    lastLoginAttempt,
    paneData,
    paneStates,
    consoleHistory,
  ]);

  useEffect(() => {
    onAuthenticatedChange?.(authenticated);
  }, [authenticated, onAuthenticatedChange]);

  const refreshPane = useCallback(
    async <K extends RoomPaneKey>(pane: K, loader: () => Promise<RoomPaneData[K]>) => {
      setPaneStates((prev) => ({
        ...prev,
        [pane]: {
          ...prev[pane],
          loading: true,
          attempt: prev[pane].attempt + 1,
          error: null,
        },
      }));

      try {
        const data = await loader();
        setPaneData((prev) => ({ ...prev, [pane]: data }));
        setPaneStates((prev) => ({
          ...prev,
          [pane]: {
            loading: false,
            attempt: prev[pane].attempt,
            error: null,
            fetched_at: Date.now(),
          },
        }));
      } catch (err) {
        setPaneStates((prev) => ({
          ...prev,
          [pane]: {
            ...prev[pane],
            loading: false,
            error: err instanceof Error ? err.message : 'Unknown error',
          },
        }));
      }
    },
    []
  );

  const performLogin = useCallback(
    async (nextPassword: string, method: 'password' | 'blank') => {
      if (loginLoading) return;

      setLoginLoading(true);
      setLoginError(null);
      try {
        const result = await api.roomLogin(contact.public_key, nextPassword);
        setLastLoginAttempt(buildServerLoginAttemptFromResponse(method, result, 'room server'));
        setAuthenticated(true);
        if (result.authenticated) {
          toast.success('Login confirmed by the room server.');
        } else {
          toast.warning("Couldn't confirm room login", {
            description:
              result.message ??
              'No confirmation came back from the room server. You can still open tools and try again.',
          });
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Unknown error';
        setLastLoginAttempt(buildServerLoginAttemptFromError(method, message, 'room server'));
        setAuthenticated(true);
        setLoginError(message);
        toast.error('Room login request failed', {
          description: `${message}. You can still open tools and retry the login from here.`,
        });
      } finally {
        setLoginLoading(false);
      }
    },
    [contact.public_key, loginLoading]
  );

  const handleLogin = useCallback(
    async (nextPassword: string) => {
      await performLogin(nextPassword, 'password');
      persistAfterLogin(nextPassword);
    },
    [performLogin, persistAfterLogin]
  );

  const handleLoginAsGuest = useCallback(async () => {
    await performLogin('', 'blank');
    persistAfterLogin('');
  }, [performLogin, persistAfterLogin]);

  const handleConsoleCommand = useCallback(
    async (command: string) => {
      setConsoleLoading(true);
      const timestamp = Date.now();
      setConsoleHistory((prev) => [
        ...prev,
        { command, response: command, timestamp, outgoing: true },
      ]);
      try {
        const response = await api.sendRepeaterCommand(contact.public_key, command);
        setConsoleHistory((prev) => [
          ...prev,
          {
            command,
            response: response.response,
            timestamp: Date.now(),
            outgoing: false,
          },
        ]);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Unknown error';
        setConsoleHistory((prev) => [
          ...prev,
          {
            command,
            response: `(error) ${message}`,
            timestamp: Date.now(),
            outgoing: false,
          },
        ]);
      } finally {
        setConsoleLoading(false);
      }
    },
    [contact.public_key]
  );

  const panelTitle = useMemo(() => contact.name || contact.public_key.slice(0, 12), [contact]);
  const showLoginFailureState =
    lastLoginAttempt !== null && lastLoginAttempt.outcome !== 'confirmed';

  if (!authenticated) {
    return (
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex w-full max-w-sm flex-col gap-4">
          <div className="rounded-md border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-warning">
            Room server access is experimental and in public alpha. Please report any issues on{' '}
            <a
              href="https://github.com/jkingsman/Remote-Terminal-for-MeshCore/issues"
              target="_blank"
              rel="noreferrer"
              className="font-medium underline underline-offset-2 hover:text-warning/80"
            >
              GitHub
            </a>
            .
          </div>
          <RepeaterLogin
            repeaterName={panelTitle}
            loading={loginLoading}
            error={loginError}
            password={password}
            onPasswordChange={setPassword}
            rememberPassword={rememberPassword}
            onRememberPasswordChange={setRememberPassword}
            onLogin={handleLogin}
            onLoginAsGuest={handleLoginAsGuest}
            description="Log in with the room password or use ACL/guest access to enter this room server"
            passwordPlaceholder="Room server password..."
            guestLabel="Login with Existing Access / Guest"
          />
        </div>
      </div>
    );
  }

  return (
    <section className="border-b border-border bg-muted/20 px-4 py-3">
      <div className="space-y-3">
        {showLoginFailureState ? (
          <ServerLoginStatusBanner
            attempt={lastLoginAttempt}
            loading={loginLoading}
            canRetryPassword={password.trim().length > 0}
            onRetryPassword={() => handleLogin(password)}
            onRetryBlank={handleLoginAsGuest}
            blankRetryLabel="Retry Existing-Access Login"
            showRetryActions={false}
          />
        ) : null}
        <div className="flex flex-wrap items-center justify-between gap-2">
          {showLoginFailureState ? (
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void handleLogin(password)}
                disabled={loginLoading || password.trim().length === 0}
              >
                Retry Password Login
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleLoginAsGuest}
                disabled={loginLoading}
              >
                Retry Existing-Access Login
              </Button>
            </div>
          ) : (
            <div />
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setAdvancedOpen((prev) => !prev)}
          >
            {advancedOpen ? 'Hide Tools' : 'Show Tools'}
          </Button>
        </div>
      </div>
      <Sheet open={advancedOpen} onOpenChange={setAdvancedOpen}>
        <SheetContent side="right" className="w-full sm:max-w-4xl p-0 flex flex-col">
          <SheetHeader className="sr-only">
            <SheetTitle>Room Server Tools</SheetTitle>
            <SheetDescription>
              Room server telemetry, ACL tools, sensor data, and CLI console
            </SheetDescription>
          </SheetHeader>
          <div className="border-b border-border px-4 py-3 pr-14">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <h2 className="truncate text-base font-semibold">Room Server Tools</h2>
                <p className="text-sm text-muted-foreground">{panelTitle}</p>
              </div>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-4">
            <div className="grid gap-3 xl:grid-cols-2">
              <TelemetryPane
                data={paneData.status}
                state={paneStates.status}
                onRefresh={() => refreshPane('status', () => api.roomStatus(contact.public_key))}
              />
              <AclPane
                data={paneData.acl}
                state={paneStates.acl}
                onRefresh={() => refreshPane('acl', () => api.roomAcl(contact.public_key))}
              />
              <LppTelemetryPane
                data={paneData.lppTelemetry}
                state={paneStates.lppTelemetry}
                onRefresh={() =>
                  refreshPane('lppTelemetry', () => api.roomLppTelemetry(contact.public_key))
                }
              />
              <ConsolePane
                history={consoleHistory}
                loading={consoleLoading}
                onSend={handleConsoleCommand}
              />
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </section>
  );
}
