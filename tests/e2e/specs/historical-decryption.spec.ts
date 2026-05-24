import { test, expect } from '@playwright/test';
import { deleteChannel, getChannels, getUndecryptedCount } from '../helpers/api';
import { injectEncryptedGroupText } from '../helpers/seed';

test.describe('Historical packet decryption', () => {
  const suffix = Date.now().toString().slice(-6);
  const channelName = `decrypt${suffix}`;
  const messageText = `hello from history ${suffix}`;

  test.afterAll(async () => {
    // Cleanup: delete the test channel
    const channels = await getChannels();
    const ch = channels.find((c) => c.name === `#${channelName}`);
    if (ch) {
      try {
        await deleteChannel(ch.key);
      } catch {
        // Best-effort
      }
    }
  });

  test('historical decryption recovers channel message from stored packet', async ({ page }) => {
    // Inject an encrypted GROUP_TEXT packet into raw_packets
    injectEncryptedGroupText({
      channelName,
      senderName: 'TestBot',
      messageText,
    });

    // Verify there are undecrypted packets
    const { count } = await getUndecryptedCount();
    expect(count).toBeGreaterThan(0);

    // Open the UI
    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    // Open new message modal → Hashtag tab
    await page.getByRole('button', { name: /add channel or contact/i }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await dialog.getByRole('tab', { name: /Hashtag/i }).click();

    // Fill channel name
    await dialog.locator('#hashtag-name').fill(channelName);

    // Check "Try decrypting" checkbox
    const tryHistorical = dialog.locator('#try-historical');
    // The checkbox may be hidden until undecrypted count loads — wait for label
    await expect(dialog.getByText(/Try decrypting.*stored packet/)).toBeVisible({ timeout: 10_000 });
    await tryHistorical.check();

    // Click Create
    await dialog.getByRole('button', { name: /^Create$/ }).click();

    // Wait for the decrypted message to appear in the conversation
    // Background decryption runs via POST /packets/decrypt/historical
    await expect(page.getByText(messageText)).toBeVisible({ timeout: 30_000 });
  });
});
