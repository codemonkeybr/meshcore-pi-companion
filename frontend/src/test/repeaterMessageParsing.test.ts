/**
 * Tests for repeater-specific behavior.
 *
 * Verifies that CLI responses from repeaters would be mis-parsed by
 * parseSenderFromText, motivating the repeater bypass in MessageList.tsx.
 */

import { describe, it, expect } from 'vitest';
import { parseSenderFromText } from '../utils/messageParser';

describe('Repeater message sender parsing', () => {
  /**
   * CLI responses from repeaters often contain colons (e.g., "clock: 12:30:00").
   * If we parse these like normal channel messages, we'd incorrectly extract
   * "clock" as a sender name, breaking the display.
   *
   * The fix in MessageList.tsx is to check if the contact is a repeater and
   * skip parseSenderFromText entirely.
   */

  it('parseSenderFromText would incorrectly parse CLI responses with colons', () => {
    // This demonstrates WHY we skip parsing for repeaters
    const cliResponse = 'clock: 2024-01-09 12:30:00';
    const parsed = parseSenderFromText(cliResponse);

    // Without the repeater check, we'd get this incorrect result:
    expect(parsed.sender).toBe('clock');
    expect(parsed.content).toBe('2024-01-09 12:30:00');
    // This would display as "clock" sent "2024-01-09 12:30:00" - WRONG!
  });

  it('various CLI response formats are incorrectly parsed without repeater bypass', () => {
    const cliResponses = [
      'ver: 1.2.3',
      'tx: 20 dBm',
      'name: MyRepeater',
      'radio: 915.0,125,9,5',
      'Error: command not found',
      'uptime: 3d 12h 30m',
    ];

    for (const response of cliResponses) {
      // All of these would be incorrectly parsed without the repeater check
      const parsed = parseSenderFromText(response);
      expect(parsed.sender).not.toBeNull();
    }
  });
});
