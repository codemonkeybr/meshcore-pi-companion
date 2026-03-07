import { useState, useEffect, useCallback, lazy, Suspense } from 'react';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Button } from '../ui/button';
import { Separator } from '../ui/separator';
import { toast } from '../ui/sonner';
import { cn } from '@/lib/utils';
import { api } from '../../api';
import type { Channel, Contact, FanoutConfig, HealthStatus } from '../../types';

const BotCodeEditor = lazy(() =>
  import('../BotCodeEditor').then((m) => ({ default: m.BotCodeEditor }))
);

const TYPE_LABELS: Record<string, string> = {
  mqtt_private: 'Private MQTT',
  mqtt_community: 'Community MQTT/mesh2mqtt',
  bot: 'Bot',
  webhook: 'Webhook',
  apprise: 'Apprise',
};

const TYPE_OPTIONS = [
  { value: 'mqtt_private', label: 'Private MQTT' },
  { value: 'mqtt_community', label: 'Community MQTT/mesh2mqtt' },
  { value: 'bot', label: 'Bot' },
  { value: 'webhook', label: 'Webhook' },
  { value: 'apprise', label: 'Apprise' },
];

const DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE = 'meshcore/{IATA}/{PUBLIC_KEY}/packets';
const DEFAULT_COMMUNITY_BROKER_HOST = 'mqtt-us-v1.letsmesh.net';
const DEFAULT_COMMUNITY_BROKER_PORT = 443;
const DEFAULT_COMMUNITY_TRANSPORT = 'websockets';
const DEFAULT_COMMUNITY_AUTH_MODE = 'token';

function formatBrokerSummary(
  config: Record<string, unknown>,
  defaults: { host: string; port: number }
) {
  const host = (config.broker_host as string) || defaults.host;
  const port = typeof config.broker_port === 'number' ? config.broker_port : defaults.port;
  return `${host}:${port}`;
}

function formatPrivateTopicSummary(config: Record<string, unknown>) {
  const prefix = (config.topic_prefix as string) || 'meshcore';
  return `${prefix}/dm:<pubkey>, ${prefix}/gm:<channel>, ${prefix}/raw/...`;
}

