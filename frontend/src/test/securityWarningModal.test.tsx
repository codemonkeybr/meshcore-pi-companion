import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  disableBotsUntilRestart: vi.fn(),
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock('../api', () => ({
  api: {
    disableBotsUntilRestart: mocks.disableBotsUntilRestart,
  },
}));

vi.mock('../components/ui/sonner', () => ({
  toast: mocks.toast,
}));

import { SecurityWarningModal } from '../components/SecurityWarningModal';
import type { HealthStatus } from '../types';

const baseHealth: HealthStatus = {
  status: 'degraded',
  radio_connected: false,
  radio_initializing: false,
  connection_info: null,
  database_size_mb: 1.2,
  oldest_undecrypted_timestamp: null,
  fanout_statuses: {},
  bots_disabled: false,
  basic_auth_enabled: false,
};

describe('SecurityWarningModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    mocks.disableBotsUntilRestart.mockResolvedValue({
      status: 'ok',
      bots_disabled: true,
      bots_disabled_source: 'until_restart',
    });
  });

  it('shows the warning when bots are enabled and basic auth is off', () => {
    render(<SecurityWarningModal health={baseHealth} />);

    expect(screen.getByText('Unprotected bot execution is enabled')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Do Not Warn Me On This Device Again' })
    ).toBeDisabled();
  });

  it('does not show when bots are disabled', () => {
    render(<SecurityWarningModal health={{ ...baseHealth, bots_disabled: true }} />);

    expect(screen.queryByText('Unprotected bot execution is enabled')).not.toBeInTheDocument();
  });

  it('does not show when basic auth is enabled', () => {
    render(<SecurityWarningModal health={{ ...baseHealth, basic_auth_enabled: true }} />);

    expect(screen.queryByText('Unprotected bot execution is enabled')).not.toBeInTheDocument();
  });

  it('persists dismissal only after the checkbox is acknowledged', async () => {
    const user = userEvent.setup();

    render(<SecurityWarningModal health={baseHealth} />);

    const dismissButton = screen.getByRole('button', {
      name: 'Do Not Warn Me On This Device Again',
    });
    await user.click(screen.getByLabelText('Acknowledge bot security risk'));
    expect(dismissButton).toBeEnabled();

    await user.click(dismissButton);

    expect(window.localStorage.getItem('meshcore_security_warning_acknowledged')).toBe('true');
    expect(screen.queryByText('Unprotected bot execution is enabled')).not.toBeInTheDocument();
  });

  it('disables bots until restart from the warning modal', async () => {
    const user = userEvent.setup();

    render(<SecurityWarningModal health={baseHealth} />);

    await user.click(screen.getByRole('button', { name: 'Disable Bots Until Server Restart' }));

    expect(mocks.disableBotsUntilRestart).toHaveBeenCalledTimes(1);
    expect(mocks.toast.success).toHaveBeenCalledWith('Bots disabled until restart');
    expect(screen.queryByText('Unprotected bot execution is enabled')).not.toBeInTheDocument();
  });

  it('shows the warning again after temporary bot disable disappears on a later health update', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<SecurityWarningModal health={baseHealth} />);

    await user.click(screen.getByRole('button', { name: 'Disable Bots Until Server Restart' }));
    expect(screen.queryByText('Unprotected bot execution is enabled')).not.toBeInTheDocument();

    rerender(
      <SecurityWarningModal
        health={{ ...baseHealth, bots_disabled: true, bots_disabled_source: 'until_restart' }}
      />
    );
    expect(screen.queryByText('Unprotected bot execution is enabled')).not.toBeInTheDocument();

    rerender(<SecurityWarningModal health={baseHealth} />);

    await waitFor(() => {
      expect(screen.getByText('Unprotected bot execution is enabled')).toBeInTheDocument();
    });
  });
});
