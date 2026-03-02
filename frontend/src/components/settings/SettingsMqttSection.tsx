import { useState, useEffect } from 'react';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Button } from '../ui/button';
import { Separator } from '../ui/separator';
import { toast } from '../ui/sonner';
import type { AppSettings, AppSettingsUpdate, HealthStatus } from '../../types';

export function SettingsMqttSection({
  appSettings,
  health,
  onSaveAppSettings,
  className,
}: {
  appSettings: AppSettings;
  health: HealthStatus | null;
  onSaveAppSettings: (update: AppSettingsUpdate) => Promise<void>;
  className?: string;
}) {
  const [mqttBrokerHost, setMqttBrokerHost] = useState('');
  const [mqttBrokerPort, setMqttBrokerPort] = useState('1883');
  const [mqttUsername, setMqttUsername] = useState('');
  const [mqttPassword, setMqttPassword] = useState('');
  const [mqttUseTls, setMqttUseTls] = useState(false);
  const [mqttTlsInsecure, setMqttTlsInsecure] = useState(false);
  const [mqttTopicPrefix, setMqttTopicPrefix] = useState('meshcore');
  const [mqttPublishMessages, setMqttPublishMessages] = useState(false);
  const [mqttPublishRawPackets, setMqttPublishRawPackets] = useState(false);

  // Community MQTT state
  const [communityMqttEnabled, setCommunityMqttEnabled] = useState(false);
  const [communityMqttIata, setCommunityMqttIata] = useState('');
  const [communityMqttBrokerHost, setCommunityMqttBrokerHost] = useState('mqtt-us-v1.letsmesh.net');
  const [communityMqttBrokerPort, setCommunityMqttBrokerPort] = useState('443');
  const [communityMqttEmail, setCommunityMqttEmail] = useState('');

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setMqttBrokerHost(appSettings.mqtt_broker_host ?? '');
    setMqttBrokerPort(String(appSettings.mqtt_broker_port ?? 1883));
    setMqttUsername(appSettings.mqtt_username ?? '');
    setMqttPassword(appSettings.mqtt_password ?? '');
    setMqttUseTls(appSettings.mqtt_use_tls ?? false);
    setMqttTlsInsecure(appSettings.mqtt_tls_insecure ?? false);
    setMqttTopicPrefix(appSettings.mqtt_topic_prefix ?? 'meshcore');
    setMqttPublishMessages(appSettings.mqtt_publish_messages ?? false);
    setMqttPublishRawPackets(appSettings.mqtt_publish_raw_packets ?? false);
    setCommunityMqttEnabled(appSettings.community_mqtt_enabled ?? false);
    setCommunityMqttIata(appSettings.community_mqtt_iata ?? '');
    setCommunityMqttBrokerHost(appSettings.community_mqtt_broker_host ?? 'mqtt-us-v1.letsmesh.net');
    setCommunityMqttBrokerPort(String(appSettings.community_mqtt_broker_port ?? 443));
    setCommunityMqttEmail(appSettings.community_mqtt_email ?? '');
  }, [appSettings]);

  const handleSave = async () => {
    setError(null);
    setBusy(true);

    try {
      const update: AppSettingsUpdate = {
        mqtt_broker_host: mqttBrokerHost,
        mqtt_broker_port: parseInt(mqttBrokerPort, 10) || 1883,
        mqtt_username: mqttUsername,
        mqtt_password: mqttPassword,
        mqtt_use_tls: mqttUseTls,
        mqtt_tls_insecure: mqttTlsInsecure,
        mqtt_topic_prefix: mqttTopicPrefix || 'meshcore',
        mqtt_publish_messages: mqttPublishMessages,
        mqtt_publish_raw_packets: mqttPublishRawPackets,
        community_mqtt_enabled: communityMqttEnabled,
        community_mqtt_iata: communityMqttIata,
        community_mqtt_broker_host: communityMqttBrokerHost || 'mqtt-us-v1.letsmesh.net',
        community_mqtt_broker_port: parseInt(communityMqttBrokerPort, 10) || 443,
        community_mqtt_email: communityMqttEmail,
      };
      await onSaveAppSettings(update);
      toast.success('MQTT settings saved');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={className}>
      <div className="space-y-1">
        <h4 className="text-sm font-medium">Private MQTT Broker</h4>
        <p className="text-xs text-muted-foreground">
          Forward all mesh data to your own MQTT broker for home automation, logging, or alerting.
          Publishes both decrypted messages and raw packets to your broker.
        </p>
      </div>

      <div className="space-y-2">
        <Label>Status</Label>
        {health?.mqtt_status === 'connected' ? (
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-green-500" />
            <span className="text-sm text-green-400">Connected</span>
          </div>
        ) : health?.mqtt_status === 'disconnected' ? (
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-red-500" />
            <span className="text-sm text-red-400">Disconnected</span>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-gray-500" />
            <span className="text-sm text-muted-foreground">Disabled</span>
          </div>
        )}
      </div>

      <Separator />

      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="mqtt-host">Broker Host</Label>
          <Input
            id="mqtt-host"
            type="text"
            placeholder="e.g. 192.168.1.100"
            value={mqttBrokerHost}
            onChange={(e) => setMqttBrokerHost(e.target.value)}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="mqtt-port">Broker Port</Label>
          <Input
            id="mqtt-port"
            type="number"
            min="1"
            max="65535"
            value={mqttBrokerPort}
            onChange={(e) => setMqttBrokerPort(e.target.value)}
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="mqtt-username">Username</Label>
          <Input
            id="mqtt-username"
            type="text"
            placeholder="Optional"
            value={mqttUsername}
            onChange={(e) => setMqttUsername(e.target.value)}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="mqtt-password">Password</Label>
          <Input
            id="mqtt-password"
            type="password"
            placeholder="Optional"
            value={mqttPassword}
            onChange={(e) => setMqttPassword(e.target.value)}
          />
        </div>
      </div>

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={mqttUseTls}
          onChange={(e) => setMqttUseTls(e.target.checked)}
          className="h-4 w-4 rounded border-border"
        />
        <span className="text-sm">Use TLS</span>
      </label>

      {mqttUseTls && (
        <>
          <label className="flex items-center gap-3 cursor-pointer ml-7">
            <input
              type="checkbox"
              checked={mqttTlsInsecure}
              onChange={(e) => setMqttTlsInsecure(e.target.checked)}
              className="h-4 w-4 rounded border-border"
            />
            <span className="text-sm">Skip certificate verification</span>
          </label>
          <p className="text-xs text-muted-foreground ml-7">
            Allow self-signed or untrusted broker certificates
          </p>
        </>
      )}

      <Separator />

      <div className="space-y-2">
        <Label htmlFor="mqtt-prefix">Topic Prefix</Label>
        <Input
          id="mqtt-prefix"
          type="text"
          value={mqttTopicPrefix}
          onChange={(e) => setMqttTopicPrefix(e.target.value)}
        />
        <div className="text-xs text-muted-foreground space-y-2">
          <div>
            <p className="font-medium">Decrypted messages <span className="font-mono font-normal opacity-75">{'{'}id, type, conversation_key, text, sender_timestamp, received_at, paths, outgoing, acked{'}'}</span></p>
            <div className="font-mono ml-2 space-y-0.5">
              <div>{mqttTopicPrefix || 'meshcore'}/dm:&lt;contact_key&gt;</div>
              <div>{mqttTopicPrefix || 'meshcore'}/gm:&lt;channel_key&gt;</div>
            </div>
          </div>
          <div>
            <p className="font-medium">Raw packets <span className="font-mono font-normal opacity-75">{'{'}id, observation_id, timestamp, data, payload_type, snr, rssi, decrypted, decrypted_info{'}'}</span></p>
            <div className="font-mono ml-2 space-y-0.5">
              <div>{mqttTopicPrefix || 'meshcore'}/raw/dm:&lt;contact_key&gt;</div>
              <div>{mqttTopicPrefix || 'meshcore'}/raw/gm:&lt;channel_key&gt;</div>
              <div>{mqttTopicPrefix || 'meshcore'}/raw/unrouted</div>
            </div>
          </div>
        </div>
      </div>

      <Separator />

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={mqttPublishMessages}
          onChange={(e) => setMqttPublishMessages(e.target.checked)}
          className="h-4 w-4 rounded border-border"
        />
        <span className="text-sm">Publish Messages</span>
      </label>
      <p className="text-xs text-muted-foreground ml-7">
        Forward decrypted DM and channel messages
      </p>

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={mqttPublishRawPackets}
          onChange={(e) => setMqttPublishRawPackets(e.target.checked)}
          className="h-4 w-4 rounded border-border"
        />
        <span className="text-sm">Publish Raw Packets</span>
      </label>
      <p className="text-xs text-muted-foreground ml-7">Forward all RF packets</p>

      <Separator />

      <div className="space-y-3">
        <h4 className="text-sm font-medium">Community Analytics</h4>
        <p className="text-xs text-muted-foreground">
          Share raw packet data with the MeshCore community for coverage mapping and network
          analysis. Only raw RF packets are shared — never decrypted messages.
        </p>
        <div className="flex items-center gap-2 mb-2">
          {health?.community_mqtt_status === 'connected' ? (
            <>
              <div className="w-2 h-2 rounded-full bg-green-500" />
              <span className="text-sm text-green-400">Connected</span>
            </>
          ) : health?.community_mqtt_status === 'disconnected' ? (
            <>
              <div className="w-2 h-2 rounded-full bg-red-500" />
              <span className="text-sm text-red-400">Disconnected</span>
            </>
          ) : (
            <>
              <div className="w-2 h-2 rounded-full bg-gray-500" />
              <span className="text-sm text-muted-foreground">Disabled</span>
            </>
          )}
        </div>
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={communityMqttEnabled}
            onChange={(e) => setCommunityMqttEnabled(e.target.checked)}
            className="h-4 w-4 rounded border-border"
          />
          <span className="text-sm">Enable Community Analytics</span>
        </label>

        {communityMqttEnabled && (
          <div className="space-y-2">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="community-broker-host">Broker Host</Label>
                <Input
                  id="community-broker-host"
                  type="text"
                  placeholder="mqtt-us-v1.letsmesh.net"
                  value={communityMqttBrokerHost}
                  onChange={(e) => setCommunityMqttBrokerHost(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="community-broker-port">Broker Port</Label>
                <Input
                  id="community-broker-port"
                  type="number"
                  min="1"
                  max="65535"
                  value={communityMqttBrokerPort}
                  onChange={(e) => setCommunityMqttBrokerPort(e.target.value)}
                />
              </div>
            </div>
            <Label htmlFor="community-iata">Region Code (IATA)</Label>
            <Input
              id="community-iata"
              type="text"
              maxLength={3}
              placeholder="e.g. DEN, LAX, NYC"
              value={communityMqttIata}
              onChange={(e) => setCommunityMqttIata(e.target.value.toUpperCase())}
              className="w-32"
            />
            <p className="text-xs text-muted-foreground">
              Your nearest airport&apos;s{' '}
              <a
                href="https://en.wikipedia.org/wiki/List_of_airports_by_IATA_airport_code:_A"
                target="_blank"
                rel="noopener noreferrer"
                className="underline hover:text-foreground"
              >
                IATA code
              </a>{' '}
              (required)
            </p>
            {communityMqttIata && (
              <p className="text-xs text-muted-foreground">
                Topic: meshcore/{communityMqttIata}/&lt;pubkey&gt;/packets
              </p>
            )}
            <Label htmlFor="community-email">Owner Email (optional)</Label>
            <Input
              id="community-email"
              type="email"
              placeholder="you@example.com"
              value={communityMqttEmail}
              onChange={(e) => setCommunityMqttEmail(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Used to claim your node on the community aggregator
            </p>
          </div>
        )}
      </div>

      <Button onClick={handleSave} disabled={busy} className="w-full">
        {busy ? 'Saving...' : 'Save MQTT Settings'}
      </Button>

      {error && <div className="text-sm text-destructive">{error}</div>}
    </div>
  );
}
