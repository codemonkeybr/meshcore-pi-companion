import { test, expect } from '@playwright/test';
import { deleteChannel, getChannels } from '../helpers/api';

test.describe('Create hashtag channel flow', () => {
  const suffix = Date.now().toString().slice(-6);
  const channelName1 = `e2echan${suffix}a`;
  const channelName2 = `e2echan${suffix}b`;

  test.afterAll(async () => {
    // Cleanup: delete test channels
    const channels = await getChannels();
    for (const name of [`#${channelName1}`, `#${channelName2}`]) {
      const ch = channels.find((c) => c.name === name);
      if (ch) {
        try {
          await deleteChannel(ch.key);
        } catch {
          // Best-effort
        }
      }
    }
  });

  test('create a hashtag channel via UI', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    // Open new message modal
    await page.getByRole('button', { name: /add channel or contact/i }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();

    // Click "Hashtag" tab
    await dialog.getByRole('tab', { name: /Hashtag/i }).click();

    // Fill in channel name
    await dialog.locator('#hashtag-name').fill(channelName1);

    // Click "Create"
    await dialog.getByRole('button', { name: /^Create$/ }).click();

    // Verify channel appears (sidebar or header)
    await expect(page.getByText(`#${channelName1}`, { exact: true }).first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test('create & add another keeps modal open', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    await page.getByRole('button', { name: /add channel or contact/i }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();

    await dialog.getByRole('tab', { name: /Hashtag/i }).click();
    await dialog.locator('#hashtag-name').fill(channelName2);

    // Click "Create & Add Another"
    await dialog.getByRole('button', { name: /Create & Add Another/i }).click();

    // Dialog should stay open and input should be cleared
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('#hashtag-name')).toHaveValue('');

    // First channel should have been created
    // Close dialog and verify
    await dialog.getByRole('button', { name: /Cancel/i }).click();
    await expect(page.getByText(`#${channelName2}`, { exact: true }).first()).toBeVisible({
      timeout: 10_000,
    });
  });
});
