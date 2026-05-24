import { useState, useCallback, useRef, useEffect } from 'react';
import { api } from '../api';
import { toast } from '../components/ui/sonner';
import type {
  Conversation,
  PaneName,
  PaneState,
  RepeaterStatusResponse,
  RepeaterNeighborsResponse,
  RepeaterAclResponse,
  RepeaterNodeInfoResponse,
  RepeaterRadioSettingsResponse,
  RepeaterAdvertIntervalsResponse,
  RepeaterOwnerInfoResponse,
  RepeaterLppTelemetryResponse,
  CommandResponse,
} from '../types';
import {
  buildServerLoginAttemptFromError,
  buildServerLoginAttemptFromResponse,
  type ServerLoginAttemptState,
} from '../utils/serverLoginState';

const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 2000;
const MAX_CACHED_REPEATERS = 20;

interface ConsoleEntry {
  command: string;
  response: string;
  timestamp: number;
  outgoing: boolean;
}

interface PaneData {
  status: RepeaterStatusResponse | null;
  nodeInfo: RepeaterNodeInfoResponse | null;
  neighbors: RepeaterNeighborsResponse | null;
  acl: RepeaterAclResponse | null;
  radioSettings: RepeaterRadioSettingsResponse | null;
  advertIntervals: RepeaterAdvertIntervalsResponse | null;
  ownerInfo: RepeaterOwnerInfoResponse | null;
  lppTelemetry: RepeaterLppTelemetryResponse | null;
}

interface RepeaterDashboardCacheEntry {
  loggedIn: boolean;
  loginError: string | null;
  lastLoginAttempt: ServerLoginAttemptState | null;
  paneData: PaneData;
  paneStates: Record<PaneName, PaneState>;
  consoleHistory: ConsoleEntry[];
}

const INITIAL_PANE_STATE: PaneState = { loading: false, attempt: 0, error: null, fetched_at: null };

function createInitialPaneStates(): Record<PaneName, PaneState> {
  return {
    status: { ...INITIAL_PANE_STATE },
    nodeInfo: { ...INITIAL_PANE_STATE },
    neighbors: { ...INITIAL_PANE_STATE },
    acl: { ...INITIAL_PANE_STATE },
    radioSettings: { ...INITIAL_PANE_STATE },
    advertIntervals: { ...INITIAL_PANE_STATE },
    ownerInfo: { ...INITIAL_PANE_STATE },
    lppTelemetry: { ...INITIAL_PANE_STATE },
  };
}

function createInitialPaneData(): PaneData {
  return {
    status: null,
    nodeInfo: null,
    neighbors: null,
    acl: null,
    radioSettings: null,
    advertIntervals: null,
    ownerInfo: null,
    lppTelemetry: null,
  };
}

const repeaterDashboardCache = new Map<string, RepeaterDashboardCacheEntry>();

function getLoginToastTitle(status: string): string {
  switch (status) {
    case 'timeout':
      return 'Login confirmation not heard';
    case 'error':
      return 'Login not confirmed';
    default:
      return 'Repeater login not confirmed';
  }
}

function clonePaneData(data: PaneData): PaneData {
  return { ...data };
}

function normalizePaneStates(paneStates: Record<PaneName, PaneState>): Record<PaneName, PaneState> {
  return {
    status: { ...paneStates.status, loading: false },
    nodeInfo: { ...paneStates.nodeInfo, loading: false },
    neighbors: { ...paneStates.neighbors, loading: false },
    acl: { ...paneStates.acl, loading: false },
    radioSettings: { ...paneStates.radioSettings, loading: false },
    advertIntervals: { ...paneStates.advertIntervals, loading: false },
    ownerInfo: { ...paneStates.ownerInfo, loading: false },
    lppTelemetry: { ...paneStates.lppTelemetry, loading: false },
  };
}

function cloneConsoleHistory(consoleHistory: ConsoleEntry[]): ConsoleEntry[] {
  return consoleHistory.map((entry) => ({ ...entry }));
}

