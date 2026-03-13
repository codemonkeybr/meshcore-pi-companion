interface RadioSettings {
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
  path_hash_mode: number;
  path_hash_mode_supported: boolean;
  advert_location_source?: 'off' | 'current';
}

export interface RadioConfigUpdate {
  name?: string;
  lat?: number;
  lon?: number;
  tx_power?: number;
  radio?: RadioSettings;
  path_hash_mode?: number;
  advert_location_source?: 'off' | 'current';
}

export interface FanoutStatusEntry {
  name: string;
  type: string;
  status: string;
}

export interface HealthStatus {
  status: string;
  radio_connected: boolean;
  radio_initializing: boolean;
  radio_state?: 'connected' | 'initializing' | 'connecting' | 'disconnected' | 'paused';
  connection_info: string | null;
  database_size_mb: number;
  oldest_undecrypted_timestamp: number | null;
  fanout_statuses: Record<string, FanoutStatusEntry>;
  bots_disabled: boolean;
}

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

export interface MaintenanceResult {
  packets_deleted: number;
  vacuumed: boolean;
}

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
  first_seen: number | null;
}

export interface ContactAdvertPath {
  path: string;
  path_len: number;
  next_hop: string | null;
  first_seen: number;
  last_seen: number;
  heard_count: number;
}

export interface ContactAdvertPathSummary {
  public_key: string;
  paths: ContactAdvertPath[];
}

export interface ContactNameHistory {
  name: string;
  first_seen: number;
  last_seen: number;
}

export interface ContactActiveRoom {
  channel_key: string;
  channel_name: string;
  message_count: number;
}

export interface NearestRepeater {
  public_key: string;
  name: string | null;
  path_len: number;
  last_seen: number;
  heard_count: number;
}

export interface ContactDetail {
  contact: Contact;
  name_history: ContactNameHistory[];
  dm_message_count: number;
  channel_message_count: number;
  most_active_rooms: ContactActiveRoom[];
  advert_paths: ContactAdvertPath[];
  advert_frequency: number | null;
  nearest_repeaters: NearestRepeater[];
}

export interface NameOnlyContactDetail {
  name: string;
  channel_message_count: number;
  most_active_rooms: ContactActiveRoom[];
}

export interface ContactAnalyticsHourlyBucket {
  bucket_start: number;
  last_24h_count: number;
  last_week_average: number;
  all_time_average: number;
}

export interface ContactAnalyticsWeeklyBucket {
  bucket_start: number;
  message_count: number;
}

export interface ContactAnalytics {
  lookup_type: 'contact' | 'name';
  name: string;
  contact: Contact | null;
  name_first_seen_at: number | null;
  name_history: ContactNameHistory[];
  dm_message_count: number;
  channel_message_count: number;
  includes_direct_messages: boolean;
  most_active_rooms: ContactActiveRoom[];
  advert_paths: ContactAdvertPath[];
  advert_frequency: number | null;
  nearest_repeaters: NearestRepeater[];
  hourly_activity: ContactAnalyticsHourlyBucket[];
  weekly_activity: ContactAnalyticsWeeklyBucket[];
}

export interface Channel {
  key: string;
  name: string;
  is_hashtag: boolean;
  on_radio: boolean;
  flood_scope_override?: string | null;
  last_read_at: number | null;
}

export interface ChannelMessageCounts {
  last_1h: number;
  last_24h: number;
  last_48h: number;
  last_7d: number;
  all_time: number;
}

export interface ChannelTopSender {
  sender_name: string;
  sender_key: string | null;
  message_count: number;
}

export interface ChannelDetail {
  channel: Channel;
  message_counts: ChannelMessageCounts;
  first_message_at: number | null;
  unique_sender_count: number;
  top_senders_24h: ChannelTopSender[];
}

/** A single path that a message took to reach us */
export interface MessagePath {
  /** Hex-encoded routing path */
  path: string;
  /** Unix timestamp when this path was received */
  received_at: number;
  /** Hop count (number of intermediate nodes). Null for legacy data (infer as len(path)/2). */
  path_len?: number | null;
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
  sender_key: string | null;
  outgoing: boolean;
  /** ACK count: 0 = not acked, 1+ = number of acks/flood echoes received */
  acked: number;
  sender_name: string | null;
  channel_name?: string | null;
}

