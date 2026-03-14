import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SettingsModal } from '../components/SettingsModal';
import type {
  AppSettings,
  AppSettingsUpdate,
  HealthStatus,
  RadioConfig,
  RadioConfigUpdate,
  StatisticsResponse,
} from '../types';
import type { SettingsSection } from '../components/settings/settingsConstants';
import {
  LAST_VIEWED_CONVERSATION_KEY,
  REOPEN_LAST_CONVERSATION_KEY,
} from '../utils/lastViewedConversation';
import { api } from '../api';

const baseConfig: RadioConfig = {
  public_key: 'aa'.repeat(32),
  name: 'TestNode',
  lat: 1,
  lon: 2,
  tx_power: 17,
  max_tx_power: 22,
  radio: {
    freq: 910.525,
    bw: 62.5,
    sf: 7,
    cr: 5,
  },
  path_hash_mode: 0,
  path_hash_mode_supported: false,
  advert_location_source: 'current',
};

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

const baseSettings: AppSettings = {
  max_radio_contacts: 200,
  favorites: [],
  auto_decrypt_dm_on_advert: false,
  sidebar_sort_order: 'recent',
  last_message_times: {},
  preferences_migrated: false,
  advert_interval: 0,
  last_advert_time: 0,
  flood_scope: '',
  blocked_keys: [],
  blocked_names: [],
};

function renderModal(overrides?: {
  appSettings?: AppSettings;
  health?: HealthStatus;
  onSaveAppSettings?: (update: AppSettingsUpdate) => Promise<void>;
  onRefreshAppSettings?: () => Promise<void>;
  onSave?: (update: RadioConfigUpdate) => Promise<void>;
  onClose?: () => void;
  onSetPrivateKey?: (key: string) => Promise<void>;
  onReboot?: () => Promise<void>;
  onDisconnect?: () => Promise<void>;
  onReconnect?: () => Promise<void>;
  open?: boolean;
  pageMode?: boolean;
  externalSidebarNav?: boolean;
  desktopSection?: SettingsSection;
  mobile?: boolean;
}) {
  setMatchMedia(overrides?.mobile ?? false);

  const onSaveAppSettings = overrides?.onSaveAppSettings ?? vi.fn(async () => {});
  const onRefreshAppSettings = overrides?.onRefreshAppSettings ?? vi.fn(async () => {});
  const onSave = overrides?.onSave ?? vi.fn(async (_update: RadioConfigUpdate) => {});
  const onClose = overrides?.onClose ?? vi.fn();
  const onSetPrivateKey = overrides?.onSetPrivateKey ?? vi.fn(async () => {});
  const onReboot = overrides?.onReboot ?? vi.fn(async () => {});
  const onDisconnect = overrides?.onDisconnect ?? vi.fn(async () => {});
  const onReconnect = overrides?.onReconnect ?? vi.fn(async () => {});

  const commonProps = {
    open: overrides?.open ?? true,
    pageMode: overrides?.pageMode,
    config: baseConfig,
    health: overrides?.health ?? baseHealth,
    appSettings: overrides?.appSettings ?? baseSettings,
    onClose,
    onSave,
    onSaveAppSettings,
    onSetPrivateKey,
    onReboot,
    onDisconnect,
    onReconnect,
    onAdvertise: vi.fn(async () => {}),
    onHealthRefresh: vi.fn(async () => {}),
    onRefreshAppSettings,
  };

  const view = overrides?.externalSidebarNav
    ? render(
        <SettingsModal
          {...commonProps}
          externalSidebarNav
          desktopSection={overrides.desktopSection ?? 'radio'}
        />
      )
    : render(<SettingsModal {...commonProps} />);

  return {
    onSaveAppSettings,
    onRefreshAppSettings,
    onSave,
    onClose,
    onSetPrivateKey,
    onReboot,
    onDisconnect,
    onReconnect,
    view,
  };
}