function getCachedState(publicKey: string | null): RepeaterDashboardCacheEntry | null {
  if (!publicKey) return null;
  const cached = repeaterDashboardCache.get(publicKey);
  if (!cached) return null;

  repeaterDashboardCache.delete(publicKey);
  repeaterDashboardCache.set(publicKey, cached);

  return {
    loggedIn: cached.loggedIn,
    loginError: cached.loginError,
    lastLoginAttempt: cached.lastLoginAttempt,
    paneData: clonePaneData(cached.paneData),
    paneStates: normalizePaneStates(cached.paneStates),
    consoleHistory: cloneConsoleHistory(cached.consoleHistory),
  };
}

function cacheState(publicKey: string, entry: RepeaterDashboardCacheEntry) {
  repeaterDashboardCache.delete(publicKey);
  repeaterDashboardCache.set(publicKey, {
    loggedIn: entry.loggedIn,
    loginError: entry.loginError,
    lastLoginAttempt: entry.lastLoginAttempt,
    paneData: clonePaneData(entry.paneData),
    paneStates: normalizePaneStates(entry.paneStates),
    consoleHistory: cloneConsoleHistory(entry.consoleHistory),
  });

  if (repeaterDashboardCache.size > MAX_CACHED_REPEATERS) {
    const lruKey = repeaterDashboardCache.keys().next().value as string | undefined;
    if (lruKey) {
      repeaterDashboardCache.delete(lruKey);
    }
  }
}

export function resetRepeaterDashboardCacheForTests() {
  repeaterDashboardCache.clear();
}

// Maps pane name to the API call
function fetchPaneData(publicKey: string, pane: PaneName) {
  switch (pane) {
    case 'status':
      return api.repeaterStatus(publicKey);
    case 'nodeInfo':
      return api.repeaterNodeInfo(publicKey);
    case 'neighbors':
      return api.repeaterNeighbors(publicKey);
    case 'acl':
      return api.repeaterAcl(publicKey);
    case 'radioSettings':
      return api.repeaterRadioSettings(publicKey);
    case 'advertIntervals':
      return api.repeaterAdvertIntervals(publicKey);
    case 'ownerInfo':
      return api.repeaterOwnerInfo(publicKey);
    case 'lppTelemetry':
      return api.repeaterLppTelemetry(publicKey);
  }
}

export interface UseRepeaterDashboardResult {
  loggedIn: boolean;
  loginLoading: boolean;
  loginError: string | null;
  lastLoginAttempt: ServerLoginAttemptState | null;
  paneData: PaneData;
  paneStates: Record<PaneName, PaneState>;
  consoleHistory: ConsoleEntry[];
  consoleLoading: boolean;
  login: (password: string) => Promise<void>;
  loginAsGuest: () => Promise<void>;
  refreshPane: (pane: PaneName) => Promise<void>;
  loadAll: () => Promise<void>;
  sendConsoleCommand: (command: string) => Promise<void>;
  sendZeroHopAdvert: () => Promise<void>;
  sendFloodAdvert: () => Promise<void>;
  rebootRepeater: () => Promise<void>;
  syncClock: () => Promise<void>;
}

interface UseRepeaterDashboardOptions {
  hasAdvertLocation?: boolean;
}

