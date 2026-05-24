import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { VisualizerControls } from '../components/visualizer/VisualizerControls';

describe('VisualizerControls', () => {
  it('allows clearing numeric inputs while editing', () => {
    render(
      <VisualizerControls
        showControls
        setShowControls={vi.fn()}
        showAmbiguousPaths={false}
        setShowAmbiguousPaths={vi.fn()}
        showAmbiguousNodes={false}
        setShowAmbiguousNodes={vi.fn()}
        useAdvertPathHints={false}
        setUseAdvertPathHints={vi.fn()}
        collapseLikelyKnownSiblingRepeaters={false}
        setCollapseLikelyKnownSiblingRepeaters={vi.fn()}
        splitAmbiguousByTraffic={false}
        setSplitAmbiguousByTraffic={vi.fn()}
        observationWindowSec={5}
        setObservationWindowSec={vi.fn()}
        pruneStaleNodes
        setPruneStaleNodes={vi.fn()}
        pruneStaleMinutes={10}
        setPruneStaleMinutes={vi.fn()}
        letEmDrift={false}
        setLetEmDrift={vi.fn()}
        autoOrbit={false}
        setAutoOrbit={vi.fn()}
        chargeStrength={-100}
        setChargeStrength={vi.fn()}
        particleSpeedMultiplier={1}
        setParticleSpeedMultiplier={vi.fn()}
        nodeCount={0}
        linkCount={0}
        onExpandContract={vi.fn()}
        onClearAndReset={vi.fn()}
      />
    );

    const observationInput = screen.getByLabelText('Ack/echo listen window:') as HTMLInputElement;
    const pruneInput = screen.getByLabelText('Window:') as HTMLInputElement;

    fireEvent.change(observationInput, { target: { value: '' } });
    fireEvent.change(pruneInput, { target: { value: '' } });

    expect(observationInput.value).toBe('');
    expect(pruneInput.value).toBe('');
  });
});
