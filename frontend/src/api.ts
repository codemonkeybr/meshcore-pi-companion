import type {
  AppSettings,
  AppSettingsUpdate,
  Channel,
  ChannelDetail,
  CommandResponse,
  Contact,
  ContactAnalytics,
  ContactAdvertPathSummary,
  FanoutConfig,
  Favorite,
  HealthStatus,
  MaintenanceResult,
  Message,
  MessagesAroundResponse,
  MigratePreferencesRequest,
  MigratePreferencesResponse,
  RadioConfig,
  RadioConfigUpdate,
  RepeaterAclResponse,
  RepeaterAdvertIntervalsResponse,
  RepeaterLoginResponse,
  RepeaterLppTelemetryResponse,
  RepeaterNeighborsResponse,
  RepeaterNodeInfoResponse,
  RepeaterOwnerInfoResponse,
  RepeaterRadioSettingsResponse,
  RepeaterStatusResponse,
  StatisticsResponse,
  TraceResponse,
  UnreadCounts,
} from './types';

const API_BASE = '/api';

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const hasBody = options?.body !== undefined;
  const res = await fetch(`${API_BASE}${url}`, {
    ...options,
    headers: {
      ...(hasBody && { 'Content-Type': 'application/json' }),
      ...options?.headers,
    },
  });
  if (!res.ok) {
    const errorText = await res.text();
    // FastAPI returns errors as {"detail": "message"}, extract the message
    let errorMessage = errorText || res.statusText;
    try {
      const errorJson = JSON.parse(errorText);
      if (errorJson.detail) {
        errorMessage = errorJson.detail;
      }
    } catch {
      // Not JSON, use raw text
    }
    throw new Error(errorMessage);
  }
  return res.json();
}

/** Check if an error is an AbortError (request was cancelled) */
export function isAbortError(err: unknown): boolean {
  // DOMException is thrown by fetch when aborted, and it's not an Error subclass
  if (err instanceof DOMException && err.name === 'AbortError') {
    return true;
  }
  // Also check for Error with AbortError name (for compatibility)
  return err instanceof Error && err.name === 'AbortError';
}

interface DecryptResult {
  started: boolean;
  total_packets: number;
  message: string;
}

