import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { TracePane } from '../components/TracePane';
import type { Contact, RadioConfig, RadioTraceResponse } from '../types';
import { CONTACT_TYPE_REPEATER } from '../types';

function makeContact(
  publicKey: string,
  name: string | null,
  type = CONTACT_TYPE_REPEATER,
  overrides: Partial<Contact> = {}
): Contact {
  return {
    public_key: publicKey,
    name,
    type,
    flags: 0,
    direct_path: null,
    direct_path_len: -1,
    direct_path_hash_mode: -1,
    last_advert: null,
    lat: null,
    lon: null,
    last_seen: null,
    on_radio: false,
    favorite: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: null,
    ...overrides,
  };
}

const config: RadioConfig = {
  public_key: 'ff'.repeat(32),
  name: 'Base Radio',
  lat: 10,
  lon: 20,
  tx_power: 17,
  max_tx_power: 22,
  radio: { freq: 910.525, bw: 62.5, sf: 7, cr: 5 },
  path_hash_mode: 0,
  path_hash_mode_supported: true,
};

describe('TracePane', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('shows only full-key repeaters and filters by name or key', () => {
    render(
      <TracePane
        config={config}
        onRunTracePath={vi.fn()}
        contacts={[
          makeContact('11'.repeat(32), 'Relay Alpha'),
          makeContact('22'.repeat(6), 'Prefix Relay'),
          makeContact('33'.repeat(32), 'Client Node', 1),
          makeContact('44'.repeat(32), 'Relay Beta'),
        ]}
      />
    );

    expect(screen.getByText('Relay Alpha')).toBeInTheDocument();
    expect(screen.getByText('Relay Beta')).toBeInTheDocument();
    expect(screen.queryByText('Prefix Relay')).not.toBeInTheDocument();
    expect(screen.queryByText('Client Node')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Search repeaters'), { target: { value: 'beta' } });
    expect(screen.queryByText('Relay Alpha')).not.toBeInTheDocument();
    expect(screen.getByText('Relay Beta')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Search repeaters'), { target: { value: '111111' } });
    expect(screen.getByText('Relay Alpha')).toBeInTheDocument();
  });

  it('adds, reorders, removes, and sends a trace path with known repeaters', async () => {
    const relayA = makeContact('11'.repeat(32), 'Relay Alpha');
    const relayB = makeContact('22'.repeat(32), 'Relay Beta');
    const onRunTracePath = vi.fn(
      async (): Promise<RadioTraceResponse> => ({
        path_len: 2,
        timeout_seconds: 6,
        nodes: [
          {
            role: 'repeater',
            public_key: relayB.public_key,
            name: relayB.name,
            observed_hash: relayB.public_key.slice(0, 8),
            snr: 7.5,
          },
          {
            role: 'repeater',
            public_key: relayA.public_key,
            name: relayA.name,
            observed_hash: relayA.public_key.slice(0, 8),
            snr: 3.25,
          },
          {
            role: 'local',
            public_key: config.public_key,
            name: config.name,
            observed_hash: null,
            snr: 5.0,
          },
        ],
      })
    );

    render(
      <TracePane config={config} onRunTracePath={onRunTracePath} contacts={[relayA, relayB]} />
    );

    fireEvent.click(screen.getByRole('button', { name: /^add repeater relay alpha/i }));
    fireEvent.click(screen.getByRole('button', { name: /^add repeater relay beta/i }));

    expect(screen.getByText(/2 hops selected · 4-byte trace/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /move relay beta up/i }));
    fireEvent.click(screen.getByRole('button', { name: /send trace/i }));

    await waitFor(() => {
      expect(onRunTracePath).toHaveBeenCalledWith(4, [
        { public_key: relayB.public_key },
        { public_key: relayA.public_key },
      ]);
    });

    expect(screen.getByRole('heading', { name: 'Results (6.0s)' })).toBeInTheDocument();
    expect(screen.getByText('+7.5 dB')).toBeInTheDocument();
    expect(screen.getByText('+5.0 dB')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /remove relay alpha/i }));
    expect(screen.getByText(/1 hop selected · 4-byte trace/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /remove relay beta/i }));
    expect(screen.getByText('No hops selected')).toBeInTheDocument();
  });

  it('reverse link appends the reversed hop chain to build a return path (issue #287)', async () => {
    const relayA = makeContact('11'.repeat(32), 'Relay Alpha');
    const relayB = makeContact('22'.repeat(32), 'Relay Beta');
    const relayC = makeContact('33'.repeat(32), 'Relay Charlie');
    const onRunTracePath = vi.fn(
      async (): Promise<RadioTraceResponse> => ({
        path_len: 0,
        timeout_seconds: 6,
        nodes: [],
      })
    );

    render(
      <TracePane
        config={config}
        onRunTracePath={onRunTracePath}
        contacts={[relayA, relayB, relayC]}
      />
    );

    // Single hop: Reverse link is a no-op (and disabled).
    fireEvent.click(screen.getByRole('button', { name: /^add repeater relay alpha/i }));
    expect(screen.getByRole('button', { name: /reverse link/i })).toBeDisabled();

    // R1, R2, R3 -> append R2, R1 => R1, R2, R3, R2, R1.
    fireEvent.click(screen.getByRole('button', { name: /^add repeater relay beta/i }));
    fireEvent.click(screen.getByRole('button', { name: /^add repeater relay charlie/i }));
    fireEvent.click(screen.getByRole('button', { name: /reverse link/i }));

    expect(screen.getByText(/5 hops selected · 4-byte trace/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /send trace/i }));
    await waitFor(() => {
      expect(onRunTracePath).toHaveBeenCalledWith(4, [
        { public_key: relayA.public_key },
        { public_key: relayB.public_key },
        { public_key: relayC.public_key },
        { public_key: relayB.public_key },
        { public_key: relayA.public_key },
      ]);
    });
  });

  it('allows adding the same repeater multiple times from the picker row', () => {
    const relayA = makeContact('11'.repeat(32), 'Relay Alpha');

    render(<TracePane config={config} onRunTracePath={vi.fn()} contacts={[relayA]} />);

    fireEvent.click(screen.getByRole('button', { name: /^add repeater relay alpha/i }));
    fireEvent.click(screen.getByRole('button', { name: /^add repeater relay alpha/i }));

    expect(screen.getByText(/2 hops selected · 4-byte trace/)).toBeInTheDocument();
    expect(screen.getByText('Added 2 times')).toBeInTheDocument();
  });

  it('adds custom hops from the modal and locks later custom hops to the same byte width', async () => {
    const relayA = makeContact('11'.repeat(32), 'Relay Alpha');
    const onRunTracePath = vi.fn(
      async (): Promise<RadioTraceResponse> => ({
        path_len: 2,
        timeout_seconds: 4.5,
        nodes: [
          {
            role: 'custom',
            public_key: null,
            name: null,
            observed_hash: 'ae',
            snr: 4.0,
          },
          {
            role: 'repeater',
            public_key: relayA.public_key,
            name: relayA.name,
            observed_hash: '11',
            snr: 2.0,
          },
          {
            role: 'local',
            public_key: config.public_key,
            name: config.name,
            observed_hash: null,
            snr: 3.0,
          },
        ],
      })
    );

    render(<TracePane config={config} onRunTracePath={onRunTracePath} contacts={[relayA]} />);

    fireEvent.click(screen.getByRole('button', { name: 'Custom path' }));
    fireEvent.click(screen.getByRole('button', { name: '1-byte' }));
    fireEvent.change(screen.getByLabelText('Repeater prefix'), { target: { value: 'ae' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add custom hop' }));

    expect(screen.getByText(/1 hop selected · 1-byte trace/)).toBeInTheDocument();
    expect(screen.getByText('AE (1-byte)')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^add repeater relay alpha/i }));
    fireEvent.click(screen.getByRole('button', { name: /send trace/i }));

    await waitFor(() => {
      expect(onRunTracePath).toHaveBeenCalledWith(1, [
        { hop_hex: 'ae' },
        { public_key: relayA.public_key },
      ]);
    });

    fireEvent.click(screen.getByRole('button', { name: 'Custom path' }));
    expect(screen.getByRole('button', { name: '2-byte' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '4-byte' })).toBeDisabled();
    expect(screen.getByText(/custom hops are locked to 1-byte prefixes/i)).toBeInTheDocument();
  });

  it('Traced lists only trace-used repeaters in MRU order, persisted locally (issue #286)', async () => {
    const relayA = makeContact('11'.repeat(32), 'Relay Alpha');
    const relayB = makeContact('22'.repeat(32), 'Relay Beta');
    const relayC = makeContact('33'.repeat(32), 'Relay Charlie');
    const onRunTracePath = vi.fn(
      async (): Promise<RadioTraceResponse> => ({
        path_len: 0,
        timeout_seconds: 6,
        nodes: [],
      })
    );

    const { unmount } = render(
      <TracePane
        config={config}
        onRunTracePath={onRunTracePath}
        contacts={[relayA, relayB, relayC]}
      />
    );

    const rowNames = () =>
      screen
        .queryAllByRole('button', { name: /^add repeater/i })
        .map((row) => row.getAttribute('aria-label'));

    // No history yet: Traced shows an explanatory empty state, not the full list.
    fireEvent.click(screen.getByRole('button', { name: 'Traced' }));
    expect(screen.getByText(/no repeaters have been used in traces yet/i)).toBeInTheDocument();
    expect(rowNames()).toEqual([]);

    // Build and run a trace with B from the A/Z list.
    fireEvent.click(screen.getByRole('button', { name: 'A/Z' }));
    fireEvent.click(screen.getByRole('button', { name: /^add repeater relay beta/i }));
    fireEvent.click(screen.getByRole('button', { name: /send trace/i }));
    await waitFor(() => expect(onRunTracePath).toHaveBeenCalledTimes(1));

    // Traced lists only B; untraced A and C are filtered out.
    fireEvent.click(screen.getByRole('button', { name: 'Traced' }));
    expect(rowNames()).toEqual(['Add repeater Relay Beta']);

    // A second trace with C bumps it above B.
    fireEvent.click(screen.getByRole('button', { name: 'A/Z' }));
    fireEvent.click(screen.getByRole('button', { name: /remove relay beta/i }));
    fireEvent.click(screen.getByRole('button', { name: /^add repeater relay charlie/i }));
    fireEvent.click(screen.getByRole('button', { name: /send trace/i }));
    await waitFor(() => expect(onRunTracePath).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByRole('button', { name: 'Traced' }));
    expect(rowNames()).toEqual(['Add repeater Relay Charlie', 'Add repeater Relay Beta']);

    // Order persists across remounts via localStorage.
    unmount();
    render(
      <TracePane
        config={config}
        onRunTracePath={onRunTracePath}
        contacts={[relayA, relayB, relayC]}
      />
    );
    fireEvent.click(screen.getByRole('button', { name: 'Traced' }));
    expect(rowNames()).toEqual(['Add repeater Relay Charlie', 'Add repeater Relay Beta']);
  });

  it('seeds Traced from stored recent traces when no usage history exists', () => {
    const relayA = makeContact('11'.repeat(32), 'Relay Alpha');
    const relayB = makeContact('22'.repeat(32), 'Relay Beta');
    localStorage.setItem(
      'remoteterm-recent-traces',
      JSON.stringify([
        {
          ranAt: 1,
          hops: [
            { kind: 'repeater', publicKey: relayB.public_key, displayName: 'Relay Beta' },
            { kind: 'custom', hopHex: 'ae', hopBytes: 1, displayName: 'AE (1B)' },
          ],
        },
      ])
    );

    render(<TracePane config={config} onRunTracePath={vi.fn()} contacts={[relayA, relayB]} />);

    fireEvent.click(screen.getByRole('button', { name: 'Traced' }));
    expect(
      screen
        .getAllByRole('button', { name: /^add repeater/i })
        .map((row) => row.getAttribute('aria-label'))
    ).toEqual(['Add repeater Relay Beta']);
  });

  it('Dist. hides repeaters without a known distance when the radio has a location', () => {
    const located = makeContact('11'.repeat(32), 'Relay Located', CONTACT_TYPE_REPEATER, {
      lat: 10.1,
      lon: 20.1,
    });
    const unlocated = makeContact('22'.repeat(32), 'Relay Mystery');

    render(<TracePane config={config} onRunTracePath={vi.fn()} contacts={[located, unlocated]} />);

    // A/Z shows both.
    expect(screen.getByText('Relay Located')).toBeInTheDocument();
    expect(screen.getByText('Relay Mystery')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Dist.' }));
    expect(screen.getByText('Relay Located')).toBeInTheDocument();
    expect(screen.queryByText('Relay Mystery')).not.toBeInTheDocument();

    // Without a local radio location, the filter is skipped (note explains instead).
    fireEvent.change(screen.getByLabelText('Search repeaters'), { target: { value: 'mystery' } });
    expect(screen.getByText(/no repeaters with a known distance matched/i)).toBeInTheDocument();
  });

  it('caps the rendered repeater list and reports the overflow', () => {
    const contacts = Array.from({ length: 70 }, (_, i) =>
      makeContact(i.toString(16).padStart(2, '0').repeat(32), `Relay ${String(i).padStart(3, '0')}`)
    );

    render(<TracePane config={config} onRunTracePath={vi.fn()} contacts={contacts} />);

    expect(screen.getAllByRole('button', { name: /^add repeater/i })).toHaveLength(60);
    expect(screen.getByText(/showing the first 60 of 70 repeaters/i)).toBeInTheDocument();
  });

  it('drops an in-flight result after the draft path changes', async () => {
    const relayA = makeContact('11'.repeat(32), 'Relay Alpha');
    const relayB = makeContact('22'.repeat(32), 'Relay Beta');
    let resolveTrace: ((value: RadioTraceResponse) => void) | null = null;
    const onRunTracePath = vi.fn(
      () =>
        new Promise<RadioTraceResponse>((resolve) => {
          resolveTrace = resolve;
        })
    );

    render(
      <TracePane config={config} onRunTracePath={onRunTracePath} contacts={[relayA, relayB]} />
    );

    fireEvent.click(screen.getByRole('button', { name: /^add repeater relay alpha/i }));
    fireEvent.click(screen.getByRole('button', { name: /send trace/i }));

    await waitFor(() => {
      expect(onRunTracePath).toHaveBeenCalledWith(4, [{ public_key: relayA.public_key }]);
    });

    fireEvent.click(screen.getByRole('button', { name: /^add repeater relay beta/i }));

    expect(screen.getByText(/2 hops selected · 4-byte trace/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /send trace/i })).toBeEnabled();

    await act(async () => {
      resolveTrace?.({
        path_len: 1,
        timeout_seconds: 6,
        nodes: [
          {
            role: 'repeater',
            public_key: relayA.public_key,
            name: relayA.name,
            observed_hash: relayA.public_key.slice(0, 8),
            snr: 7.5,
          },
          {
            role: 'local',
            public_key: config.public_key,
            name: config.name,
            observed_hash: null,
            snr: 5.0,
          },
        ],
      });
    });

    expect(screen.queryByRole('heading', { name: 'Results (6.0s)' })).not.toBeInTheDocument();
    expect(screen.queryByText('+7.5 dB')).not.toBeInTheDocument();
    // The Results section stays hidden entirely until a result or error lands.
    expect(screen.queryByRole('heading', { name: /^results/i })).not.toBeInTheDocument();
  });
});
