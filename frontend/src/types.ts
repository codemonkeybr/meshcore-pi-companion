/**
 * Type aliases for key types used throughout the application.
 * These are all hex strings but serve different purposes.
 */

/** 64-character hex string identifying a contact/node */
export type PublicKey = string;

/** 12-character hex prefix of a public key (used in message routing) */
export type PubkeyPrefix = string;

/** 32-character hex string identifying a channel */
export type ChannelKey = string;

export interface RadioSettings {
  freq: number;
  bw: number;
  sf: number;
  cr: number;
}

export interface RadioConfig {
  public_key: string;
  name: string;
  lat: number;
  lon: number;
  tx_power: number;
  max_tx_power: number;
  radio: RadioSettings;
}

export interface RadioConfigUpdate {
  name?: string;
  lat?: number;
  lon?: number;
  tx_power?: number;
  radio?: RadioSettings;
}

export interface HealthStatus {
  status: string;
  radio_connected: boolean;
  serial_port: string | null;
  database_size_mb: number;
  oldest_undecrypted_timestamp: number | null;
}

export interface MaintenanceResult {
  packets_deleted: number;
  vacuumed: boolean;
}

export interface Contact {
  public_key: PublicKey;
  name: string | null;
  type: number;
  flags: number;
  last_path: string | null;
  last_path_len: number;
  last_advert: number | null;
  lat: number | null;
  lon: number | null;
  last_seen: number | null;
  on_radio: boolean;
  last_contacted: number | null;
  last_read_at: number | null;
}

export interface Channel {
  key: ChannelKey;
  name: string;
  is_hashtag: boolean;
  on_radio: boolean;
  last_read_at: number | null;
}

/** A single path that a message took to reach us */
export interface MessagePath {
  /** Hex-encoded routing path (2 chars per hop) */
  path: string;
  /** Unix timestamp when this path was received */
  received_at: number;
}

export interface Message {
  id: number;
  type: 'PRIV' | 'CHAN';
  /** For PRIV: sender's PublicKey (or prefix). For CHAN: ChannelKey */
  conversation_key: string;
  text: string;
  sender_timestamp: number | null;
  received_at: number;
  /** List of routing paths this message arrived via. Null for outgoing messages. */
  paths: MessagePath[] | null;
  txt_type: number;
  signature: string | null;
  outgoing: boolean;
  /** ACK count: 0 = not acked, 1+ = number of acks/flood echoes received */
  acked: number;
}

export type ConversationType = 'contact' | 'channel' | 'raw' | 'map' | 'visualizer';

export interface Conversation {
  type: ConversationType;
  /** PublicKey for contacts, ChannelKey for channels, 'raw'/'map' for special views */
  id: string;
  name: string;
  /** For map view: public key prefix to focus on */
  mapFocusKey?: string;
}

export interface RawPacket {
  id: number;
  timestamp: number;
  data: string; // hex
  payload_type: string;
  snr: number | null; // Signal-to-noise ratio in dB
  rssi: number | null; // Received signal strength in dBm
  decrypted: boolean;
  decrypted_info: {
    channel_name: string | null;
    sender: string | null;
  } | null;
}

export interface Favorite {
  type: 'channel' | 'contact';
  id: string; // channel key or contact public key
}

export interface AppSettings {
  max_radio_contacts: number;
  favorites: Favorite[];
  auto_decrypt_dm_on_advert: boolean;
  sidebar_sort_order: 'recent' | 'alpha';
  last_message_times: Record<string, number>;
  preferences_migrated: boolean;
  advert_interval: number;
}

export interface AppSettingsUpdate {
  max_radio_contacts?: number;
  auto_decrypt_dm_on_advert?: boolean;
  sidebar_sort_order?: 'recent' | 'alpha';
  advert_interval?: number;
}

export interface MigratePreferencesRequest {
  favorites: Favorite[];
  sort_order: string;
  last_message_times: Record<string, number>;
}

export interface MigratePreferencesResponse {
  migrated: boolean;
  settings: AppSettings;
}

/** Contact type constants */
export const CONTACT_TYPE_CLIENT = 1;
export const CONTACT_TYPE_REPEATER = 2;

export interface NeighborInfo {
  pubkey_prefix: string;
  name: string | null;
  snr: number;
  last_heard_seconds: number;
}

export interface AclEntry {
  pubkey_prefix: string;
  name: string | null;
  permission: number;
  permission_name: string;
}

export interface TelemetryResponse {
  pubkey_prefix: string;
  battery_volts: number;
  tx_queue_len: number;
  noise_floor_dbm: number;
  last_rssi_dbm: number;
  last_snr_db: number;
  packets_received: number;
  packets_sent: number;
  airtime_seconds: number;
  rx_airtime_seconds: number;
  uptime_seconds: number;
  sent_flood: number;
  sent_direct: number;
  recv_flood: number;
  recv_direct: number;
  flood_dups: number;
  direct_dups: number;
  full_events: number;
  neighbors: NeighborInfo[];
  acl: AclEntry[];
}

export interface CommandResponse {
  command: string;
  response: string;
  sender_timestamp: number | null;
}
