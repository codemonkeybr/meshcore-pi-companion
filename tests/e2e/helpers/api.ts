/**
 * Direct REST API helpers for E2E test setup and teardown.
 * These bypass the UI to set up preconditions and verify backend state.
 */

const BASE_URL = 'http://localhost:8001/api';

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${init?.method || 'GET'} ${path} returned ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

// --- Health ---

export interface HealthStatus {
  radio_connected: boolean;
  radio_initializing: boolean;
  connection_info: string | null;
}

export function getHealth(): Promise<HealthStatus> {
  return fetchJson('/health');
}

// --- Radio Config ---

export interface RadioConfig {
  name: string;
  public_key: string;
  lat: number;
  lon: number;
  tx_power: number;
  freq: number;
  bw: number;
  sf: number;
  cr: number;
}

export function getRadioConfig(): Promise<RadioConfig> {
  return fetchJson('/radio/config');
}

export function updateRadioConfig(patch: Partial<RadioConfig>): Promise<RadioConfig> {
  return fetchJson('/radio/config', {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

export function rebootRadio(): Promise<{ status: string; message: string }> {
  return fetchJson('/radio/reboot', { method: 'POST' });
}

// --- Channels ---

export interface Channel {
  key: string;
  name: string;
  is_hashtag: boolean;
  on_radio: boolean;
  flood_scope_override?: string | null;
}

export function getChannels(): Promise<Channel[]> {
  return fetchJson('/channels');
}

export function createChannel(name: string): Promise<Channel> {
  return fetchJson('/channels', {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}

export function deleteChannel(key: string): Promise<void> {
  return fetchJson(`/channels/${key}`, { method: 'DELETE' });
}

// --- Contacts ---

export interface Contact {
  public_key: string;
  name: string | null;
  type: number;
  flags: number;
  last_path: string | null;
  last_path_len: number;
  out_path_hash_mode: number;
  route_override_path?: string | null;
  route_override_len?: number | null;
  route_override_hash_mode?: number | null;
  last_advert: number | null;
  lat: number | null;
  lon: number | null;
  last_seen: number | null;
  on_radio: boolean;
  last_contacted: number | null;
  last_read_at: number | null;
}

export function getContacts(limit: number = 100, offset: number = 0): Promise<Contact[]> {
  return fetchJson(`/contacts?limit=${limit}&offset=${offset}`);
}

export function createContact(
  publicKey: string,
  name?: string,
  tryHistorical: boolean = false
): Promise<Contact> {
  return fetchJson('/contacts', {
    method: 'POST',
    body: JSON.stringify({
      public_key: publicKey,
      ...(name ? { name } : {}),
      try_historical: tryHistorical,
    }),
  });
}

export function deleteContact(publicKey: string): Promise<{ status: string }> {
  return fetchJson(`/contacts/${publicKey}`, { method: 'DELETE' });
}

// --- Messages ---

export interface MessagePath {
  path: string;
  received_at: number;
}

export interface Message {
  id: number;
  type: 'PRIV' | 'CHAN';
  conversation_key: string;
  text: string;
  outgoing: boolean;
  acked: number;
  received_at: number;
  sender_timestamp: number | null;
  paths: MessagePath[] | null;
}

export function getMessages(params: {
  type?: string;
  conversation_key?: string;
  limit?: number;
}): Promise<Message[]> {
  const qs = new URLSearchParams();
  if (params.type) qs.set('type', params.type);
  if (params.conversation_key) qs.set('conversation_key', params.conversation_key);
  if (params.limit) qs.set('limit', String(params.limit));
  return fetchJson(`/messages?${qs}`);
}

export function sendChannelMessage(
  channelKey: string,
  text: string
): Promise<{ status: string; message_id: number }> {
  return fetchJson('/messages/channel', {
    method: 'POST',
    body: JSON.stringify({ channel_key: channelKey, text }),
  });
}

// --- Read state ---

export interface UnreadCounts {
  counts: Record<string, number>;
  mentions: Record<string, boolean>;
  last_message_times: Record<string, number>;
  last_read_ats: Record<string, number | null>;
}

export function getUnreads(): Promise<UnreadCounts> {
  return fetchJson('/read-state/unreads');
}

export function markAllRead(): Promise<{ status: string; timestamp: number }> {
  return fetchJson('/read-state/mark-all-read', { method: 'POST' });
}

// --- Settings ---

export type Favorite = { type: string; id: string };

export interface AppSettings {
  max_radio_contacts: number;
  favorites: Favorite[];
  auto_decrypt_dm_on_advert: boolean;
  sidebar_sort_order: string;
  last_message_times: Record<string, number>;
  preferences_migrated: boolean;
  advert_interval: number;
}

export function getSettings(): Promise<AppSettings> {
  return fetchJson('/settings');
}

export function updateSettings(patch: Partial<AppSettings>): Promise<AppSettings> {
  return fetchJson('/settings', {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

// --- Fanout ---

export interface FanoutConfig {
  id: string;
  type: string;
  name: string;
  enabled: boolean;
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  sort_order: number;
  created_at: number;
}

export function getFanoutConfigs(): Promise<FanoutConfig[]> {
  return fetchJson('/fanout');
}

export function createFanoutConfig(body: {
  type: string;
  name: string;
  config: Record<string, unknown>;
  scope?: Record<string, unknown>;
  enabled?: boolean;
}): Promise<FanoutConfig> {
  return fetchJson('/fanout', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function updateFanoutConfig(
  id: string,
  patch: Partial<{ name: string; config: Record<string, unknown>; scope: Record<string, unknown>; enabled: boolean }>
): Promise<FanoutConfig> {
  return fetchJson(`/fanout/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

export function deleteFanoutConfig(id: string): Promise<{ deleted: boolean }> {
  return fetchJson(`/fanout/${id}`, { method: 'DELETE' });
}

// --- Helpers ---

/**
 * Ensure #flightless channel exists, creating it if needed.
 * Returns the channel object.
 */
export async function ensureFlightlessChannel(): Promise<Channel> {
  const channels = await getChannels();
  const existing = channels.find((c) => c.name === '#flightless');
  if (existing) return existing;
  return createChannel('#flightless');
}

/**
 * Wait for health to show a fully ready radio, polling with retries.
 */
export async function waitForRadioConnected(
  timeoutMs: number = 30_000,
  intervalMs: number = 2000
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const health = await getHealth();
      if (health.radio_connected && !health.radio_initializing) return;
    } catch {
      // Backend might be restarting
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(`Radio did not finish reconnect/setup within ${timeoutMs}ms`);
}

// --- Packets / Historical decryption ---

export function getUndecryptedCount(): Promise<{ count: number }> {
  return fetchJson('/packets/undecrypted/count');
}

export interface DecryptResult {
  started: boolean;
  total_packets: number;
  message: string;
}

export function decryptHistorical(params: {
  key_type: 'channel' | 'contact';
  channel_key?: string;
  channel_name?: string;
  private_key?: string;
  contact_public_key?: string;
}): Promise<DecryptResult> {
  return fetchJson('/packets/decrypt/historical', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}