export interface MessagesAroundResponse {
  messages: Message[];
  has_older: boolean;
  has_newer: boolean;
}

type ConversationType = 'contact' | 'channel' | 'raw' | 'map' | 'visualizer' | 'search';

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
  /** Per-observation WS identity (unique per RF arrival, may be absent in older payloads) */
  observation_id?: number;
  timestamp: number;
  data: string; // hex
  payload_type: string;
  snr: number | null; // Signal-to-noise ratio in dB
  rssi: number | null; // Received signal strength in dBm
  decrypted: boolean;
  decrypted_info: {
    channel_name: string | null;
    sender: string | null;
    channel_key: string | null;
    contact_key: string | null;
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
  last_advert_time: number;
  flood_scope: string;
  blocked_keys: string[];
  blocked_names: string[];
}

export interface AppSettingsUpdate {
  max_radio_contacts?: number;
  auto_decrypt_dm_on_advert?: boolean;
  sidebar_sort_order?: 'recent' | 'alpha';
  advert_interval?: number;
  flood_scope?: string;
  blocked_keys?: string[];
  blocked_names?: string[];
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

export interface CommandResponse {
  command: string;
  response: string;
  sender_timestamp: number | null;
}

// --- Granular repeater endpoint types ---

export interface RepeaterLoginResponse {
  status: string;
}

export interface RepeaterStatusResponse {
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
}

export interface RepeaterNeighborsResponse {
  neighbors: NeighborInfo[];
}

export interface RepeaterAclResponse {
  acl: AclEntry[];
}

export interface RepeaterNodeInfoResponse {
  name: string | null;
  lat: string | null;
  lon: string | null;
  clock_utc: string | null;
}

export interface RepeaterRadioSettingsResponse {
  firmware_version: string | null;
  radio: string | null;
  tx_power: string | null;
  airtime_factor: string | null;
  repeat_enabled: string | null;
  flood_max: string | null;
}

export interface RepeaterAdvertIntervalsResponse {
  advert_interval: string | null;
  flood_advert_interval: string | null;
}

export interface RepeaterOwnerInfoResponse {
  owner_info: string | null;
  guest_password: string | null;
}

export interface LppSensor {
  channel: number;
  type_name: string;
  value: number | Record<string, number>;
}

export interface RepeaterLppTelemetryResponse {
  sensors: LppSensor[];
}

export type PaneName =
  | 'status'
  | 'nodeInfo'
  | 'neighbors'
  | 'acl'
  | 'radioSettings'
  | 'advertIntervals'
  | 'ownerInfo'
  | 'lppTelemetry';

export interface PaneState {
  loading: boolean;
  attempt: number;
  error: string | null;
  fetched_at?: number | null;
}

export interface TraceResponse {
  remote_snr: number | null;
  local_snr: number | null;
  path_len: number;
}

export interface UnreadCounts {
  counts: Record<string, number>;
  mentions: Record<string, boolean>;
  last_message_times: Record<string, number>;
  last_read_ats: Record<string, number | null>;
}

interface BusyChannel {
  channel_key: string;
  channel_name: string;
  message_count: number;
}

interface ContactActivityCounts {
  last_hour: number;
  last_24_hours: number;
  last_week: number;
}

export interface StatisticsResponse {
  busiest_channels_24h: BusyChannel[];
  contact_count: number;
  repeater_count: number;
  channel_count: number;
  total_packets: number;
  decrypted_packets: number;
  undecrypted_packets: number;
  total_dms: number;
  total_channel_messages: number;
  total_outgoing: number;
  contacts_heard: ContactActivityCounts;
  repeaters_heard: ContactActivityCounts;
  path_hash_width_24h: {
    total_packets: number;
    single_byte: number;
    double_byte: number;
    triple_byte: number;
    single_byte_pct: number;
    double_byte_pct: number;
    triple_byte_pct: number;
  };
}
