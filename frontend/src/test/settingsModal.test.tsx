import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SettingsModal } from '../components/SettingsModal';
import type {
  AppSettings,
  AppSettingsUpdate,
  HealthStatus,
  RadioConfig,
  RadioConfigUpdate,
} from '../types';
import type { SettingsSection } from '../components/SettingsModal';

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
};

const baseHealth: HealthStatus = {
  status: 'connected',
  radio_connected: true,
  connection_info: 'Serial: /dev/ttyUSB0',
  database_size_mb: 1.2,
  oldest_undecrypted_timestamp: null,
};

const baseSettings: AppSettings = {
  max_radio_contacts: 200,
  experimental_channel_double_send: false,
  favorites: [],
  auto_decrypt_dm_on_advert: false,
  sidebar_sort_order: 'recent',
  last_message_times: {},
  preferences_migrated: false,
  advert_interval: 0,
  bots: [],
};

function renderModal(overrides?: {
  appSettings?: AppSettings;
  onSaveAppSettings?: (update: AppSettingsUpdate) => Promise<void>;
  onRefreshAppSettings?: () => Promise<void>;
  onSave?: (update: RadioConfigUpdate) => Promise<void>;
  onClose?: () => void;
  onSetPrivateKey?: (key: string) => Promise<void>;
  onReboot?: () => Promise<void>;
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

  const commonProps = {
    open: overrides?.open ?? true,
    pageMode: overrides?.pageMode,
    config: baseConfig,
    health: baseHealth,
    appSettings: overrides?.appSettings ?? baseSettings,
    onClose,
    onSave,
    onSaveAppSettings,
    onSetPrivateKey,
    onReboot,
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

function openConnectivitySection() {
  const connectivityToggle = screen.getByRole('button', { name: /Connectivity/i });
  fireEvent.click(connectivityToggle);
}

describe('SettingsModal', () => {
  afterEach(() => {
    vi.restoreAllMocks();
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

  it('shows favorite-first contact sync helper text in connectivity tab', async () => {
    renderModal();

    openConnectivitySection();

    expect(
      screen.getByText(
        /Favorite contacts load first, then recent non-repeater contacts until this\s+limit is reached/i
      )
    ).toBeInTheDocument();
  });

  it('saves changed max contacts value through onSaveAppSettings', async () => {
    const { onSaveAppSettings } = renderModal();

    openConnectivitySection();

    const maxContactsInput = screen.getByLabelText('Max Contacts on Radio');
    fireEvent.change(maxContactsInput, { target: { value: '250' } });

    fireEvent.click(screen.getByRole('button', { name: 'Save Settings' }));

    await waitFor(() => {
      expect(onSaveAppSettings).toHaveBeenCalledWith({ max_radio_contacts: 250 });
    });
  });

  it('does not save max contacts when unchanged', async () => {
    const { onSaveAppSettings } = renderModal({
      appSettings: { ...baseSettings, max_radio_contacts: 200 },
    });

    openConnectivitySection();
    fireEvent.click(screen.getByRole('button', { name: 'Save Settings' }));

    await waitFor(() => {
      expect(onSaveAppSettings).not.toHaveBeenCalled();
    });
  });

  it('saves experimental channel double-send toggle through onSaveAppSettings', async () => {
    const { onSaveAppSettings } = renderModal({
      appSettings: { ...baseSettings, experimental_channel_double_send: false },
    });

    openConnectivitySection();

    const toggle = screen.getByLabelText('Always send channel messages twice');
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole('button', { name: 'Save Settings' }));

    await waitFor(() => {
      expect(onSaveAppSettings).toHaveBeenCalledWith({
        experimental_channel_double_send: true,
      });
    });
  });

  it('renders selected section from external sidebar nav on desktop mode', async () => {
    renderModal({
      externalSidebarNav: true,
      desktopSection: 'bot',
    });

    expect(screen.getByText('No bots configured')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Connectivity/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Preset')).not.toBeInTheDocument();
  });

  it('toggles sections in mobile accordion mode', () => {
    renderModal({ mobile: true });
    const identityToggle = screen.getAllByRole('button', { name: /Identity/i })[0];

    expect(screen.queryByLabelText('Preset')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Public Key')).not.toBeInTheDocument();

    fireEvent.click(identityToggle);
    expect(screen.getByLabelText('Public Key')).toBeInTheDocument();

    fireEvent.click(identityToggle);
    expect(screen.queryByLabelText('Public Key')).not.toBeInTheDocument();
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
        desktopSection="bot"
        config={baseConfig}
        health={baseHealth}
        appSettings={baseSettings}
        onClose={vi.fn()}
        onSave={vi.fn(async () => {})}
        onSaveAppSettings={onSaveAppSettings}
        onSetPrivateKey={vi.fn(async () => {})}
        onReboot={vi.fn(async () => {})}
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
    });

    fireEvent.click(screen.getByRole('button', { name: 'Save Radio Config & Reboot' }));
    await waitFor(() => {
      expect(onSave).toHaveBeenCalledTimes(1);
      expect(onReboot).toHaveBeenCalledTimes(1);
    });
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /Identity/i }));
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
});