export const api = {
  // Health
  getHealth: () => fetchJson<HealthStatus>('/health'),

  // Radio config
  getRadioConfig: () => fetchJson<RadioConfig>('/radio/config'),
  updateRadioConfig: (config: RadioConfigUpdate) =>
    fetchJson<RadioConfig>('/radio/config', {
      method: 'PATCH',
      body: JSON.stringify(config),
    }),
  setPrivateKey: (privateKey: string) =>
    fetchJson<{ status: string }>('/radio/private-key', {
      method: 'PUT',
      body: JSON.stringify({ private_key: privateKey }),
    }),
  sendAdvertisement: () =>
    fetchJson<{ status: string }>('/radio/advertise', {
      method: 'POST',
    }),
  rebootRadio: () =>
    fetchJson<{ status: string; message: string }>('/radio/reboot', {
      method: 'POST',
    }),
  disconnectRadio: () =>
    fetchJson<{ status: string; message: string; connected: boolean; paused: boolean }>(
      '/radio/disconnect',
      {
        method: 'POST',
      }
    ),
  reconnectRadio: () =>
    fetchJson<{ status: string; message: string; connected: boolean }>('/radio/reconnect', {
      method: 'POST',
    }),

  // Contacts
  getContacts: (limit = 100, offset = 0) =>
    fetchJson<Contact[]>(`/contacts?limit=${limit}&offset=${offset}`),
  getRepeaterAdvertPaths: (limitPerRepeater = 10) =>
    fetchJson<ContactAdvertPathSummary[]>(
      `/contacts/repeaters/advert-paths?limit_per_repeater=${limitPerRepeater}`
    ),
  getContactAnalytics: (params: { publicKey?: string; name?: string }) => {
    const searchParams = new URLSearchParams();
    if (params.publicKey) searchParams.set('public_key', params.publicKey);
    if (params.name) searchParams.set('name', params.name);
    return fetchJson<ContactAnalytics>(`/contacts/analytics?${searchParams.toString()}`);
  },
  deleteContact: (publicKey: string) =>
    fetchJson<{ status: string }>(`/contacts/${publicKey}`, {
      method: 'DELETE',
    }),
  createContact: (publicKey: string, name?: string, tryHistorical?: boolean) =>
    fetchJson<Contact>('/contacts', {
      method: 'POST',
      body: JSON.stringify({ public_key: publicKey, name, try_historical: tryHistorical }),
    }),
  markContactRead: (publicKey: string) =>
    fetchJson<{ status: string; public_key: string }>(`/contacts/${publicKey}/mark-read`, {
      method: 'POST',
    }),
  sendRepeaterCommand: (publicKey: string, command: string) =>
    fetchJson<CommandResponse>(`/contacts/${publicKey}/command`, {
      method: 'POST',
      body: JSON.stringify({ command }),
    }),
  requestTrace: (publicKey: string) =>
    fetchJson<TraceResponse>(`/contacts/${publicKey}/trace`, {
      method: 'POST',
    }),
  setContactRoutingOverride: (publicKey: string, route: string) =>
    fetchJson<{ status: string; public_key: string }>(`/contacts/${publicKey}/routing-override`, {
      method: 'POST',
      body: JSON.stringify({ route }),
    }),

  // Channels
  getChannels: () => fetchJson<Channel[]>('/channels'),
  createChannel: (name: string, key?: string) =>
    fetchJson<Channel>('/channels', {
      method: 'POST',
      body: JSON.stringify({ name, key }),
    }),
  deleteChannel: (key: string) =>
    fetchJson<{ status: string }>(`/channels/${key}`, { method: 'DELETE' }),
  getChannelDetail: (key: string) => fetchJson<ChannelDetail>(`/channels/${key}/detail`),
  markChannelRead: (key: string) =>
    fetchJson<{ status: string; key: string }>(`/channels/${key}/mark-read`, {
      method: 'POST',
    }),
  setChannelFloodScopeOverride: (key: string, floodScopeOverride: string) =>
    fetchJson<Channel>(`/channels/${key}/flood-scope-override`, {
      method: 'POST',
      body: JSON.stringify({ flood_scope_override: floodScopeOverride }),
    }),

  // Messages
  getMessages: (
    params?: {
      limit?: number;
      offset?: number;
      type?: 'PRIV' | 'CHAN';
      conversation_key?: string;
      before?: number;
      before_id?: number;
      after?: number;
      after_id?: number;
      q?: string;
    },
    signal?: AbortSignal
  ) => {
    const searchParams = new URLSearchParams();
    if (params?.limit !== undefined) searchParams.set('limit', params.limit.toString());
    if (params?.offset !== undefined) searchParams.set('offset', params.offset.toString());
    if (params?.type) searchParams.set('type', params.type);
    if (params?.conversation_key) searchParams.set('conversation_key', params.conversation_key);
    if (params?.before !== undefined) searchParams.set('before', params.before.toString());
    if (params?.before_id !== undefined) searchParams.set('before_id', params.before_id.toString());
    if (params?.after !== undefined) searchParams.set('after', params.after.toString());
    if (params?.after_id !== undefined) searchParams.set('after_id', params.after_id.toString());
    if (params?.q) searchParams.set('q', params.q);
    const query = searchParams.toString();
    return fetchJson<Message[]>(`/messages${query ? `?${query}` : ''}`, { signal });
  },
  getMessagesAround: (
    messageId: number,
    type?: 'PRIV' | 'CHAN',
    conversationKey?: string,
    signal?: AbortSignal
  ) => {
    const searchParams = new URLSearchParams();
    if (type) searchParams.set('type', type);
    if (conversationKey) searchParams.set('conversation_key', conversationKey);
    const query = searchParams.toString();
    return fetchJson<MessagesAroundResponse>(
      `/messages/around/${messageId}${query ? `?${query}` : ''}`,
      { signal }
    );
  },
  sendDirectMessage: (destination: string, text: string) =>
    fetchJson<Message>('/messages/direct', {
      method: 'POST',
      body: JSON.stringify({ destination, text }),
    }),
  sendChannelMessage: (channelKey: string, text: string) =>
    fetchJson<Message>('/messages/channel', {
      method: 'POST',
      body: JSON.stringify({ channel_key: channelKey, text }),
    }),
  resendChannelMessage: (messageId: number, newTimestamp?: boolean) =>
    fetchJson<{ status: string; message_id: number }>(
      `/messages/channel/${messageId}/resend${newTimestamp ? '?new_timestamp=true' : ''}`,
      { method: 'POST' }
    ),

  // Packets
  getUndecryptedPacketCount: () => fetchJson<{ count: number }>('/packets/undecrypted/count'),
  decryptHistoricalPackets: (params: {
    key_type: 'channel' | 'contact';
    channel_key?: string;
    channel_name?: string;
  }) =>
    fetchJson<DecryptResult>('/packets/decrypt/historical', {
      method: 'POST',
      body: JSON.stringify(params),
    }),
  runMaintenance: (options: { pruneUndecryptedDays?: number; purgeLinkedRawPackets?: boolean }) =>
    fetchJson<MaintenanceResult>('/packets/maintenance', {
      method: 'POST',
      body: JSON.stringify({
        ...(options.pruneUndecryptedDays !== undefined && {
          prune_undecrypted_days: options.pruneUndecryptedDays,
        }),
        ...(options.purgeLinkedRawPackets !== undefined && {
          purge_linked_raw_packets: options.purgeLinkedRawPackets,
        }),
      }),
    }),

  // Read State
  getUnreads: () => fetchJson<UnreadCounts>('/read-state/unreads'),
  markAllRead: () =>
    fetchJson<{ status: string; timestamp: number }>('/read-state/mark-all-read', {
      method: 'POST',
    }),

  // App Settings
  getSettings: () => fetchJson<AppSettings>('/settings'),
  updateSettings: (settings: AppSettingsUpdate) =>
    fetchJson<AppSettings>('/settings', {
      method: 'PATCH',
      body: JSON.stringify(settings),
    }),

  // Block lists
  toggleBlockedKey: (key: string) =>
    fetchJson<AppSettings>('/settings/blocked-keys/toggle', {
      method: 'POST',
      body: JSON.stringify({ key }),
    }),
  toggleBlockedName: (name: string) =>
    fetchJson<AppSettings>('/settings/blocked-names/toggle', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),

  // Favorites
  toggleFavorite: (type: Favorite['type'], id: string) =>
    fetchJson<AppSettings>('/settings/favorites/toggle', {
      method: 'POST',
      body: JSON.stringify({ type, id }),
    }),

  // Preferences migration (one-time, from localStorage to database)
  migratePreferences: (request: MigratePreferencesRequest) =>
    fetchJson<MigratePreferencesResponse>('/settings/migrate', {
      method: 'POST',
      body: JSON.stringify(request),
    }),

  // Fanout
  getFanoutConfigs: () => fetchJson<FanoutConfig[]>('/fanout'),
  createFanoutConfig: (config: {
    type: string;
    name: string;
    config: Record<string, unknown>;
    scope: Record<string, unknown>;
    enabled?: boolean;
  }) =>
    fetchJson<FanoutConfig>('/fanout', {
      method: 'POST',
      body: JSON.stringify(config),
    }),
  updateFanoutConfig: (
    id: string,
    update: {
      name?: string;
      config?: Record<string, unknown>;
      scope?: Record<string, unknown>;
      enabled?: boolean;
    }
  ) =>
    fetchJson<FanoutConfig>(`/fanout/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(update),
    }),
  deleteFanoutConfig: (id: string) =>
    fetchJson<{ deleted: boolean }>(`/fanout/${id}`, {
      method: 'DELETE',
    }),

  // Statistics
  getStatistics: () => fetchJson<StatisticsResponse>('/statistics'),

  // Granular repeater endpoints
  repeaterLogin: (publicKey: string, password: string) =>
    fetchJson<RepeaterLoginResponse>(`/contacts/${publicKey}/repeater/login`, {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  repeaterStatus: (publicKey: string) =>
    fetchJson<RepeaterStatusResponse>(`/contacts/${publicKey}/repeater/status`, {
      method: 'POST',
    }),
  repeaterNeighbors: (publicKey: string) =>
    fetchJson<RepeaterNeighborsResponse>(`/contacts/${publicKey}/repeater/neighbors`, {
      method: 'POST',
    }),
  repeaterNodeInfo: (publicKey: string) =>
    fetchJson<RepeaterNodeInfoResponse>(`/contacts/${publicKey}/repeater/node-info`, {
      method: 'POST',
    }),
  repeaterAcl: (publicKey: string) =>
    fetchJson<RepeaterAclResponse>(`/contacts/${publicKey}/repeater/acl`, {
      method: 'POST',
    }),
  repeaterRadioSettings: (publicKey: string) =>
    fetchJson<RepeaterRadioSettingsResponse>(`/contacts/${publicKey}/repeater/radio-settings`, {
      method: 'POST',
    }),
  repeaterAdvertIntervals: (publicKey: string) =>
    fetchJson<RepeaterAdvertIntervalsResponse>(`/contacts/${publicKey}/repeater/advert-intervals`, {
      method: 'POST',
    }),
  repeaterOwnerInfo: (publicKey: string) =>
    fetchJson<RepeaterOwnerInfoResponse>(`/contacts/${publicKey}/repeater/owner-info`, {
      method: 'POST',
    }),
  repeaterLppTelemetry: (publicKey: string) =>
    fetchJson<RepeaterLppTelemetryResponse>(`/contacts/${publicKey}/repeater/lpp-telemetry`, {
      method: 'POST',
    }),
};
