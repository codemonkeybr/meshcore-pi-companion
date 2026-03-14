import { describe, expect, it } from 'vitest';

import { getSceneNodeLabel } from '../components/visualizer/shared';

describe('visualizer shared label helpers', () => {
  it('adds an ambiguity suffix to in-graph labels for ambiguous nodes', () => {
    expect(
      getSceneNodeLabel({
        id: '?32',
        name: 'Likely Relay',
        type: 'repeater',
        isAmbiguous: true,
      })
    ).toBe('Likely Relay (?)');
  });

  it('does not add an ambiguity suffix to unambiguous nodes', () => {
    expect(
      getSceneNodeLabel({
        id: 'aaaaaaaaaaaa',
        name: 'Alice',
        type: 'client',
        isAmbiguous: false,
      })
    ).toBe('Alice');
  });
});
