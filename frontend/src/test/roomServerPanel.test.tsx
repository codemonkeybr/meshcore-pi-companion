import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

import { RoomServerPanel, resetRoomCacheForTests } from '../components/RoomServerPanel';
import type { Contact } from '../types';

vi.mock('../api', () => ({
  api: {
    roomLogin: vi.fn(),
    roomStatus: vi.fn(),
    roomAcl: vi.fn(),
    roomLppTelemetry: vi.fn(),
    sendRepeaterCommand: vi.fn(),
  },
}));

vi.mock('../components/ui/sonner', () => ({
  toast: Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  }),
}));

const { api: _rawApi } = await import('../api');
const mockApi = _rawApi as unknown as Record<string, Mock>;
const { toast } = await import('../components/ui/sonner');
const mockToast = toast as unknown as Record<string, Mock>;

const roomContact: Contact = {
  public_key: 'aa'.repeat(32),
  name: 'Ops Board',
  type: 3,
  flags: 0,
  direct_path: null,
  direct_path_len: -1,
  direct_path_hash_mode: 0,
  last_advert: null,
  lat: null,
  lon: null,
  last_seen: null,
  on_radio: false,
  favorite: false,
  last_contacted: null,
  last_read_at: null,
  first_seen: null,
};

describe('RoomServerPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    resetRoomCacheForTests();
  });

  it('keeps room controls available when login is not confirmed', async () => {
    mockApi.roomLogin.mockResolvedValueOnce({
      status: 'timeout',
      authenticated: false,
      message:
        "No login confirmation was heard from the room server. You're free to try sending messages; try logging in again if authenticated actions fail.",
    });
    const onAuthenticatedChange = vi.fn();

    render(<RoomServerPanel contact={roomContact} onAuthenticatedChange={onAuthenticatedChange} />);

    fireEvent.click(screen.getByText('Login with Existing Access / Guest'));

    await waitFor(() => {
      expect(screen.getByText('Show Tools')).toBeInTheDocument();
    });
    expect(screen.getByText('Show Tools')).toBeInTheDocument();
    expect(screen.getByText('Retry Existing-Access Login')).toBeInTheDocument();
    expect(mockToast.warning).toHaveBeenCalledWith("Couldn't confirm room login", {
      description:
        "No login confirmation was heard from the room server. You're free to try sending messages; try logging in again if authenticated actions fail.",
    });
    expect(onAuthenticatedChange).toHaveBeenLastCalledWith(true);
  });

  it('retains the last password for one-click retry after unlocking the panel', async () => {
    mockApi.roomLogin
      .mockResolvedValueOnce({
        status: 'timeout',
        authenticated: false,
        message: 'No reply heard',
      })
      .mockResolvedValueOnce({
        status: 'ok',
        authenticated: true,
        message: null,
      });

    render(<RoomServerPanel contact={roomContact} />);

    fireEvent.change(screen.getByLabelText('Repeater password'), {
      target: { value: 'secret-room-password' },
    });
    fireEvent.click(screen.getByText('Login with Password'));

    await waitFor(() => {
      expect(screen.getByText('Retry Password Login')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Retry Password Login'));

    await waitFor(() => {
      expect(mockApi.roomLogin).toHaveBeenNthCalledWith(
        1,
        roomContact.public_key,
        'secret-room-password'
      );
      expect(mockApi.roomLogin).toHaveBeenNthCalledWith(
        2,
        roomContact.public_key,
        'secret-room-password'
      );
    });
  });

  it('shows only a success toast after a confirmed login', async () => {
    mockApi.roomLogin.mockResolvedValueOnce({
      status: 'ok',
      authenticated: true,
      message: null,
    });

    render(<RoomServerPanel contact={roomContact} />);

    fireEvent.click(screen.getByText('Login with Existing Access / Guest'));

    await waitFor(() => {
      expect(screen.getByText('Show Tools')).toBeInTheDocument();
    });

    expect(screen.queryByText('Login confirmed by the room server.')).not.toBeInTheDocument();
    expect(screen.queryByText('Retry Password Login')).not.toBeInTheDocument();
    expect(screen.queryByText('Retry Existing-Access Login')).not.toBeInTheDocument();
    expect(mockToast.success).toHaveBeenCalledWith('Login confirmed by the room server.');
  });
});
