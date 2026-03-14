import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
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
  radio_initializing: false,
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

function renderSectionWithRefresh(
  onHealthRefresh: () => Promise<void>,
  overrides?: { health?: HealthStatus }
) {
  return render(
    <SettingsFanoutSection
      health={overrides?.health ?? baseHealth}
      onHealthRefresh={onHealthRefresh}
    />
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  mockedApi.getFanoutConfigs.mockResolvedValue([]);
  mockedApi.getChannels.mockResolvedValue([]);
  mockedApi.getContacts.mockResolvedValue([]);
});

describe('SettingsFanoutSection', () => {
  it('shows add integration menu with all integration types', async () => {
    renderSection();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Integration' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Add Integration' }));

    expect(screen.getByRole('menuitem', { name: 'Private MQTT' })).toBeInTheDocument();
    expect(
      screen.getByRole('menuitem', { name: 'meshcoretomqtt/LetsMesh/MeshRank' })
    ).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Webhook' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Apprise' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Amazon SQS' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Bot' })).toBeInTheDocument();
  });

  it('shows bot option in add integration menu when bots are enabled', async () => {
    renderSection();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Integration' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Add Integration' }));
    expect(screen.getByRole('menuitem', { name: 'Bot' })).toBeInTheDocument();
  });

  it('shows bots disabled banner when bots_disabled', async () => {
    renderSection({ health: { ...baseHealth, bots_disabled: true } });
    await waitFor(() => {
      expect(screen.getByText(/Bot system is disabled/)).toBeInTheDocument();
    });
  });

  it('hides bot option from add integration menu when bots_disabled', async () => {
    renderSection({ health: { ...baseHealth, bots_disabled: true } });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Integration' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Add Integration' }));
    expect(screen.queryByRole('menuitem', { name: 'Bot' })).not.toBeInTheDocument();
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

  it('save as enabled returns to list even if health refresh fails', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    mockedApi.updateFanoutConfig.mockResolvedValue({ ...webhookConfig, enabled: true });
    const failingRefresh = vi.fn(async () => {
      throw new Error('refresh failed');
    });

    renderSectionWithRefresh(failingRefresh);
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Save as Enabled' }));

    await waitFor(() => expect(screen.queryByText('← Back to list')).not.toBeInTheDocument());
    expect(screen.getByText('Test Hook')).toBeInTheDocument();
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
    renderSection();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Integration' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Add Integration' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Webhook' }));

    await waitFor(() => {
      expect(screen.getByText('← Back to list')).toBeInTheDocument();
      expect(screen.getByLabelText('Name')).toHaveValue('Webhook #1');
      // Should show the URL input for webhook type
      expect(screen.getByLabelText(/URL/)).toBeInTheDocument();
    });

    expect(mockedApi.createFanoutConfig).not.toHaveBeenCalled();
  });

  it('new SQS draft shows queue url fields and sensible defaults', async () => {
    renderSection();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Integration' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Add Integration' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Amazon SQS' }));

    await waitFor(() => {
      expect(screen.getByText('← Back to list')).toBeInTheDocument();
      expect(screen.getByLabelText('Name')).toHaveValue('Amazon SQS #1');
      expect(screen.getByLabelText('Queue URL')).toBeInTheDocument();
      expect(screen.getByText('Forward raw packets')).toBeInTheDocument();
    });
  });

  it('backing out of a new draft does not create an integration', async () => {
    renderSection();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Integration' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Add Integration' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Webhook' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    fireEvent.click(screen.getByText('← Back to list'));

    await waitFor(() => expect(screen.queryByText('← Back to list')).not.toBeInTheDocument());
    expect(mockedApi.createFanoutConfig).not.toHaveBeenCalled();
  });

  it('back to list asks for confirmation before leaving', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    fireEvent.click(screen.getByText('← Back to list'));

    expect(window.confirm).toHaveBeenCalledWith('Leave without saving?');
    await waitFor(() => expect(screen.queryByText('← Back to list')).not.toBeInTheDocument());
  });

  it('back to list stays on the edit screen when confirmation is cancelled', async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    fireEvent.click(screen.getByText('← Back to list'));

    expect(window.confirm).toHaveBeenCalledWith('Leave without saving?');
    expect(screen.getByText('← Back to list')).toBeInTheDocument();
  });

  it('saving a new draft creates the integration on demand', async () => {
    const createdWebhook: FanoutConfig = {
      id: 'wh-new',
      type: 'webhook',
      name: 'Webhook #1',
      enabled: false,
      config: { url: '', method: 'POST', headers: {}, hmac_secret: '', hmac_header: '' },
      scope: { messages: 'all', raw_packets: 'none' },
      sort_order: 0,
      created_at: 2000,
    };
    mockedApi.createFanoutConfig.mockResolvedValue(createdWebhook);
    mockedApi.getFanoutConfigs.mockResolvedValueOnce([]).mockResolvedValueOnce([createdWebhook]);

    renderSection();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Add Integration' })).toBeInTheDocument()
    );

    fireEvent.click(screen.getByRole('button', { name: 'Add Integration' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Webhook' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Save as Disabled' }));

    await waitFor(() =>
      expect(mockedApi.createFanoutConfig).toHaveBeenCalledWith({
        type: 'webhook',
        name: 'Webhook #1',
        config: { url: '', method: 'POST', headers: {}, hmac_secret: '', hmac_header: '' },
        scope: { messages: 'all', raw_packets: 'none' },
        enabled: false,
      })
    );
  });

  it('new draft names increment within the integration type', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([
      webhookConfig,
      {
        ...webhookConfig,
        id: 'wh-2',
        name: 'Another Hook',
      },
    ]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Add Integration' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Webhook' }));
    await waitFor(() => expect(screen.getByLabelText('Name')).toHaveValue('Webhook #3'));
  });

  it('clicking a list name allows inline rename and saves on blur', async () => {
    const renamedWebhook = { ...webhookConfig, name: 'Renamed Hook' };
    mockedApi.getFanoutConfigs
      .mockResolvedValueOnce([webhookConfig])
      .mockResolvedValueOnce([renamedWebhook]);
    mockedApi.updateFanoutConfig.mockResolvedValue(renamedWebhook);

    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Test Hook' }));
    const inlineInput = screen.getByLabelText('Edit name for Test Hook');
    fireEvent.change(inlineInput, { target: { value: 'Renamed Hook' } });
    fireEvent.blur(inlineInput);

    await waitFor(() =>
      expect(mockedApi.updateFanoutConfig).toHaveBeenCalledWith('wh-1', { name: 'Renamed Hook' })
    );
    await waitFor(() => expect(screen.getByText('Renamed Hook')).toBeInTheDocument());
  });

  it('escape cancels inline rename without saving', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Test Hook' }));
    const inlineInput = screen.getByLabelText('Edit name for Test Hook');
    fireEvent.change(inlineInput, { target: { value: 'Cancelled Hook' } });
    fireEvent.keyDown(inlineInput, { key: 'Escape' });

    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());
    expect(mockedApi.updateFanoutConfig).not.toHaveBeenCalledWith('wh-1', {
      name: 'Cancelled Hook',
    });
  });

  it('community MQTT editor exposes packet topic template', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-1',
      type: 'mqtt_community',
      name: 'Community Feed',
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
    await waitFor(() => expect(screen.getByText('Community Feed')).toBeInTheDocument());

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
      name: 'Community Feed',
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
    await waitFor(() => expect(screen.getByText('Community Feed')).toBeInTheDocument());

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
      name: 'Community Feed',
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
    await waitFor(() => expect(screen.getByText('Community Feed')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByLabelText('Authentication')).toHaveValue('none');
    expect(screen.queryByLabelText('Token Audience')).not.toBeInTheDocument();
  });

  it('community MQTT list shows configured packet topic', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-1',
      type: 'mqtt_community',
      name: 'Community Feed',
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
      name: 'Private Broker',
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
      name: 'Webhook Feed',
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
      name: 'Apprise Feed',
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

  it('sqs list shows queue url summary', async () => {
    const config: FanoutConfig = {
      id: 'sqs-1',
      type: 'sqs',
      name: 'Queue Feed',
      enabled: true,
      config: {
        queue_url: 'https://sqs.us-east-1.amazonaws.com/123456789012/mesh-events',
        region_name: 'us-east-1',
      },
      scope: { messages: 'all', raw_packets: 'none' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([config]);
    renderSection();

    await waitFor(() =>
      expect(
        screen.getByText('https://sqs.us-east-1.amazonaws.com/123456789012/mesh-events')
      ).toBeInTheDocument()
    );
  });

  it('groups integrations by type and sorts entries alphabetically within each group', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([
      {
        ...webhookConfig,
        id: 'wh-b',
        name: 'Zulu Hook',
      },
      {
        ...webhookConfig,
        id: 'wh-a',
        name: 'Alpha Hook',
      },
      {
        id: 'ap-1',
        type: 'apprise',
        name: 'Bravo Alerts',
        enabled: true,
        config: { urls: 'discord://abc', preserve_identity: true, include_path: true },
        scope: { messages: 'all', raw_packets: 'none' },
        sort_order: 0,
        created_at: 1000,
      },
    ]);
    renderSection();

    const webhookGroup = await screen.findByRole('region', { name: 'Webhook integrations' });
    const appriseGroup = screen.getByRole('region', { name: 'Apprise integrations' });

    expect(
      screen.queryByRole('region', { name: 'Private MQTT integrations' })
    ).not.toBeInTheDocument();
    expect(within(webhookGroup).getByText('Alpha Hook')).toBeInTheDocument();
    expect(within(webhookGroup).getByText('Zulu Hook')).toBeInTheDocument();
    expect(within(appriseGroup).getByText('Bravo Alerts')).toBeInTheDocument();

    const alpha = within(webhookGroup).getByText('Alpha Hook');
    const zulu = within(webhookGroup).getByText('Zulu Hook');
    expect(alpha.compareDocumentPosition(zulu) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
