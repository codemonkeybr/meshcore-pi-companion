import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { SettingsFanoutSection } from '../components/settings/SettingsFanoutSection';
import type { HealthStatus, FanoutConfig } from '../types';

// Mock the api module
vi.mock('../api', () => ({
  api: {
    getFanoutConfigs: vi.fn(),
    createFanoutConfig: vi.fn(),
    updateFanoutConfig: vi.fn(),
    deleteFanoutConfig: vi.fn(),
    getChannels: vi.fn(),
    getContacts: vi.fn(),
  },
}));

// Suppress BotCodeEditor lazy load in tests
vi.mock('../components/BotCodeEditor', () => ({
  BotCodeEditor: () => <textarea data-testid="bot-code-editor" />,
}));

import { api } from '../api';

const mockedApi = vi.mocked(api);

const baseHealth: HealthStatus = {
  status: 'connected',
  radio_connected: true,
  connection_info: 'Serial: /dev/ttyUSB0',
  database_size_mb: 1.2,
  oldest_undecrypted_timestamp: null,
  fanout_statuses: {},
  bots_disabled: false,
};

const webhookConfig: FanoutConfig = {
  id: 'wh-1',
  type: 'webhook',
  name: 'Test Hook',
  enabled: true,
  config: { url: 'https://example.com/hook', method: 'POST', headers: {} },
  scope: { messages: 'all', raw_packets: 'none' },
  sort_order: 0,
  created_at: 1000,
};

function renderSection(overrides?: { health?: HealthStatus }) {
  return render(
    <SettingsFanoutSection
      health={overrides?.health ?? baseHealth}
      onHealthRefresh={vi.fn(async () => {})}
    />
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedApi.getFanoutConfigs.mockResolvedValue([]);
  mockedApi.getChannels.mockResolvedValue([]);
  mockedApi.getContacts.mockResolvedValue([]);
});

