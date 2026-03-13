import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { StatusBar } from '../components/StatusBar';
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
};

describe('StatusBar', () => {
  it('shows Radio Initializing while setup is still running', () => {
    render(
      <StatusBar
        health={{ ...baseHealth, radio_connected: true, radio_initializing: true }}
        config={null}
        onSettingsClick={vi.fn()}
      />
    );

    expect(screen.getByRole('status', { name: 'Radio Initializing' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reconnect' })).not.toBeInTheDocument();
  });

  it('shows Radio OK when the radio is connected and ready', () => {
    render(
      <StatusBar
        health={{ ...baseHealth, status: 'ok', radio_connected: true }}
        config={null}
        onSettingsClick={vi.fn()}
      />
    );

    expect(screen.getByRole('status', { name: 'Radio OK' })).toBeInTheDocument();
  });

  it('shows Radio Disconnected when the radio is unavailable', () => {
    render(<StatusBar health={baseHealth} config={null} onSettingsClick={vi.fn()} />);

    expect(screen.getByRole('status', { name: 'Radio Disconnected' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reconnect' })).toBeInTheDocument();
  });

  it('shows Radio Paused and a Connect action when reconnect attempts are paused', () => {
    render(
      <StatusBar
        health={{ ...baseHealth, radio_state: 'paused' }}
        config={null}
        onSettingsClick={vi.fn()}
      />
    );

    expect(screen.getByRole('status', { name: 'Radio Paused' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Connect' })).toBeInTheDocument();
  });

  it('toggles between classic and light themes from the shortcut button', () => {
    localStorage.setItem('remoteterm-theme', 'cyberpunk');

    render(<StatusBar health={baseHealth} config={null} onSettingsClick={vi.fn()} />);

    const themeToggle = screen.getByRole('button', { name: 'Switch to light theme' });
    fireEvent.click(themeToggle);

    expect(localStorage.getItem('remoteterm-theme')).toBe('light');
    expect(document.documentElement.dataset.theme).toBe('light');

    fireEvent.click(screen.getByRole('button', { name: 'Switch to classic theme' }));

    expect(localStorage.getItem('remoteterm-theme')).toBe('original');
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });
});
