import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { SettingsAboutSection } from '../components/settings/SettingsAboutSection';

describe('SettingsAboutSection', () => {
  it('renders the debug support snapshot link', () => {
    render(
      <SettingsAboutSection
        health={{
          status: 'ok',
          radio_connected: true,
          radio_initializing: false,
          connection_info: 'Serial: /dev/ttyUSB0',
          app_info: {
            version: '3.2.0-test',
            commit_hash: 'deadbeef',
          },
          database_size_mb: 1.2,
          oldest_undecrypted_timestamp: null,
          fanout_statuses: {},
          bots_disabled: false,
        }}
      />
    );

    const link = screen.getByRole('link', { name: /Open debug support snapshot/i });
    expect(link).toHaveAttribute('href', './api/debug');
    expect(link).toHaveAttribute('target', '_blank');
  });
});
