import { test, expect } from '@playwright/test';

test.describe('Node Map page', () => {
  test('node map page loads', async ({ page }) => {
    await page.goto('/#map');

    // Verify heading (also appears in sidebar, so scope to main)
    await expect(page.getByRole('main').getByText('Node Map')).toBeVisible({ timeout: 10_000 });

    // Verify legend elements. Scoped to the legend group: the "Since" filter
    // chips carry the same bucket names, so an unscoped text match is ambiguous.
    const legend = page.getByRole('group', { name: 'Marker recency legend' });
    await expect(legend.getByText('<1h')).toBeVisible();
    await expect(legend.getByText('<1d')).toBeVisible();
  });
});
