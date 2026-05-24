import { test, expect } from '@playwright/test';
import { getRadioConfig, updateRadioConfig } from '../helpers/api';

test.describe('Radio settings', () => {
  test('change radio name via settings UI and verify persistence', async ({ page }) => {
    const originalConfig = await getRadioConfig();
    const originalName = originalConfig.name;

    // Radio names are limited to 8 characters.
    // Use a randomized name per run to avoid collisions with stale state.
    const randomSuffix = Math.floor(Math.random() * 10000)
      .toString()
      .padStart(4, '0');
    const testName = `E2E${randomSuffix}`; // 7 chars

    try {
      await page.goto('/');
      await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

      // --- Step 1: Change the name via settings UI ---
      await page.getByText('Settings').click();
      const nameInput = page.locator('#name');
      await nameInput.clear();
      await nameInput.fill(testName);

      // Use "Save Radio Config" (no reboot) — name changes apply immediately
      await page.getByRole('button', { name: 'Save Radio Config', exact: true }).click();
      await expect(page.getByText('Radio config saved')).toBeVisible({ timeout: 10_000 });

      // --- Step 2: Verify via API (send_appstart refreshes cached info) ---
      const config = await getRadioConfig();
      expect(config.name).toBe(testName);

      // Exit settings page mode
      await page.getByRole('button', { name: /Back to Chat/i }).click();

      // --- Step 3: Verify persistence across page reload ---
      await page.reload();
      await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible({
        timeout: 15_000,
      });

      await page.getByText('Settings').click();
      await expect(page.locator('#name')).toHaveValue(testName, { timeout: 10_000 });
    } finally {
      // Always restore original name, even when assertions fail.
      try {
        await updateRadioConfig({ name: originalName });
      } catch {
        console.warn('Failed to restore radio name — manual intervention may be needed');
      }
    }

  });
});
