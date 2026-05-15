import { test, expect } from '@playwright/test';

test.describe('Sync Channels from Radio', () => {
  test('Sync Channels button is visible in Settings → Radio and syncs channels', async ({
    page,
  }) => {
    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    await page.getByText('Settings').click();

    // Button should be visible
    const syncButton = page.getByRole('button', { name: 'Sync Channels from Radio' });
    await expect(syncButton).toBeVisible();

    // Click it and expect a success toast
    const syncResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        response.url().includes('/api/channels/sync')
    );

    await syncButton.click();

    const syncResponse = await syncResponsePromise;
    expect(syncResponse.ok()).toBeTruthy();

    // Toast should appear
    await expect(page.getByText(/Synced \d+ channel/)).toBeVisible({ timeout: 10_000 });
  });
});
