import { describe, expect, it } from 'vitest';
import { PayloadType } from '@michaelhart/meshcore-decoder';

import { describeCiphertextStructure, formatHexByHop } from '../utils/rawPacketInspector';

describe('rawPacketInspector helpers', () => {
  it('formats path hex as hop-delimited groups', () => {
    expect(formatHexByHop('A1B2C3D4E5F6', 2)).toBe('A1B2 → C3D4 → E5F6');
    expect(formatHexByHop('AABBCC', 1)).toBe('AA → BB → CC');
  });

  it('leaves non-hop-aligned hex unchanged', () => {
    expect(formatHexByHop('A1B2C3', 2)).toBe('A1B2C3');
    expect(formatHexByHop('A1B2', null)).toBe('A1B2');
  });

  it('describes undecryptable ciphertext with multiline bullets', () => {
    expect(describeCiphertextStructure(PayloadType.GroupText, 9, 'fallback')).toContain(
      '\n• Timestamp (4 bytes)'
    );
    expect(describeCiphertextStructure(PayloadType.GroupText, 9, 'fallback')).toContain(
      '\n• Flags (1 byte)'
    );
    expect(describeCiphertextStructure(PayloadType.TextMessage, 12, 'fallback')).toContain(
      '\n• Message (remaining bytes)'
    );
  });
});