function setMatchMedia(matches: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches,
      media: '(max-width: 767px)',
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

function openRadioSection() {
  const radioToggle = screen.getByRole('button', { name: /Radio/i });
  fireEvent.click(radioToggle);
}

function openLocalSection() {
  const localToggle = screen.getByRole('button', { name: /Local Configuration/i });
  fireEvent.click(localToggle);
}

function openDatabaseSection() {
  const databaseToggle = screen.getByRole('button', { name: /Database/i });
  fireEvent.click(databaseToggle);
}

describe('SettingsModal', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    window.location.hash = '';
  });

  it('refreshes app settings when opened', async () => {
    const { onRefreshAppSettings } = renderModal();

    await waitFor(() => {
      expect(onRefreshAppSettings).toHaveBeenCalledTimes(1);
    });
  });

  it('refreshes app settings in page mode even when open is false', async () => {
    const { onRefreshAppSettings } = renderModal({ open: false, pageMode: true });

    await waitFor(() => {
      expect(onRefreshAppSettings).toHaveBeenCalledTimes(1);
    });
  });

  it('does not render when closed outside page mode', () => {
    renderModal({ open: false });
    expect(screen.queryByLabelText('Preset')).not.toBeInTheDocument();
  });

  it('shows favorite-contact radio sync helper text in radio tab', async () => {
    renderModal();
    openRadioSection();

    expect(screen.getByText(/Configured radio contact capacity/i)).toBeInTheDocument();
  });

  it('shows reconnect action when radio connection is paused', () => {
    renderModal({
      health: { ...baseHealth, radio_state: 'paused' },
    });
    openRadioSection();

    expect(screen.getByRole('button', { name: 'Reconnect' })).toBeInTheDocument();
  });

  it('saves advert location source through radio config save', async () => {
    const { onSave } = renderModal();
    openRadioSection();

    fireEvent.change(screen.getByLabelText('Advert Location Source'), {
      target: { value: 'off' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({ advert_location_source: 'off' })
      );
    });
  });

  it('saves changed max contacts value through onSaveAppSettings', async () => {
    const { onSaveAppSettings } = renderModal();
    openRadioSection();

    const maxContactsInput = screen.getByLabelText('Max Contacts on Radio');
    fireEvent.change(maxContactsInput, { target: { value: '250' } });

    // Click the "Save Settings" button in the Flood & Advert Control section
    const saveButtons = screen.getAllByRole('button', { name: 'Save Settings' });
    fireEvent.click(saveButtons[0]);

    await waitFor(() => {
      expect(onSaveAppSettings).toHaveBeenCalledWith({ max_radio_contacts: 250 });
    });
  });

  it('does not save max contacts when unchanged', async () => {
    const { onSaveAppSettings } = renderModal({
      appSettings: { ...baseSettings, max_radio_contacts: 200 },
    });
    openRadioSection();

    // Click the "Save Settings" button in the Flood & Advert Control section
    const saveButtons = screen.getAllByRole('button', { name: 'Save Settings' });
    fireEvent.click(saveButtons[0]);

    await waitFor(() => {
      expect(onSaveAppSettings).not.toHaveBeenCalled();
    });
  });

  it('renders selected section from external sidebar nav on desktop mode', async () => {
    renderModal({
      externalSidebarNav: true,
      desktopSection: 'fanout',
    });

    expect(screen.queryByRole('button', { name: /Local Configuration/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Preset')).not.toBeInTheDocument();
  });

  it('does not clip the fanout add-integration menu in external desktop mode', () => {
    renderModal({
      externalSidebarNav: true,
      desktopSection: 'fanout',
    });

    const addIntegrationButton = screen.getByRole('button', { name: 'Add Integration' });
    const wrapperSection = addIntegrationButton.closest('section');
    expect(wrapperSection).not.toHaveClass('overflow-hidden');
  });

  it('applies the centered 800px column layout to non-fanout settings content', () => {
    renderModal({
      externalSidebarNav: true,
      desktopSection: 'local',
    });

    const localSettingsText = screen.getByText('These settings apply only to this device/browser.');
    expect(localSettingsText.closest('div')).toHaveClass('mx-auto', 'w-full', 'max-w-[800px]');
  });

  it('toggles sections in mobile accordion mode', () => {
    renderModal({ mobile: true });
    const localToggle = screen.getAllByRole('button', { name: /Local Configuration/i })[0];

    expect(screen.queryByLabelText('Preset')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Local label text')).not.toBeInTheDocument();

    fireEvent.click(localToggle);
    expect(screen.getByLabelText('Local label text')).toBeInTheDocument();

    fireEvent.click(localToggle);
    expect(screen.queryByLabelText('Local label text')).not.toBeInTheDocument();
  });

  it('lists the new Windows 95 and iPhone themes', () => {
    renderModal();
    openLocalSection();

    expect(screen.getByText('Windows 95')).toBeInTheDocument();
    expect(screen.getByText('iPhone')).toBeInTheDocument();
  });

  it('clears stale errors when switching external desktop sections', async () => {
    const onSaveAppSettings = vi.fn(async () => {
      throw new Error('Save failed');
    });

    const { view } = renderModal({
      externalSidebarNav: true,
      desktopSection: 'database',
      onSaveAppSettings,
    });

    fireEvent.click(screen.getByRole('button', { name: 'Save Settings' }));
    await waitFor(() => {
      expect(screen.getByText('Save failed')).toBeInTheDocument();
    });

    view.rerender(
      <SettingsModal
        open
        externalSidebarNav
        desktopSection="fanout"
        config={baseConfig}
        health={baseHealth}
        appSettings={baseSettings}
        onClose={vi.fn()}
        onSave={vi.fn(async () => {})}
        onSaveAppSettings={onSaveAppSettings}
        onSetPrivateKey={vi.fn(async () => {})}
        onReboot={vi.fn(async () => {})}
        onDisconnect={vi.fn(async () => {})}
        onReconnect={vi.fn(async () => {})}
        onAdvertise={vi.fn(async () => {})}
        onHealthRefresh={vi.fn(async () => {})}
        onRefreshAppSettings={vi.fn(async () => {})}
      />
    );

    expect(screen.queryByText('Save failed')).not.toBeInTheDocument();
  });

  it('does not call onClose after save/reboot flows in page mode', async () => {
    const onClose = vi.fn();
    const onSave = vi.fn(async () => {});
    const onSetPrivateKey = vi.fn(async () => {});
    const onReboot = vi.fn(async () => {});

    renderModal({
      pageMode: true,
      onClose,
      onSave,
      onSetPrivateKey,
      onReboot,
      onDisconnect: vi.fn(async () => {}),
      onReconnect: vi.fn(async () => {}),
    });
    openRadioSection();

    fireEvent.click(screen.getByRole('button', { name: 'Save & Reboot' }));
    await waitFor(() => {
      expect(onSave).toHaveBeenCalledTimes(1);
      expect(onReboot).toHaveBeenCalledTimes(1);
    });
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Set Private Key (write-only)'), {
      target: { value: 'a'.repeat(64) },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Set Private Key & Reboot' }));

    await waitFor(() => {
      expect(onSetPrivateKey).toHaveBeenCalledWith('a'.repeat(64));
      expect(onReboot).toHaveBeenCalledTimes(2);
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('stores and clears reopen-last-conversation preference locally', () => {
    window.location.hash = '#raw';
    renderModal();
    openLocalSection();

    const checkbox = screen.getByLabelText('Reopen to last viewed channel/conversation');
    expect(checkbox).not.toBeChecked();

    fireEvent.click(checkbox);

    expect(localStorage.getItem(REOPEN_LAST_CONVERSATION_KEY)).toBe('1');
    expect(localStorage.getItem(LAST_VIEWED_CONVERSATION_KEY)).toContain('"type":"raw"');

    fireEvent.click(checkbox);

    expect(localStorage.getItem(REOPEN_LAST_CONVERSATION_KEY)).toBeNull();
    expect(localStorage.getItem(LAST_VIEWED_CONVERSATION_KEY)).toBeNull();
  });

  it('purges decrypted raw packets via maintenance endpoint action', async () => {
    const runMaintenanceSpy = vi.spyOn(api, 'runMaintenance').mockResolvedValue({
      packets_deleted: 12,
      vacuumed: true,
    });

    renderModal();
    openDatabaseSection();

    fireEvent.click(screen.getByRole('button', { name: 'Purge Archival Raw Packets' }));

    await waitFor(() => {
      expect(runMaintenanceSpy).toHaveBeenCalledWith({ purgeLinkedRawPackets: true });
    });
  });

  it('renders statistics section with fetched data', async () => {
    const mockStats: StatisticsResponse = {
      busiest_channels_24h: [
        { channel_key: 'AA'.repeat(16), channel_name: 'general', message_count: 42 },
      ],
      contact_count: 10,
      repeater_count: 3,
      channel_count: 5,
      total_packets: 200,
      decrypted_packets: 150,
      undecrypted_packets: 50,
      total_dms: 25,
      total_channel_messages: 80,
      total_outgoing: 30,
      contacts_heard: { last_hour: 2, last_24_hours: 7, last_week: 10 },
      repeaters_heard: { last_hour: 1, last_24_hours: 3, last_week: 3 },
      path_hash_width_24h: {
        total_packets: 120,
        single_byte: 60,
        double_byte: 36,
        triple_byte: 24,
        single_byte_pct: 50,
        double_byte_pct: 30,
        triple_byte_pct: 20,
      },
    };

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(mockStats), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    renderModal({
      externalSidebarNav: true,
      desktopSection: 'statistics',
    });

    await waitFor(() => {
      expect(screen.getByText('Network')).toBeInTheDocument();
    });

    // Verify key labels are present
    expect(screen.getByText('Contacts')).toBeInTheDocument();
    expect(screen.getByText('Repeaters')).toBeInTheDocument();
    expect(screen.getByText('Direct Messages')).toBeInTheDocument();
    expect(screen.getByText('Channel Messages')).toBeInTheDocument();
    expect(screen.getByText('Sent (Outgoing)')).toBeInTheDocument();
    expect(screen.getByText('Total stored')).toBeInTheDocument();
    expect(screen.getByText('Decrypted')).toBeInTheDocument();
    expect(screen.getByText('Undecrypted')).toBeInTheDocument();
    expect(screen.getByText('Path Hash Width (24h)')).toBeInTheDocument();
    expect(
      screen.getByText(/Parsed stored raw packets from the last 24 hours: 120/)
    ).toBeInTheDocument();
    expect(screen.getByText('1-byte hops')).toBeInTheDocument();
    expect(screen.getByText('60 (50.0%)')).toBeInTheDocument();
    expect(screen.getByText('36 (30.0%)')).toBeInTheDocument();
    expect(screen.getByText('24 (20.0%)')).toBeInTheDocument();
    expect(screen.getByText('Contacts heard')).toBeInTheDocument();
    expect(screen.getByText('Repeaters heard')).toBeInTheDocument();

    // Busiest channels
    expect(screen.getByText('general')).toBeInTheDocument();
    expect(screen.getByText('42 msgs')).toBeInTheDocument();
  });

  it('fetches statistics when expanded in mobile external-nav mode', async () => {
    const mockStats: StatisticsResponse = {
      busiest_channels_24h: [],
      contact_count: 10,
      repeater_count: 3,
      channel_count: 5,
      total_packets: 200,
      decrypted_packets: 150,
      undecrypted_packets: 50,
      total_dms: 25,
      total_channel_messages: 80,
      total_outgoing: 30,
      contacts_heard: { last_hour: 2, last_24_hours: 7, last_week: 10 },
      repeaters_heard: { last_hour: 1, last_24_hours: 3, last_week: 3 },
      path_hash_width_24h: {
        total_packets: 120,
        single_byte: 60,
        double_byte: 36,
        triple_byte: 24,
        single_byte_pct: 50,
        double_byte_pct: 30,
        triple_byte_pct: 20,
      },
    };

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(mockStats), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    renderModal({
      mobile: true,
      externalSidebarNav: true,
      desktopSection: 'radio',
    });

    expect(fetchSpy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Statistics/i }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith('/api/statistics', expect.any(Object));
    });

    await waitFor(() => {
      expect(screen.getByText('Network')).toBeInTheDocument();
    });
  });
});