describe('SettingsFanoutSection', () => {
  it('shows add buttons for all integration types', async () => {
    renderSection();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Private MQTT' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Community MQTT/mesh2mqtt' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Webhook' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Apprise' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Bot' })).toBeInTheDocument();
    });
  });

  it('shows updated add label phrasing', async () => {
    renderSection();
    await waitFor(() => {
      expect(screen.getByText('Add a new entry:')).toBeInTheDocument();
    });
  });

  it('hides bot add button when bots_disabled', async () => {
    renderSection({ health: { ...baseHealth, bots_disabled: true } });
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Bot' })).not.toBeInTheDocument();
    });
  });

  it('shows bots disabled banner when bots_disabled', async () => {
    renderSection({ health: { ...baseHealth, bots_disabled: true } });
    await waitFor(() => {
      expect(screen.getByText(/Bot system is disabled/)).toBeInTheDocument();
    });
  });

  it('lists existing configs after load', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection();
    await waitFor(() => {
      expect(screen.getByText('Test Hook')).toBeInTheDocument();
    });
  });

  it('navigates to edit view when clicking edit', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection();
    await waitFor(() => {
      expect(screen.getByText('Test Hook')).toBeInTheDocument();
    });

    const editBtn = screen.getByRole('button', { name: 'Edit' });
    fireEvent.click(editBtn);

    await waitFor(() => {
      expect(screen.getByText('← Back to list')).toBeInTheDocument();
    });
  });

  it('calls toggle enabled on checkbox click', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    mockedApi.updateFanoutConfig.mockResolvedValue({ ...webhookConfig, enabled: false });
    renderSection();
    await waitFor(() => {
      expect(screen.getByText('Test Hook')).toBeInTheDocument();
    });

    const checkbox = screen.getByRole('checkbox');
    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(mockedApi.updateFanoutConfig).toHaveBeenCalledWith('wh-1', { enabled: false });
    });
  });

  it('webhook with persisted "none" scope renders "All messages" selected', async () => {
    const wh: FanoutConfig = {
      ...webhookConfig,
      scope: { messages: 'none', raw_packets: 'none' },
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([wh]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    // "none" is not a valid mode without raw packets — should fall back to "all"
    const allRadio = screen.getByLabelText('All messages');
    expect(allRadio).toBeChecked();
  });

  it('does not show "No messages" scope option for webhook', async () => {
    const wh: FanoutConfig = {
      ...webhookConfig,
      scope: { messages: 'all', raw_packets: 'none' },
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([wh]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByText('All messages')).toBeInTheDocument();
    expect(screen.queryByText('No messages')).not.toBeInTheDocument();
  });

  it('shows empty scope warning when "only" mode has nothing selected', async () => {
    const wh: FanoutConfig = {
      ...webhookConfig,
      scope: { messages: { channels: [], contacts: [] }, raw_packets: 'none' },
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([wh]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByText(/will not forward any data/)).toBeInTheDocument();
  });

  it('shows warning for private MQTT when both scope axes are off', async () => {
    const mqtt: FanoutConfig = {
      id: 'mqtt-1',
      type: 'mqtt_private',
      name: 'My MQTT',
      enabled: true,
      config: { broker_host: 'localhost', broker_port: 1883 },
      scope: { messages: 'none', raw_packets: 'none' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([mqtt]);
    renderSection();
    await waitFor(() => expect(screen.getByText('My MQTT')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByText(/will not forward any data/)).toBeInTheDocument();
  });

  it('private MQTT shows raw packets toggle and No messages option', async () => {
    const mqtt: FanoutConfig = {
      id: 'mqtt-1',
      type: 'mqtt_private',
      name: 'My MQTT',
      enabled: true,
      config: { broker_host: 'localhost', broker_port: 1883 },
      scope: { messages: 'all', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([mqtt]);
    renderSection();
    await waitFor(() => expect(screen.getByText('My MQTT')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByText('Forward raw packets')).toBeInTheDocument();
    expect(screen.getByText('No messages')).toBeInTheDocument();
  });

  it('private MQTT hides warning when raw packets enabled but messages off', async () => {
    const mqtt: FanoutConfig = {
      id: 'mqtt-1',
      type: 'mqtt_private',
      name: 'My MQTT',
      enabled: true,
      config: { broker_host: 'localhost', broker_port: 1883 },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([mqtt]);
    renderSection();
    await waitFor(() => expect(screen.getByText('My MQTT')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.queryByText(/will not forward any data/)).not.toBeInTheDocument();
  });

  it('navigates to create view when clicking add button', async () => {
    const createdWebhook: FanoutConfig = {
      id: 'wh-new',
      type: 'webhook',
      name: 'Webhook',
      enabled: false,
      config: { url: '', method: 'POST', headers: {} },
      scope: { messages: 'all', raw_packets: 'none' },
      sort_order: 0,
      created_at: 2000,
    };
    mockedApi.createFanoutConfig.mockResolvedValue(createdWebhook);
    // After creation, getFanoutConfigs returns the new config
    mockedApi.getFanoutConfigs.mockResolvedValueOnce([]).mockResolvedValueOnce([createdWebhook]);

    renderSection();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Webhook' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Webhook' }));

    await waitFor(() => {
      expect(screen.getByText('← Back to list')).toBeInTheDocument();
      // Should show the URL input for webhook type
      expect(screen.getByLabelText(/URL/)).toBeInTheDocument();
    });
  });

  it('community MQTT editor exposes packet topic template', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-1',
      type: 'mqtt_community',
      name: 'Community MQTT/mesh2mqtt',
      enabled: false,
      config: {
        broker_host: 'mqtt-us-v1.letsmesh.net',
        broker_port: 443,
        transport: 'tcp',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'token',
        iata: 'LAX',
        email: '',
        token_audience: 'meshrank.net',
        topic_template: 'mesh2mqtt/{IATA}/node/{PUBLIC_KEY}',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([communityConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Community MQTT/mesh2mqtt')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByLabelText('Packet Topic Template')).toHaveValue(
      'mesh2mqtt/{IATA}/node/{PUBLIC_KEY}'
    );
    expect(screen.getByLabelText('Transport')).toHaveValue('tcp');
    expect(screen.getByLabelText('Authentication')).toHaveValue('token');
    expect(screen.getByLabelText('Token Audience')).toHaveValue('meshrank.net');
    expect(screen.getByText(/LetsMesh uses/)).toBeInTheDocument();
  });

  it('existing community MQTT config without auth_mode defaults to token in the editor', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-legacy',
      type: 'mqtt_community',
      name: 'Legacy Community MQTT',
      enabled: false,
      config: {
        broker_host: 'mqtt-us-v1.letsmesh.net',
        broker_port: 443,
        transport: 'websockets',
        use_tls: true,
        tls_verify: true,
        iata: 'LAX',
        email: 'user@example.com',
        token_audience: '',
        topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([communityConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Legacy Community MQTT')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByLabelText('Authentication')).toHaveValue('token');
    expect(screen.getByLabelText('Token Audience')).toBeInTheDocument();
  });

  it('community MQTT token audience can be cleared back to blank', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-1',
      type: 'mqtt_community',
      name: 'Community MQTT/mesh2mqtt',
      enabled: false,
      config: {
        broker_host: 'mqtt-us-v1.letsmesh.net',
        broker_port: 443,
        transport: 'websockets',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'token',
        iata: 'LAX',
        email: '',
        token_audience: 'meshrank.net',
        topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([communityConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Community MQTT/mesh2mqtt')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    const audienceInput = screen.getByLabelText('Token Audience');
    fireEvent.change(audienceInput, { target: { value: '' } });

    expect(audienceInput).toHaveValue('');
  });

  it('community MQTT can be configured for no auth', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-1',
      type: 'mqtt_community',
      name: 'Community MQTT/mesh2mqtt',
      enabled: false,
      config: {
        broker_host: 'meshrank.net',
        broker_port: 8883,
        transport: 'tcp',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'none',
        iata: 'LAX',
        topic_template: 'meshrank/uplink/ROOM/{PUBLIC_KEY}/packets',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([communityConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Community MQTT/mesh2mqtt')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByLabelText('Authentication')).toHaveValue('none');
    expect(screen.queryByLabelText('Token Audience')).not.toBeInTheDocument();
  });

  it('community MQTT list shows configured packet topic', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-1',
      type: 'mqtt_community',
      name: 'Community MQTT/mesh2mqtt',
      enabled: false,
      config: {
        broker_host: 'mqtt-us-v1.letsmesh.net',
        broker_port: 443,
        transport: 'websockets',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'token',
        iata: 'LAX',
        email: '',
        token_audience: 'mqtt-us-v1.letsmesh.net',
        topic_template: 'mesh2mqtt/{IATA}/node/{PUBLIC_KEY}',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([communityConfig]);
    renderSection();

    await waitFor(() =>
      expect(screen.getByText('Broker: mqtt-us-v1.letsmesh.net:443')).toBeInTheDocument()
    );
    expect(screen.getByText('mesh2mqtt/{IATA}/node/{PUBLIC_KEY}')).toBeInTheDocument();
    expect(screen.queryByText('Region: LAX')).not.toBeInTheDocument();
  });

  it('private MQTT list shows broker and topic summary', async () => {
    const privateConfig: FanoutConfig = {
      id: 'mqtt-1',
      type: 'mqtt_private',
      name: 'Private MQTT',
      enabled: true,
      config: { broker_host: 'broker.local', broker_port: 1883, topic_prefix: 'meshcore' },
      scope: { messages: 'all', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([privateConfig]);
    renderSection();

    await waitFor(() => expect(screen.getByText('Broker: broker.local:1883')).toBeInTheDocument());
    expect(
      screen.getByText('meshcore/dm:<pubkey>, meshcore/gm:<channel>, meshcore/raw/...')
    ).toBeInTheDocument();
  });

  it('webhook list shows destination URL', async () => {
    const config: FanoutConfig = {
      id: 'wh-1',
      type: 'webhook',
      name: 'Webhook',
      enabled: true,
      config: { url: 'https://example.com/hook', method: 'POST', headers: {} },
      scope: { messages: 'all', raw_packets: 'none' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([config]);
    renderSection();

    await waitFor(() => expect(screen.getByText('https://example.com/hook')).toBeInTheDocument());
  });

  it('apprise list shows compact target summary', async () => {
    const config: FanoutConfig = {
      id: 'ap-1',
      type: 'apprise',
      name: 'Apprise',
      enabled: true,
      config: {
        urls: 'discord://abc\nmailto://one@example.com\nmailto://two@example.com',
        preserve_identity: true,
        include_path: true,
      },
      scope: { messages: 'all', raw_packets: 'none' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([config]);
    renderSection();

    await waitFor(() =>
      expect(screen.getByText(/discord:\/\/abc, mailto:\/\/one@example.com/)).toBeInTheDocument()
    );
  });
});
