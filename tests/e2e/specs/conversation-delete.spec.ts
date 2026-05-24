import { test, expect } from '@playwright/test';
import { createChannel, deleteChannel, getChannels } from '../helpers/api';

const PUBLIC_CHANNEL_KEY = '8B3387E9C5CDEA6AC9E5EDBAA115CD72';

test.describe('Conversation deletion flow', () => {
  test.beforeAll(async () => {
    const channels = await getChannels();
    if (!channels.some((c) => c.key === PUBLIC_CHANNEL_KEY)) {
      await createChannel('Public');
    }
  });

  test('deleting active channel removes it from sidebar and clears composer', async ({ page }) => {
    const channelName = `#e2edel${Date.now().toString().slice(-6)}`;
    const channel = await createChannel(channelName);

    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    await page.getByText(channelName, { exact: true }).first().click();
    await expect(page.getByPlaceholder(new RegExp(`message\\s+${channelName}`, 'i'))).toBeVisible();

    page.once('dialog', async (dialog) => {
      await dialog.accept();
    });
    await page.getByTitle('Delete').click();

    await expect(page.getByText('Channel deleted')).toBeVisible();
    await expect(page.getByText(channelName, { exact: true })).not.toBeVisible();
    await expect(page.getByPlaceholder(new RegExp(`message\\s+${channelName}`, 'i'))).not.toBeVisible();

    try {
      await deleteChannel(channel.key);
    } catch {
      // Best-effort cleanup
    }
  });

  test('deleting active channel falls back to Public conversation', async ({ page }) => {
    const channelName = `#e2edel${Date.now().toString().slice(-6)}`;
    const channel = await createChannel(channelName);

    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    await page.getByText(channelName, { exact: true }).first().click();
    await expect(page.getByPlaceholder(new RegExp(`message\\s+${channelName}`, 'i'))).toBeVisible();

    page.once('dialog', async (dialog) => {
      await dialog.accept();
    });
    await page.getByTitle('Delete').click();

    await expect(page.getByPlaceholder(/message\s+public/i)).toBeVisible({ timeout: 15_000 });
  });
});