export function useRepeaterDashboard(
  activeConversation: Conversation | null,
  options: UseRepeaterDashboardOptions = {}
): UseRepeaterDashboardResult {
  const conversationId =
    activeConversation && activeConversation.type === 'contact' ? activeConversation.id : null;
  const cachedState = getCachedState(conversationId);

  const [loggedIn, setLoggedIn] = useState(cachedState?.loggedIn ?? false);
  const [loginLoading, setLoginLoading] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(cachedState?.loginError ?? null);
  const [lastLoginAttempt, setLastLoginAttempt] = useState<ServerLoginAttemptState | null>(
    cachedState?.lastLoginAttempt ?? null
  );

  const [paneData, setPaneData] = useState<PaneData>(
    cachedState?.paneData ?? createInitialPaneData
  );
  const [paneStates, setPaneStates] = useState<Record<PaneName, PaneState>>(
    cachedState?.paneStates ?? createInitialPaneStates
  );
  const paneDataRef = useRef<PaneData>(cachedState?.paneData ?? createInitialPaneData());
  const paneStatesRef = useRef<Record<PaneName, PaneState>>(
    cachedState?.paneStates ?? createInitialPaneStates()
  );

  const [consoleHistory, setConsoleHistory] = useState<ConsoleEntry[]>(
    cachedState?.consoleHistory ?? []
  );
  const [consoleLoading, setConsoleLoading] = useState(false);

  // Track which conversation we're operating on to avoid stale updates after
  // unmount. Initialised from activeConversation because the parent renders
  // <RepeaterDashboard key={id}>, so this hook only ever sees one conversation.
  const activeIdRef = useRef(activeConversation?.id ?? null);

  // Guard against setting state after unmount (retry timers firing late)
  const mountedRef = useRef(true);
  useEffect(() => {
    activeIdRef.current = conversationId;
  }, [conversationId]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!conversationId) return;
    cacheState(conversationId, {
      loggedIn,
      loginError,
      lastLoginAttempt,
      paneData,
      paneStates,
      consoleHistory,
    });
  }, [
    consoleHistory,
    conversationId,
    loggedIn,
    loginError,
    lastLoginAttempt,
    paneData,
    paneStates,
  ]);

  useEffect(() => {
    paneDataRef.current = paneData;
  }, [paneData]);

  useEffect(() => {
    paneStatesRef.current = paneStates;
  }, [paneStates]);

  const getPublicKey = useCallback((): string | null => {
    if (!activeConversation || activeConversation.type !== 'contact') return null;
    return activeConversation.id;
  }, [activeConversation]);

  const login = useCallback(
    async (password: string) => {
      const publicKey = getPublicKey();
      if (!publicKey) return;
      const conversationId = publicKey;
      const method = password.trim().length > 0 ? 'password' : 'blank';

      setLoginLoading(true);
      setLoginError(null);
      try {
        const result = await api.repeaterLogin(publicKey, password);
        if (activeIdRef.current !== conversationId) return;
        setLastLoginAttempt(buildServerLoginAttemptFromResponse(method, result, 'repeater'));
        setLoggedIn(true);
        if (!result.authenticated) {
          const msg = result.message ?? 'Repeater login was not confirmed';
          setLoginError(msg);
          toast.error(getLoginToastTitle(result.status), { description: msg });
        }
      } catch (err) {
        if (activeIdRef.current !== conversationId) return;
        const msg = err instanceof Error ? err.message : 'Login failed';
        setLastLoginAttempt(buildServerLoginAttemptFromError(method, msg, 'repeater'));
        setLoggedIn(true);
        setLoginError(msg);
        toast.error('Login request failed', {
          description: `${msg}. The dashboard is still available, but repeater operations may fail until a login succeeds.`,
        });
      } finally {
        if (activeIdRef.current === conversationId) {
          setLoginLoading(false);
        }
      }
    },
    [getPublicKey]
  );

  const loginAsGuest = useCallback(async () => {
    await login('');
  }, [login]);

  const refreshPane = useCallback(
    async (pane: PaneName) => {
      const publicKey = getPublicKey();
      if (!publicKey) return;
      const conversationId = publicKey;

      if (pane === 'neighbors' && !options.hasAdvertLocation) {
        const nodeInfoState = paneStatesRef.current.nodeInfo;
        const nodeInfoData = paneDataRef.current.nodeInfo;
        const needsNodeInfoPrefetch =
          nodeInfoState.error !== null ||
          (nodeInfoState.fetched_at == null && nodeInfoData == null);

        if (needsNodeInfoPrefetch) {
          await refreshPane('nodeInfo');
          if (!mountedRef.current || activeIdRef.current !== conversationId) return;
        }
      }

      for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
        if (!mountedRef.current || activeIdRef.current !== conversationId) return;

        const loadingState = {
          loading: true,
          attempt,
          error: null,
          fetched_at: paneStatesRef.current[pane].fetched_at ?? null,
        };
        paneStatesRef.current = {
          ...paneStatesRef.current,
          [pane]: loadingState,
        };
        setPaneStates((prev) => ({
          ...prev,
          [pane]: loadingState,
        }));

        try {
          const data = await fetchPaneData(publicKey, pane);
          if (!mountedRef.current || activeIdRef.current !== conversationId) return;

          paneDataRef.current = {
            ...paneDataRef.current,
            [pane]: data,
          };
          const successState = {
            loading: false,
            attempt,
            error: null,
            fetched_at: Date.now(),
          };
          paneStatesRef.current = {
            ...paneStatesRef.current,
            [pane]: successState,
          };

          setPaneData((prev) => ({ ...prev, [pane]: data }));
          setPaneStates((prev) => ({
            ...prev,
            [pane]: successState,
          }));
          return; // Success
        } catch (err) {
          if (!mountedRef.current || activeIdRef.current !== conversationId) return;

          const msg = err instanceof Error ? err.message : 'Request failed';

          if (attempt === MAX_RETRIES) {
            const errorState = {
              loading: false,
              attempt,
              error: msg,
              fetched_at: paneStatesRef.current[pane].fetched_at ?? null,
            };
            paneStatesRef.current = {
              ...paneStatesRef.current,
              [pane]: errorState,
            };
            setPaneStates((prev) => ({
              ...prev,
              [pane]: errorState,
            }));
            toast.error(`Failed to fetch ${pane}`, { description: msg });
          } else {
            // Wait before retrying
            await new Promise((r) => setTimeout(r, RETRY_DELAY_MS));
          }
        }
      }
    },
    [getPublicKey, options.hasAdvertLocation]
  );

  const loadAll = useCallback(async () => {
    const panes: PaneName[] = [
      'status',
      'nodeInfo',
      'neighbors',
      'radioSettings',
      'acl',
      'advertIntervals',
      'ownerInfo',
      'lppTelemetry',
    ];
    // Serial execution — parallel calls just queue behind the radio lock anyway
    for (const pane of panes) {
      await refreshPane(pane);
    }
  }, [refreshPane]);

  const sendConsoleCommand = useCallback(
    async (command: string) => {
      const publicKey = getPublicKey();
      if (!publicKey) return;
      const conversationId = publicKey;

      const now = Math.floor(Date.now() / 1000);

      // Add outgoing command entry
      setConsoleHistory((prev) => [
        ...prev,
        { command, response: '', timestamp: now, outgoing: true },
      ]);

      setConsoleLoading(true);
      try {
        const result: CommandResponse = await api.sendRepeaterCommand(publicKey, command);
        if (activeIdRef.current !== conversationId) return;

        setConsoleHistory((prev) => [
          ...prev,
          {
            command,
            response: result.response,
            timestamp: result.sender_timestamp ?? now,
            outgoing: false,
          },
        ]);
      } catch (err) {
        if (activeIdRef.current !== conversationId) return;
        const msg = err instanceof Error ? err.message : 'Command failed';
        setConsoleHistory((prev) => [
          ...prev,
          { command, response: `Error: ${msg}`, timestamp: now, outgoing: false },
        ]);
      } finally {
        if (activeIdRef.current === conversationId) {
          setConsoleLoading(false);
        }
      }
    },
    [getPublicKey]
  );

  const sendZeroHopAdvert = useCallback(async () => {
    await sendConsoleCommand('advert.zerohop');
  }, [sendConsoleCommand]);

  const sendFloodAdvert = useCallback(async () => {
    await sendConsoleCommand('advert');
  }, [sendConsoleCommand]);

  const rebootRepeater = useCallback(async () => {
    await sendConsoleCommand('reboot');
  }, [sendConsoleCommand]);

  const syncClock = useCallback(async () => {
    const epochSeconds = Math.floor(Date.now() / 1000);
    await sendConsoleCommand(`time ${epochSeconds}`);
  }, [sendConsoleCommand]);

  return {
    loggedIn,
    loginLoading,
    loginError,
    lastLoginAttempt,
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
  };
}