function formatAppriseTargets(urls: string | undefined, maxLength = 80) {
  const targets = (urls || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  if (targets.length === 0) return 'No targets configured';

  const joined = targets.join(', ');
  if (joined.length <= maxLength) return joined;
  return `${joined.slice(0, maxLength - 3)}...`;
}

const DEFAULT_BOT_CODE = `def bot(
    sender_name: str | None,
    sender_key: str | None,
    message_text: str,
    is_dm: bool,
    channel_key: str | None,
    channel_name: str | None,
    sender_timestamp: int | None,
    path: str | None,
    is_outgoing: bool = False,
) -> str | list[str] | None:
    """
    Process messages and optionally return a reply.

    Args:
        sender_name: Display name of sender (may be None)
        sender_key: 64-char hex public key (None for channel msgs)
        message_text: The message content
        is_dm: True for direct messages, False for channel
        channel_key: 32-char hex key for channels, None for DMs
        channel_name: Channel name with hash (e.g. "#bot"), None for DMs
        sender_timestamp: Sender's timestamp (unix seconds, may be None)
        path: Hex-encoded routing path (may be None)
        is_outgoing: True if this is our own outgoing message

    Returns:
        None for no reply, a string for a single reply,
        or a list of strings to send multiple messages in order
    """
    # Don't reply to our own outgoing messages
    if is_outgoing:
        return None

    # Example: Only respond in #bot channel to "!pling" command
    if channel_name == "#bot" and "!pling" in message_text.lower():
        return "[BOT] Plong!"
    return None`;

function getStatusLabel(status: string | undefined, type?: string) {
  if (status === 'connected')
    return type === 'bot' || type === 'webhook' || type === 'apprise' ? 'Active' : 'Connected';
  if (status === 'error') return 'Error';
  if (status === 'disconnected') return 'Disconnected';
  return 'Inactive';
}

function getStatusColor(status: string | undefined, enabled?: boolean) {
  if (enabled === false) return 'bg-muted-foreground';
  if (status === 'connected')
    return 'bg-status-connected shadow-[0_0_6px_hsl(var(--status-connected)/0.5)]';
  if (status === 'error' || status === 'disconnected')
    return 'bg-destructive shadow-[0_0_6px_hsl(var(--destructive)/0.5)]';
  return 'bg-muted-foreground';
}

function MqttPrivateConfigEditor({
  config,
  scope,
  onChange,
  onScopeChange,
}: {
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
  onScopeChange: (scope: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Forward mesh data to your own MQTT broker for home automation, logging, or alerting.
      </p>

      <div className="rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning">
        Outgoing messages (DMs and group messages) will be reported to private MQTT brokers in
        decrypted/plaintext form.
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-mqtt-host">Broker Host</Label>
          <Input
            id="fanout-mqtt-host"
            type="text"
            placeholder="e.g. 192.168.1.100"
            value={(config.broker_host as string) || ''}
            onChange={(e) => onChange({ ...config, broker_host: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-mqtt-port">Broker Port</Label>
          <Input
            id="fanout-mqtt-port"
            type="number"
            min="1"
            max="65535"
            value={(config.broker_port as number) || 1883}
            onChange={(e) =>
              onChange({ ...config, broker_port: parseInt(e.target.value, 10) || 1883 })
            }
          />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-mqtt-user">Username</Label>
          <Input
            id="fanout-mqtt-user"
            type="text"
            placeholder="Optional"
            value={(config.username as string) || ''}
            onChange={(e) => onChange({ ...config, username: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-mqtt-pass">Password</Label>
          <Input
            id="fanout-mqtt-pass"
            type="password"
            placeholder="Optional"
            value={(config.password as string) || ''}
            onChange={(e) => onChange({ ...config, password: e.target.value })}
          />
        </div>
      </div>

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={!!config.use_tls}
          onChange={(e) => onChange({ ...config, use_tls: e.target.checked })}
          className="h-4 w-4 rounded border-border"
        />
        <span className="text-sm">Use TLS</span>
      </label>

      {!!config.use_tls && (
        <label className="flex items-center gap-3 cursor-pointer ml-7">
          <input
            type="checkbox"
            checked={!!config.tls_insecure}
            onChange={(e) => onChange({ ...config, tls_insecure: e.target.checked })}
            className="h-4 w-4 rounded border-border"
          />
          <span className="text-sm">Skip certificate verification</span>
        </label>
      )}

      <Separator />

      <div className="space-y-2">
        <Label htmlFor="fanout-mqtt-prefix">Topic Prefix</Label>
        <Input
          id="fanout-mqtt-prefix"
          type="text"
          value={(config.topic_prefix as string) || 'meshcore'}
          onChange={(e) => onChange({ ...config, topic_prefix: e.target.value })}
        />
      </div>

      <Separator />

      <ScopeSelector scope={scope} onChange={onScopeChange} showRawPackets />
    </div>
  );
}

function MqttCommunityConfigEditor({
  config,
  onChange,
}: {
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}) {
  const authMode = (config.auth_mode as string) || DEFAULT_COMMUNITY_AUTH_MODE;

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Share raw packet data with the MeshCore community for coverage mapping and network analysis.
        Only raw RF packets are shared &mdash; never decrypted messages.
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-comm-host">Broker Host</Label>
          <Input
            id="fanout-comm-host"
            type="text"
            placeholder={DEFAULT_COMMUNITY_BROKER_HOST}
            value={(config.broker_host as string) || DEFAULT_COMMUNITY_BROKER_HOST}
            onChange={(e) => onChange({ ...config, broker_host: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-comm-port">Broker Port</Label>
          <Input
            id="fanout-comm-port"
            type="number"
            min="1"
            max="65535"
            value={(config.broker_port as number) || DEFAULT_COMMUNITY_BROKER_PORT}
            onChange={(e) =>
              onChange({
                ...config,
                broker_port: parseInt(e.target.value, 10) || DEFAULT_COMMUNITY_BROKER_PORT,
              })
            }
          />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-comm-transport">Transport</Label>
          <select
            id="fanout-comm-transport"
            value={(config.transport as string) || DEFAULT_COMMUNITY_TRANSPORT}
            onChange={(e) => onChange({ ...config, transport: e.target.value })}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            <option value="websockets">WebSockets</option>
            <option value="tcp">TCP</option>
          </select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-comm-auth-mode">Authentication</Label>
          <select
            id="fanout-comm-auth-mode"
            value={authMode}
            onChange={(e) => onChange({ ...config, auth_mode: e.target.value })}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            <option value="token">Token</option>
            <option value="none">None</option>
            <option value="password">Username / Password</option>
          </select>
        </div>
      </div>

      <p className="text-xs text-muted-foreground">
        LetsMesh uses <code>token</code> auth. MeshRank uses <code>none</code>.
      </p>

      {authMode === 'token' && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="fanout-comm-token-audience">Token Audience</Label>
            <Input
              id="fanout-comm-token-audience"
              type="text"
              placeholder={(config.broker_host as string) || DEFAULT_COMMUNITY_BROKER_HOST}
              value={(config.token_audience as string | undefined) ?? ''}
              onChange={(e) => onChange({ ...config, token_audience: e.target.value })}
            />
            <p className="text-xs text-muted-foreground">Defaults to the broker host when blank</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="fanout-comm-email">Owner Email (optional)</Label>
            <Input
              id="fanout-comm-email"
              type="email"
              placeholder="you@example.com"
              value={(config.email as string) || ''}
              onChange={(e) => onChange({ ...config, email: e.target.value })}
            />
            <p className="text-xs text-muted-foreground">
              Used to claim your node on the community aggregator
            </p>
          </div>
        </div>
      )}

      {authMode === 'password' && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="fanout-comm-username">Username</Label>
            <Input
              id="fanout-comm-username"
              type="text"
              value={(config.username as string) || ''}
              onChange={(e) => onChange({ ...config, username: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="fanout-comm-password">Password</Label>
            <Input
              id="fanout-comm-password"
              type="password"
              value={(config.password as string) || ''}
              onChange={(e) => onChange({ ...config, password: e.target.value })}
            />
          </div>
        </div>
      )}

      <div className="space-y-2">
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={config.use_tls === undefined ? true : !!config.use_tls}
            onChange={(e) => onChange({ ...config, use_tls: e.target.checked })}
            className="h-4 w-4 rounded border-border"
          />
          <span className="text-sm">Use TLS</span>
        </label>

        <label className="flex items-center gap-3 cursor-pointer ml-7">
          <input
            type="checkbox"
            checked={config.tls_verify === undefined ? true : !!config.tls_verify}
            onChange={(e) => onChange({ ...config, tls_verify: e.target.checked })}
            className="h-4 w-4 rounded border-border"
            disabled={config.use_tls === undefined ? false : !config.use_tls}
          />
          <span className="text-sm">Verify TLS certificates</span>
        </label>
      </div>

      <div className="space-y-2">
        <Label htmlFor="fanout-comm-iata">Region Code (IATA)</Label>
        <Input
          id="fanout-comm-iata"
          type="text"
          maxLength={3}
          placeholder="e.g. DEN, LAX, NYC"
          value={(config.iata as string) || ''}
          onChange={(e) => onChange({ ...config, iata: e.target.value.toUpperCase() })}
          className="w-32"
        />
        <p className="text-xs text-muted-foreground">
          Your nearest airport&apos;s IATA code (required)
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="fanout-comm-topic-template">Packet Topic Template</Label>
        <Input
          id="fanout-comm-topic-template"
          type="text"
          value={(config.topic_template as string) || DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE}
          onChange={(e) => onChange({ ...config, topic_template: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">
          Use <code>{'{IATA}'}</code> and <code>{'{PUBLIC_KEY}'}</code>. Default:{' '}
          <code>{DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE}</code>
        </p>
      </div>
    </div>
  );
}

function BotConfigEditor({
  config,
  onChange,
}: {
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}) {
  const code = (config.code as string) || '';
  return (
    <div className="space-y-3">
      <div className="p-3 bg-destructive/10 border border-destructive/30 rounded-md">
        <p className="text-sm text-destructive">
          <strong>Experimental:</strong> This is an alpha feature and introduces automated message
          sending to your radio; unexpected behavior may occur. Use with caution, and please report
          any bugs!
        </p>
      </div>

      <div className="p-3 bg-warning/10 border border-warning/30 rounded-md">
        <p className="text-sm text-warning">
          <strong>Security Warning:</strong> This feature executes arbitrary Python code on the
          server. Only run trusted code, and be cautious of arbitrary usage of message parameters.
        </p>
      </div>

      <div className="p-3 bg-warning/10 border border-warning/30 rounded-md">
        <p className="text-sm text-warning">
          <strong>Don&apos;t wreck the mesh!</strong> Bots process ALL messages, including their
          own. Be careful of creating infinite loops!
        </p>
      </div>

      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          Define a <code className="bg-muted px-1 rounded">bot()</code> function that receives
          message data and optionally returns a reply.
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onChange({ ...config, code: DEFAULT_BOT_CODE })}
        >
          Reset to Example
        </Button>
      </div>

      <Suspense
        fallback={
          <div className="h-64 md:h-96 rounded-md border border-input bg-code-editor-bg flex items-center justify-center text-muted-foreground">
            Loading editor...
          </div>
        }
      >
        <BotCodeEditor value={code} onChange={(c) => onChange({ ...config, code: c })} />
      </Suspense>

      <div className="text-xs text-muted-foreground space-y-1">
        <p>
          <strong>Available:</strong> Standard Python libraries and any modules installed in the
          server environment.
        </p>
        <p>
          <strong>Limits:</strong> 10 second timeout per bot.
        </p>
        <p>
          <strong>Note:</strong> Bots respond to all messages, including your own. For channel
          messages, <code>sender_key</code> is <code>None</code>. Multiple enabled bots run
          concurrently. Outgoing messages are serialized with a two-second delay between sends to
          prevent repeater collision.
        </p>
      </div>
    </div>
  );
}

type ScopeMode = 'all' | 'none' | 'only' | 'except';

function getScopeMode(value: unknown): ScopeMode {
  if (value === 'all') return 'all';
  if (value === 'none') return 'none';
  if (typeof value === 'object' && value !== null) {
    // Check if either channels or contacts uses the {except: [...]} shape
    const obj = value as Record<string, unknown>;
    const ch = obj.channels;
    const co = obj.contacts;
    if (
      (typeof ch === 'object' && ch !== null && !Array.isArray(ch)) ||
      (typeof co === 'object' && co !== null && !Array.isArray(co))
    ) {
      return 'except';
    }
    return 'only';
  }
  return 'all';
}

/** Extract the key list from a filter value, whether it's a plain list or {except: [...]} */
function getFilterKeys(filter: unknown): string[] {
  if (Array.isArray(filter)) return filter as string[];
  if (typeof filter === 'object' && filter !== null && 'except' in filter)
    return ((filter as Record<string, unknown>).except as string[]) ?? [];
  return [];
}

function ScopeSelector({
  scope,
  onChange,
  showRawPackets = false,
}: {
  scope: Record<string, unknown>;
  onChange: (scope: Record<string, unknown>) => void;
  showRawPackets?: boolean;
}) {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);

  useEffect(() => {
    api.getChannels().then(setChannels).catch(console.error);

    // Paginate to fetch all contacts (API caps at 1000 per request)
    (async () => {
      const all: Contact[] = [];
      const pageSize = 1000;
      let offset = 0;

      while (true) {
        const page = await api.getContacts(pageSize, offset);
        all.push(...page);
        if (page.length < pageSize) break;
        offset += pageSize;
      }
      setContacts(all);
    })().catch(console.error);
  }, []);

  const messages = scope.messages ?? 'all';
  const rawMode = getScopeMode(messages);
  // When raw packets aren't offered, "none" is not a valid choice — treat as "all"
  const mode = !showRawPackets && rawMode === 'none' ? 'all' : rawMode;
  const isListMode = mode === 'only' || mode === 'except';

  const selectedChannels: string[] =
    isListMode && typeof messages === 'object' && messages !== null
      ? getFilterKeys((messages as Record<string, unknown>).channels)
      : [];
  const selectedContacts: string[] =
    isListMode && typeof messages === 'object' && messages !== null
      ? getFilterKeys((messages as Record<string, unknown>).contacts)
      : [];

  /** Wrap channel/contact key lists in the right shape for the current mode */
  const buildMessages = (chKeys: string[], coKeys: string[]) => {
    if (mode === 'except') {
      return {
        channels: { except: chKeys },
        contacts: { except: coKeys },
      };
    }
    return { channels: chKeys, contacts: coKeys };
  };

  const handleModeChange = (newMode: ScopeMode) => {
    if (newMode === 'all' || newMode === 'none') {
      onChange({ ...scope, messages: newMode });
    } else if (newMode === 'only') {
      onChange({ ...scope, messages: { channels: [], contacts: [] } });
    } else {
      onChange({
        ...scope,
        messages: { channels: { except: [] }, contacts: { except: [] } },
      });
    }
  };

  const toggleChannel = (key: string) => {
    const current = [...selectedChannels];
    const idx = current.indexOf(key);
    if (idx >= 0) current.splice(idx, 1);
    else current.push(key);
    onChange({ ...scope, messages: buildMessages(current, selectedContacts) });
  };

  const toggleContact = (key: string) => {
    const current = [...selectedContacts];
    const idx = current.indexOf(key);
    if (idx >= 0) current.splice(idx, 1);
    else current.push(key);
    onChange({ ...scope, messages: buildMessages(selectedChannels, current) });
  };

  // Exclude repeaters (2), rooms (3), and sensors (4)
  const filteredContacts = contacts.filter((c) => c.type === 0 || c.type === 1);

  const modeDescriptions: Record<ScopeMode, string> = {
    all: 'All messages',
    none: 'No messages',
    only: 'Only listed channels/contacts',
    except: 'All except listed channels/contacts',
  };

  const rawEnabled = showRawPackets && scope.raw_packets === 'all';

  // Warn when the effective scope matches nothing
  const messagesEffectivelyNone =
    mode === 'none' ||
    (mode === 'only' && selectedChannels.length === 0 && selectedContacts.length === 0) ||
    (mode === 'except' &&
      channels.length > 0 &&
      filteredContacts.length > 0 &&
      selectedChannels.length >= channels.length &&
      selectedContacts.length >= filteredContacts.length);
  const showEmptyScopeWarning = messagesEffectivelyNone && !rawEnabled;

  const isChannelChecked = (key: string) => selectedChannels.includes(key);
  const isContactChecked = (key: string) => selectedContacts.includes(key);

  const listHint =
    mode === 'only'
      ? 'Newly added channels or contacts will not be automatically included.'
      : 'Newly added channels or contacts will be automatically included unless excluded here.';

  const checkboxLabel = mode === 'except' ? 'exclude' : 'include';

  const messageModes: ScopeMode[] = showRawPackets
    ? ['all', 'none', 'only', 'except']
    : ['all', 'only', 'except'];

  return (
    <div className="space-y-3">
      <Label>Message Scope</Label>

      {showRawPackets && (
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={rawEnabled}
            onChange={(e) => onChange({ ...scope, raw_packets: e.target.checked ? 'all' : 'none' })}
            className="h-4 w-4 rounded border-border"
          />
          <span className="text-sm">Forward raw packets</span>
        </label>
      )}

      <div className="space-y-1">
        {messageModes.map((m) => (
          <label key={m} className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="scope-mode"
              checked={mode === m}
              onChange={() => handleModeChange(m)}
              className="h-4 w-4 accent-primary"
            />
            <span className="text-sm">{modeDescriptions[m]}</span>
          </label>
        ))}
      </div>

      {showEmptyScopeWarning && (
        <div className="rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning">
          Nothing is selected &mdash; this integration will not forward any data.
        </div>
      )}

      {isListMode && (
        <>
          <p className="text-xs text-muted-foreground">{listHint}</p>

          {channels.length > 0 && (
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <Label className="text-xs">
                  Channels{' '}
                  <span className="text-muted-foreground font-normal">({checkboxLabel})</span>
                </Label>
                <span className="flex gap-1">
                  <button
                    type="button"
                    className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                    onClick={() =>
                      onChange({
                        ...scope,
                        messages: buildMessages(
                          channels.map((ch) => ch.key),
                          selectedContacts
                        ),
                      })
                    }
                  >
                    All
                  </button>
                  <span className="text-xs text-muted-foreground">/</span>
                  <button
                    type="button"
                    className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                    onClick={() =>
                      onChange({ ...scope, messages: buildMessages([], selectedContacts) })
                    }
                  >
                    None
                  </button>
                </span>
              </div>
              <div className="max-h-32 overflow-y-auto border border-input rounded-md p-2 space-y-1">
                {channels.map((ch) => (
                  <label key={ch.key} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={isChannelChecked(ch.key)}
                      onChange={() => toggleChannel(ch.key)}
                      className="h-3.5 w-3.5 rounded border-input accent-primary"
                    />
                    <span className="text-sm truncate">{ch.name}</span>
                  </label>
                ))}
              </div>
            </div>
          )}

          {filteredContacts.length > 0 && (
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <Label className="text-xs">
                  Contacts{' '}
                  <span className="text-muted-foreground font-normal">({checkboxLabel})</span>
                </Label>
                <span className="flex gap-1">
                  <button
                    type="button"
                    className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                    onClick={() =>
                      onChange({
                        ...scope,
                        messages: buildMessages(
                          selectedChannels,
                          filteredContacts.map((c) => c.public_key)
                        ),
                      })
                    }
                  >
                    All
                  </button>
                  <span className="text-xs text-muted-foreground">/</span>
                  <button
                    type="button"
                    className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                    onClick={() =>
                      onChange({ ...scope, messages: buildMessages(selectedChannels, []) })
                    }
                  >
                    None
                  </button>
                </span>
              </div>
              <div className="max-h-32 overflow-y-auto border border-input rounded-md p-2 space-y-1">
                {filteredContacts.map((c) => (
                  <label key={c.public_key} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={isContactChecked(c.public_key)}
                      onChange={() => toggleContact(c.public_key)}
                      className="h-3.5 w-3.5 rounded border-input accent-primary"
                    />
                    <span className="text-sm truncate">
                      {c.name || c.public_key.substring(0, 12) + '...'}
                    </span>
                  </label>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function AppriseConfigEditor({
  config,
  scope,
  onChange,
  onScopeChange,
}: {
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
  onScopeChange: (scope: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Send push notifications via{' '}
        <a
          href="https://github.com/caronc/apprise"
          target="_blank"
          rel="noopener noreferrer"
          className="underline hover:text-foreground"
        >
          Apprise
        </a>{' '}
        when messages are received. Supports Discord, Slack, Telegram, email, and{' '}
        <a
          href="https://github.com/caronc/apprise/wiki#supported-notifications"
          target="_blank"
          rel="noopener noreferrer"
          className="underline hover:text-foreground"
        >
          100+ other services
        </a>
        .
      </p>

      <div className="space-y-2">
        <Label htmlFor="fanout-apprise-urls">Notification URLs</Label>
        <textarea
          id="fanout-apprise-urls"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono min-h-[80px]"
          placeholder={
            'discord://webhook_id/token\nslack://token_a/token_b/token_c\ntgram://bot_token/chat_id'
          }
          value={(config.urls as string) || ''}
          onChange={(e) => onChange({ ...config, urls: e.target.value })}
          rows={4}
        />
        <p className="text-xs text-muted-foreground">
          One URL per line. All URLs receive every matched notification.
        </p>
      </div>

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={config.preserve_identity !== false}
          onChange={(e) => onChange({ ...config, preserve_identity: e.target.checked })}
          className="h-4 w-4 rounded border-border"
        />
        <div>
          <span className="text-sm">Preserve identity on Discord</span>
          <p className="text-xs text-muted-foreground">
            When enabled, Discord webhooks will use their configured name/avatar instead of
            overriding with MeshCore sender info.
          </p>
        </div>
      </label>

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={config.include_path !== false}
          onChange={(e) => onChange({ ...config, include_path: e.target.checked })}
          className="h-4 w-4 rounded border-border"
        />
        <span className="text-sm">Include routing path in notifications</span>
      </label>

      <Separator />

      <ScopeSelector scope={scope} onChange={onScopeChange} />
    </div>
  );
}

function WebhookConfigEditor({
  config,
  scope,
  onChange,
  onScopeChange,
}: {
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
  onScopeChange: (scope: Record<string, unknown>) => void;
}) {
  const headersStr = JSON.stringify(config.headers ?? {}, null, 2);
  const [headersText, setHeadersText] = useState(headersStr);
  const [headersError, setHeadersError] = useState<string | null>(null);

  const handleHeadersChange = (text: string) => {
    setHeadersText(text);
    try {
      const parsed = JSON.parse(text);
      if (typeof parsed !== 'object' || Array.isArray(parsed)) {
        setHeadersError('Must be a JSON object');
        return;
      }
      setHeadersError(null);
      onChange({ ...config, headers: parsed });
    } catch {
      setHeadersError('Invalid JSON');
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Send message data as JSON to an HTTP endpoint when messages are received.
      </p>

      <div className="space-y-2">
        <Label htmlFor="fanout-webhook-url">URL</Label>
        <Input
          id="fanout-webhook-url"
          type="url"
          placeholder="https://example.com/webhook"
          value={(config.url as string) || ''}
          onChange={(e) => onChange({ ...config, url: e.target.value })}
        />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-webhook-method">HTTP Method</Label>
          <select
            id="fanout-webhook-method"
            value={(config.method as string) || 'POST'}
            onChange={(e) => onChange({ ...config, method: e.target.value })}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            <option value="POST">POST</option>
            <option value="PUT">PUT</option>
            <option value="PATCH">PATCH</option>
          </select>
        </div>
      </div>

      <Separator />

      <div className="space-y-3">
        <Label>HMAC Signing</Label>
        <p className="text-xs text-muted-foreground">
          When a secret is set, each request includes an HMAC-SHA256 signature of the JSON body in
          the specified header (e.g. <code className="bg-muted px-1 rounded">sha256=ab12cd...</code>
          ).
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="fanout-webhook-hmac-secret">HMAC Secret</Label>
            <Input
              id="fanout-webhook-hmac-secret"
              type="password"
              placeholder="Leave empty to disable signing"
              value={(config.hmac_secret as string) || ''}
              onChange={(e) => onChange({ ...config, hmac_secret: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="fanout-webhook-hmac-header">Signature Header Name</Label>
            <Input
              id="fanout-webhook-hmac-header"
              type="text"
              placeholder="X-Webhook-Signature"
              value={(config.hmac_header as string) || ''}
              onChange={(e) => onChange({ ...config, hmac_header: e.target.value })}
            />
          </div>
        </div>
      </div>

      <Separator />

      <div className="space-y-2">
        <Label htmlFor="fanout-webhook-headers">Extra Headers (JSON)</Label>
        <textarea
          id="fanout-webhook-headers"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono min-h-[60px]"
          value={headersText}
          onChange={(e) => handleHeadersChange(e.target.value)}
          placeholder='{"Authorization": "Bearer ..."}'
        />
        {headersError && <p className="text-xs text-destructive">{headersError}</p>}
      </div>

      <Separator />

      <ScopeSelector scope={scope} onChange={onScopeChange} />
    </div>
  );
}

export function SettingsFanoutSection({
  health,
  onHealthRefresh,
  className,
}: {
  health: HealthStatus | null;
  onHealthRefresh?: () => Promise<void>;
  className?: string;
}) {
  const [configs, setConfigs] = useState<FanoutConfig[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editConfig, setEditConfig] = useState<Record<string, unknown>>({});
  const [editScope, setEditScope] = useState<Record<string, unknown>>({});
  const [editName, setEditName] = useState('');
  const [busy, setBusy] = useState(false);

  const loadConfigs = useCallback(async () => {
    try {
      const data = await api.getFanoutConfigs();
      setConfigs(data);
    } catch (err) {
      console.error('Failed to load fanout configs:', err);
    }
  }, []);

  useEffect(() => {
    loadConfigs();
  }, [loadConfigs]);

  const handleToggleEnabled = async (cfg: FanoutConfig) => {
    try {
      await api.updateFanoutConfig(cfg.id, { enabled: !cfg.enabled });
      await loadConfigs();
      if (onHealthRefresh) await onHealthRefresh();
      toast.success(cfg.enabled ? 'Integration disabled' : 'Integration enabled');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to update');
    }
  };

  const handleEdit = (cfg: FanoutConfig) => {
    setEditingId(cfg.id);
    setEditConfig(cfg.config);
    setEditScope(cfg.scope);
    setEditName(cfg.name);
  };

  const handleSave = async (enabled?: boolean) => {
    if (!editingId) return;
    setBusy(true);
    try {
      const update: Record<string, unknown> = {
        name: editName,
        config: editConfig,
        scope: editScope,
      };
      if (enabled !== undefined) update.enabled = enabled;
      await api.updateFanoutConfig(editingId, update);
      await loadConfigs();
      if (onHealthRefresh) await onHealthRefresh();
      setEditingId(null);
      toast.success(enabled ? 'Integration saved and enabled' : 'Integration saved');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (id: string) => {
    const cfg = configs.find((c) => c.id === id);
    if (!confirm(`Delete "${cfg?.name}"? This cannot be undone.`)) return;
    try {
      await api.deleteFanoutConfig(id);
      if (editingId === id) setEditingId(null);
      await loadConfigs();
      if (onHealthRefresh) await onHealthRefresh();
      toast.success('Integration deleted');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete');
    }
  };

  const handleAddCreate = async (type: string) => {
    const defaults: Record<string, Record<string, unknown>> = {
      mqtt_private: {
        broker_host: '',
        broker_port: 1883,
        username: '',
        password: '',
        use_tls: false,
        tls_insecure: false,
        topic_prefix: 'meshcore',
      },
      mqtt_community: {
        broker_host: 'mqtt-us-v1.letsmesh.net',
        broker_port: DEFAULT_COMMUNITY_BROKER_PORT,
        transport: DEFAULT_COMMUNITY_TRANSPORT,
        use_tls: true,
        tls_verify: true,
        auth_mode: DEFAULT_COMMUNITY_AUTH_MODE,
        username: '',
        password: '',
        iata: '',
        email: '',
        token_audience: '',
        topic_template: DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE,
      },
      bot: {
        code: DEFAULT_BOT_CODE,
      },
      webhook: {
        url: '',
        method: 'POST',
        headers: {},
        hmac_secret: '',
        hmac_header: '',
      },
      apprise: {
        urls: '',
        preserve_identity: true,
        include_path: true,
      },
    };
    const defaultScopes: Record<string, Record<string, unknown>> = {
      mqtt_private: { messages: 'all', raw_packets: 'all' },
      mqtt_community: { messages: 'none', raw_packets: 'all' },
      bot: { messages: 'all', raw_packets: 'none' },
      webhook: { messages: 'all', raw_packets: 'none' },
      apprise: { messages: 'all', raw_packets: 'none' },
    };

    try {
      const created = await api.createFanoutConfig({
        type,
        name: TYPE_LABELS[type] || type,
        config: defaults[type] || {},
        scope: defaultScopes[type] || {},
        enabled: false,
      });
      await loadConfigs();
      handleEdit(created);
      toast.success('Integration created');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to create');
    }
  };

  const editingConfig = editingId ? configs.find((c) => c.id === editingId) : null;

  // Detail view
  if (editingConfig) {
    return (
      <div className={cn('space-y-4', className)}>
        <button
          type="button"
          className="text-sm text-muted-foreground hover:text-foreground transition-colors"
          onClick={() => setEditingId(null)}
        >
          &larr; Back to list
        </button>

        <div className="space-y-2">
          <Label htmlFor="fanout-edit-name">Name</Label>
          <Input
            id="fanout-edit-name"
            type="text"
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
          />
        </div>

        <div className="text-xs text-muted-foreground">
          Type: {TYPE_LABELS[editingConfig.type] || editingConfig.type}
        </div>

        <Separator />

        {editingConfig.type === 'mqtt_private' && (
          <MqttPrivateConfigEditor
            config={editConfig}
            scope={editScope}
            onChange={setEditConfig}
            onScopeChange={setEditScope}
          />
        )}

        {editingConfig.type === 'mqtt_community' && (
          <MqttCommunityConfigEditor config={editConfig} onChange={setEditConfig} />
        )}

        {editingConfig.type === 'bot' && (
          <BotConfigEditor config={editConfig} onChange={setEditConfig} />
        )}

        {editingConfig.type === 'apprise' && (
          <AppriseConfigEditor
            config={editConfig}
            scope={editScope}
            onChange={setEditConfig}
            onScopeChange={setEditScope}
          />
        )}

        {editingConfig.type === 'webhook' && (
          <WebhookConfigEditor
            config={editConfig}
            scope={editScope}
            onChange={setEditConfig}
            onScopeChange={setEditScope}
          />
        )}

        <Separator />

        <div className="flex gap-2">
          <Button
            onClick={() => handleSave(true)}
            disabled={busy}
            className="flex-1 bg-status-connected hover:bg-status-connected/90 text-primary-foreground"
          >
            {busy ? 'Saving...' : 'Save as Enabled'}
          </Button>
          <Button
            variant="secondary"
            onClick={() => handleSave(false)}
            disabled={busy}
            className="flex-1"
          >
            {busy ? 'Saving...' : 'Save as Disabled'}
          </Button>
          <Button variant="destructive" onClick={() => handleDelete(editingConfig.id)}>
            Delete
          </Button>
        </div>
      </div>
    );
  }

  // List view
  return (
    <div className={cn('space-y-4', className)}>
      <div className="rounded-md border border-warning/50 bg-warning/10 px-4 py-3 text-sm text-warning">
        Integrations are an experimental feature in open beta.
      </div>

      {health?.bots_disabled && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          Bot system is disabled by server configuration (MESHCORE_DISABLE_BOTS). Bot integrations
          cannot be created or modified.
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted-foreground">Add a new entry:</span>
        {TYPE_OPTIONS.filter((opt) => opt.value !== 'bot' || !health?.bots_disabled).map((opt) => (
          <Button
            key={opt.value}
            variant="outline"
            size="sm"
            onClick={() => handleAddCreate(opt.value)}
          >
            {opt.label}
          </Button>
        ))}
      </div>

      {configs.length > 0 && (
        <div className="space-y-2">
          {configs.map((cfg) => {
            const statusEntry = health?.fanout_statuses?.[cfg.id];
            const status = cfg.enabled ? statusEntry?.status : undefined;
            const communityConfig = cfg.config as Record<string, unknown>;
            return (
              <div key={cfg.id} className="border border-input rounded-md overflow-hidden">
                <div className="flex items-center gap-2 px-3 py-2 bg-muted/50">
                  <label
                    className="flex items-center cursor-pointer"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <input
                      type="checkbox"
                      checked={cfg.enabled}
                      onChange={() => handleToggleEnabled(cfg)}
                      className="w-4 h-4 rounded border-input accent-primary"
                      aria-label={`Enable ${cfg.name}`}
                    />
                  </label>

                  <span className="text-sm font-medium flex-1">{cfg.name}</span>

                  <span className="text-xs text-muted-foreground">
                    {TYPE_LABELS[cfg.type] || cfg.type}
                  </span>

                  <div
                    className={cn(
                      'w-2 h-2 rounded-full transition-colors',
                      getStatusColor(status, cfg.enabled)
                    )}
                    title={cfg.enabled ? getStatusLabel(status, cfg.type) : 'Disabled'}
                    aria-hidden="true"
                  />
                  <span className="text-xs text-muted-foreground hidden sm:inline">
                    {cfg.enabled ? getStatusLabel(status, cfg.type) : 'Disabled'}
                  </span>

                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-xs"
                    onClick={() => handleEdit(cfg)}
                  >
                    Edit
                  </Button>
                </div>

                {cfg.type === 'mqtt_community' && (
                  <div className="space-y-1 border-t border-input px-3 py-2 text-xs text-muted-foreground">
                    <div>
                      Broker:{' '}
                      {formatBrokerSummary(communityConfig, {
                        host: DEFAULT_COMMUNITY_BROKER_HOST,
                        port: DEFAULT_COMMUNITY_BROKER_PORT,
                      })}
                    </div>
                    <div className="break-all">
                      Topic:{' '}
                      <code>
                        {(communityConfig.topic_template as string) ||
                          DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE}
                      </code>
                    </div>
                  </div>
                )}

                {cfg.type === 'mqtt_private' && (
                  <div className="space-y-1 border-t border-input px-3 py-2 text-xs text-muted-foreground">
                    <div>
                      Broker:{' '}
                      {formatBrokerSummary(cfg.config as Record<string, unknown>, {
                        host: '',
                        port: 1883,
                      })}
                    </div>
                    <div className="break-all">
                      Topics:{' '}
                      <code>
                        {formatPrivateTopicSummary(cfg.config as Record<string, unknown>)}
                      </code>
                    </div>
                  </div>
                )}

                {cfg.type === 'webhook' && (
                  <div className="space-y-1 border-t border-input px-3 py-2 text-xs text-muted-foreground">
                    <div className="break-all">
                      URL:{' '}
                      <code>
                        {((cfg.config as Record<string, unknown>).url as string) || 'Not set'}
                      </code>
                    </div>
                  </div>
                )}

                {cfg.type === 'apprise' && (
                  <div className="space-y-1 border-t border-input px-3 py-2 text-xs text-muted-foreground">
                    <div className="break-all">
                      Targets:{' '}
                      <code>
                        {formatAppriseTargets(
                          (cfg.config as Record<string, unknown>).urls as string | undefined
                        )}
                      </code>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
