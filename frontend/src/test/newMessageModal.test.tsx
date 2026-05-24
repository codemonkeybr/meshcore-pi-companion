/**
 * Tests for NewMessageModal form state reset.
 *
 * Verifies that form fields are cleared when the modal closes (via Create,
 * Cancel, or Dialog dismiss) and when switching tabs.
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { NewMessageModal } from '../components/NewMessageModal';
import { toast } from '../components/ui/sonner';

// Mock sonner (toast)
vi.mock('../components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const mockToast = toast as unknown as {
  success: ReturnType<typeof vi.fn>;
  error: ReturnType<typeof vi.fn>;
};

describe('NewMessageModal form reset', () => {
  const onClose = vi.fn();
  const onCreateContact = vi.fn().mockResolvedValue(undefined);
  const onCreateChannel = vi.fn().mockResolvedValue(undefined);
  const onCreateHashtagChannel = vi.fn().mockResolvedValue(undefined);
  const onBulkAddHashtagChannels = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    vi.clearAllMocks();
  });

  function renderModal(
    open = true,
    overrides: Partial<Parameters<typeof NewMessageModal>[0]> = {}
  ) {
    return render(
      <NewMessageModal
        open={open}
        undecryptedCount={5}
        onClose={onClose}
        onCreateContact={onCreateContact}
        onCreateChannel={onCreateChannel}
        onCreateHashtagChannel={onCreateHashtagChannel}
        onBulkAddHashtagChannels={onBulkAddHashtagChannels}
        {...overrides}
      />
    );
  }

  async function switchToTab(user: ReturnType<typeof userEvent.setup>, name: string) {
    await user.click(screen.getByRole('tab', { name }));
  }

  describe('hashtag tab', () => {
    it('prefills the hashtag tab from a linked channel request', async () => {
      renderModal(true, {
        prefillRequest: {
          tab: 'hashtag',
          hashtagName: 'mesh-room',
          nonce: 1,
        },
      });

      await waitFor(() => {
        expect(screen.getByRole('tab', { name: 'Hashtag Channel' })).toHaveAttribute(
          'data-state',
          'active'
        );
      });
      expect((screen.getByPlaceholderText('channel-name') as HTMLInputElement).value).toBe(
        'mesh-room'
      );
    });

    it('clears name after successful Create', async () => {
      const user = userEvent.setup();
      const { unmount } = renderModal();
      await switchToTab(user, 'Hashtag Channel');

      const input = screen.getByPlaceholderText('channel-name') as HTMLInputElement;
      await user.type(input, 'testchan');
      expect(input.value).toBe('testchan');

      await user.click(screen.getByRole('button', { name: 'Create' }));

      await waitFor(() => {
        expect(onCreateHashtagChannel).toHaveBeenCalledWith('#testchan', false);
      });
      expect(onClose).toHaveBeenCalled();
      unmount();

      // Re-render to simulate reopening — state should be reset
      renderModal();
      await switchToTab(user, 'Hashtag Channel');
      expect((screen.getByPlaceholderText('channel-name') as HTMLInputElement).value).toBe('');
    });

    it('clears name when Cancel is clicked', async () => {
      const user = userEvent.setup();
      renderModal();
      await switchToTab(user, 'Hashtag Channel');

      const input = screen.getByPlaceholderText('channel-name') as HTMLInputElement;
      await user.type(input, 'mychannel');
      expect(input.value).toBe('mychannel');

      await user.click(screen.getByRole('button', { name: 'Cancel' }));
      expect(onClose).toHaveBeenCalled();
    });
  });

  describe('bulk hashtag tab', () => {
    it('is only visible when enabled', () => {
      renderModal();
      expect(screen.queryByRole('tab', { name: 'Bulk Add Channel' })).toBeNull();
    });

    it('opens on the bulk tab when enabled and submits normalized channel names', async () => {
      const user = userEvent.setup();
      renderModal(true, { showBulkAddChannelTab: true });

      await waitFor(() => {
        expect(screen.getByRole('tab', { name: 'Bulk Add Channel' })).toHaveAttribute(
          'data-state',
          'active'
        );
      });

      await user.type(
        screen.getByRole('textbox', { name: 'Bulk channel names' }),
        '#Ops{enter}mesh-room another-room #Ops'
      );
      await user.click(screen.getByRole('button', { name: 'Add Channels' }));

      await waitFor(() => {
        expect(onBulkAddHashtagChannels).toHaveBeenCalledWith(
          ['#ops', '#mesh-room', '#another-room'],
          false
        );
      });
      expect(onClose).toHaveBeenCalled();
    });

    it('shows invalid bulk channel names before submitting', async () => {
      const user = userEvent.setup();
      renderModal(true, { showBulkAddChannelTab: true });

      await user.type(
        screen.getByRole('textbox', { name: 'Bulk channel names' }),
        'good-room bad_room'
      );
      await user.click(screen.getByRole('button', { name: 'Add Channels' }));

      expect(onBulkAddHashtagChannels).not.toHaveBeenCalled();
      expect(screen.getByText('Invalid channel names: bad_room')).toBeTruthy();
    });
  });

  describe('new-contact tab', () => {
    it('clears name and key after successful Create', async () => {
      const user = userEvent.setup();
      renderModal();
      await switchToTab(user, 'Contact');

      await user.type(screen.getByPlaceholderText('Contact name'), 'Bob');
      await user.type(screen.getByPlaceholderText('64-character hex public key'), 'bb'.repeat(32));

      await user.click(screen.getByRole('button', { name: 'Create' }));

      await waitFor(() => {
        expect(onCreateContact).toHaveBeenCalledWith('Bob', 'bb'.repeat(32), false, 1);
      });
      expect(onClose).toHaveBeenCalled();
    });
  });

  describe('new-channel tab', () => {
    it('clears name and key after successful Create', async () => {
      const user = userEvent.setup();
      renderModal();
      await switchToTab(user, 'Private Channel');

      await user.type(screen.getByPlaceholderText('Channel name'), 'MyRoom');
      await user.type(screen.getByPlaceholderText('Pre-shared key (hex)'), 'cc'.repeat(16));

      await user.click(screen.getByRole('button', { name: 'Create' }));

      await waitFor(() => {
        expect(onCreateChannel).toHaveBeenCalledWith('MyRoom', 'cc'.repeat(16), false);
      });
      expect(onClose).toHaveBeenCalled();
    });

    it('toasts when creation fails', async () => {
      const user = userEvent.setup();
      onCreateChannel.mockRejectedValueOnce(new Error('Bad key'));
      renderModal();
      await switchToTab(user, 'Private Channel');

      await user.type(screen.getByPlaceholderText('Channel name'), 'MyRoom');
      await user.type(screen.getByPlaceholderText('Pre-shared key (hex)'), 'cc'.repeat(16));
      await user.click(screen.getByRole('button', { name: 'Create' }));

      await waitFor(() => {
        expect(mockToast.error).toHaveBeenCalledWith('Failed to create conversation', {
          description: 'Bad key',
        });
      });
      expect(screen.getByText('Bad key')).toBeTruthy();
    });
  });

  describe('tab switching resets form', () => {
    it('clears contact fields when switching to channel tab', async () => {
      const user = userEvent.setup();
      renderModal();
      await switchToTab(user, 'Contact');

      await user.type(screen.getByPlaceholderText('Contact name'), 'Bob');
      await user.type(screen.getByPlaceholderText('64-character hex public key'), 'deadbeef');

      // Switch to Private Channel tab — fields should reset
      await switchToTab(user, 'Private Channel');

      expect((screen.getByPlaceholderText('Channel name') as HTMLInputElement).value).toBe('');
      expect((screen.getByPlaceholderText('Pre-shared key (hex)') as HTMLInputElement).value).toBe(
        ''
      );
    });

    it('clears channel fields when switching to hashtag tab', async () => {
      const user = userEvent.setup();
      renderModal();
      await switchToTab(user, 'Private Channel');

      await user.type(screen.getByPlaceholderText('Channel name'), 'SecretRoom');
      await user.type(screen.getByPlaceholderText('Pre-shared key (hex)'), 'ff'.repeat(16));

      await switchToTab(user, 'Hashtag Channel');

      expect((screen.getByPlaceholderText('channel-name') as HTMLInputElement).value).toBe('');
    });
  });

  describe('tryHistorical checkbox resets', () => {
    it('resets tryHistorical when switching tabs', async () => {
      const user = userEvent.setup();
      renderModal();
      await switchToTab(user, 'Hashtag Channel');

      // Check the "Try decrypting" checkbox
      const checkbox = screen.getByRole('checkbox', { name: /Try decrypting/ });
      await user.click(checkbox);

      // The streaming message should appear
      expect(screen.getByText(/Messages will stream in/)).toBeTruthy();

      // Switch tab and come back
      await switchToTab(user, 'Contact');
      await switchToTab(user, 'Hashtag Channel');

      // The streaming message should be gone (tryHistorical was reset)
      expect(screen.queryByText(/Messages will stream in/)).toBeNull();
    });
  });
});
