import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CrackerPanel } from '../components/CrackerPanel';

vi.mock('meshcore-hashtag-cracker', () => ({
  GroupTextCracker: class {
    isGpuAvailable() {
      return false;
    }
    destroy() {}
    setWordlist() {}
    abort() {}
  },
}));

vi.mock('nosleep.js', () => ({
  default: class {
    enable() {}
    disable() {}
  },
}));

vi.mock('../api', () => ({
  api: {
    getUndecryptedPacketCount: vi.fn(),
  },
}));

vi.mock('../components/ui/sonner', () => ({
  toast: {
    error: vi.fn(),
  },
}));

import { api } from '../api';

const mockedApi = vi.mocked(api);

describe('CrackerPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getUndecryptedPacketCount.mockResolvedValue({ count: 0 });
  });

  it('allows clearing max length while editing', async () => {
    render(<CrackerPanel packets={[]} channels={[]} onChannelCreate={vi.fn()} visible={false} />);

    await waitFor(() => {
      expect(mockedApi.getUndecryptedPacketCount).toHaveBeenCalled();
    });

    const maxLengthInput = screen.getByLabelText('Max Length:') as HTMLInputElement;
    fireEvent.change(maxLengthInput, { target: { value: '' } });

    expect(maxLengthInput.value).toBe('');
  });
});
