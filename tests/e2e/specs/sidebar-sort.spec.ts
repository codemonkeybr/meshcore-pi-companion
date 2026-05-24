import { test, expect } from '@playwright/test';

test.describe('Sidebar sort toggle', () => {
  test('toggle sort order between A-Z and recent', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    // Sidebar sort is now tracked per section, so target the Channels control
    // explicitly instead of assuming a shared global toggle.
    const sortByRecent = page.getByRole('button', {
      name: 'Sort Channels by recent',
    });
    const sortAlpha = page.getByRole('button', {
      name: 'Sort Channels alphabetically',
    });

    // Wait for at least one sort button to appear
    await expect(sortByRecent.or(sortAlpha)).toBeVisible({ timeout: 10_000 });

    const isAlpha = await sortByRecent.isVisible();

    if (isAlpha) {
      // Currently A-Z, clicking should switch to recent
      await sortByRecent.click();
      await expect(sortAlpha).toBeVisible({ timeout: 5_000 });

      // Click again to revert
      await sortAlpha.click();
      await expect(sortByRecent).toBeVisible({ timeout: 5_000 });
    } else {
      // Currently recent, clicking should switch to A-Z
      await sortAlpha.click();
      await expect(sortByRecent).toBeVisible({ timeout: 5_000 });

      // Click again to revert
      await sortByRecent.click();
      await expect(sortAlpha).toBeVisible({ timeout: 5_000 });
    }
  });
});
