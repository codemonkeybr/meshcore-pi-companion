import { describe, expect, it } from 'vitest';

import {
  buildTelemetryCsv,
  telemetryCsvFilename,
  toLocalIsoString,
} from '../components/repeater/RepeaterTelemetryHistoryPane';

const COLUMNS = [
  { key: 'timestamp_iso', header: 'Timestamp (ISO 8601)' },
  { key: 'timestamp', header: 'Unix Timestamp' },
  { key: 'battery_volts', header: 'Voltage (V)' },
  { key: 'lpp_temperature_ch1', header: 'Temperature Ch1 (°C)' },
];

describe('telemetryCsvFilename', () => {
  const at = new Date(2026, 6, 21, 9, 5, 3); // 2026-07-21 09:05:03 local

  it('formats as <name>_data_YYYYMMDD_HHMMSS.csv with zero padding', () => {
    expect(telemetryCsvFilename('BaseCamp', at)).toBe('BaseCamp_data_20260721_090503.csv');
  });

  it('reduces unsafe characters to single underscores', () => {
    expect(telemetryCsvFilename('Hill/Top Repeater #2!', at)).toBe(
      'Hill_Top_Repeater_2_data_20260721_090503.csv'
    );
  });

  it('falls back to "repeater" when the name sanitizes away entirely', () => {
    expect(telemetryCsvFilename('///', at)).toBe('repeater_data_20260721_090503.csv');
    expect(telemetryCsvFilename('', at)).toBe('repeater_data_20260721_090503.csv');
  });
});

describe('toLocalIsoString', () => {
  it('emits local wall-clock time with an explicit UTC offset', () => {
    const value = toLocalIsoString(new Date(2026, 0, 2, 3, 4, 5));
    // Offset varies with the runner's zone, so assert shape plus local fields.
    expect(value).toMatch(/^2026-01-02T03:04:05[+-]\d{2}:\d{2}$/);
  });
});

describe('buildTelemetryCsv', () => {
  it('writes a header row and one CRLF-terminated row per sample', () => {
    const csv = buildTelemetryCsv(
      [
        { timestamp: 1700000000, battery_volts: 4.05, lpp_temperature_ch1: 21.5 },
        { timestamp: 1700000600, battery_volts: 4.04, lpp_temperature_ch1: 21.6 },
      ],
      COLUMNS
    );

    const lines = csv.split('\r\n');
    expect(lines).toHaveLength(3);
    expect(lines[0]).toBe('Timestamp (ISO 8601),Unix Timestamp,Voltage (V),Temperature Ch1 (°C)');
    expect(lines[1]).toContain('1700000000,4.05,21.5');
    expect(lines[2]).toContain('1700000600,4.04,21.6');
  });

  it('leaves gaps empty rather than filling them with zeros', () => {
    const csv = buildTelemetryCsv([{ timestamp: 1700000000, battery_volts: undefined }], COLUMNS);

    const cells = csv.split('\r\n')[1].split(',');
    expect(cells[2]).toBe(''); // battery_volts
    expect(cells[3]).toBe(''); // absent LPP sensor
  });

  it('strips floating-point noise without flattening small magnitudes', () => {
    // 0.1 + 0.2 === 0.30000000000000004 — real IEEE-754 noise rather than a
    // literal, which the linter rejects for losing precision at parse time.
    const noisy = 0.1 + 0.2;
    expect(`${noisy}`).toBe('0.30000000000000004');

    const csv = buildTelemetryCsv(
      [{ timestamp: 1, battery_volts: noisy, lpp_temperature_ch1: 0.000012 }],
      COLUMNS
    );

    const cells = csv.split('\r\n')[1].split(',');
    expect(cells[2]).toBe('0.3');
    expect(cells[3]).toBe('0.000012');
  });

  it('quotes headers containing commas or quotes', () => {
    const csv = buildTelemetryCsv(
      [],
      [{ key: 'x', header: 'Odd, "Name"' }] // sensor labels are user-influenced
    );

    expect(csv).toBe('"Odd, ""Name"""');
  });

  it('renders the ISO column from the sample timestamp', () => {
    const csv = buildTelemetryCsv([{ timestamp: 1700000000 }], COLUMNS);

    expect(csv.split('\r\n')[1].split(',')[0]).toBe(toLocalIsoString(new Date(1700000000 * 1000)));
  });
});
