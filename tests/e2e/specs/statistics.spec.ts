import { test, expect } from '@playwright/test';

test.describe('Statistics page', () => {
  test('statistics section shows data', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    // Open settings
    await page.getByText('Settings').click();

    // Click the Statistics section
    await page.getByRole('button', { name: /Statistics/i }).click();

    // Verify section headings/labels are visible (use heading role or exact match to avoid ambiguity)
    await expect(page.locator('h3').getByText('Network')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('Contacts', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('Channels', { exact: true }).first()).toBeVisible();
    await expect(page.locator('h3').getByText('Packets', { exact: true })).toBeVisible();
  });
});
