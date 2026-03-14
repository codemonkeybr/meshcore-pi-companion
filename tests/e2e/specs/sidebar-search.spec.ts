import { test, expect } from '@playwright/test';
import { createChannel, createContact, deleteChannel, deleteContact } from '../helpers/api';

test.describe('Sidebar search/filter', () => {
  const suffix = Date.now().toString().slice(-6);
  const nameA = `#alpha${suffix}`;
  const nameB = `#bravo${suffix}`;
  const contactName = `Search Contact ${suffix}`;
  const contactKey = `feed${suffix.padStart(8, '0')}${'ab'.repeat(26)}`;
  let keyA = '';
  let keyB = '';

  test.beforeAll(async () => {
    const chA = await createChannel(nameA);
    const chB = await createChannel(nameB);
    await createContact(contactKey, contactName);
    keyA = chA.key;
    keyB = chB.key;
  });

  test.afterAll(async () => {
    try {
      await deleteContact(contactKey);
    } catch {
      // Best-effort cleanup
    }
    for (const key of [keyA, keyB]) {
      try {
        await deleteChannel(key);
      } catch {
        // Best-effort cleanup
      }
    }
  });

  test('search filters channel and contact conversations by name and key prefix', async ({
    page,
  }) => {
    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    // Seeded conversations should be visible.
    await expect(page.getByText(nameA, { exact: true })).toBeVisible();
    await expect(page.getByText(nameB, { exact: true })).toBeVisible();
    await expect(page.getByText(contactName, { exact: true })).toBeVisible();

    const searchInput = page.getByLabel('Search conversations');

    // Channel name query should filter to the matching channel only.
    await searchInput.fill(`alpha${suffix}`);
    await expect(page.getByText(nameA, { exact: true })).toBeVisible();
    await expect(page.getByText(nameB, { exact: true })).not.toBeVisible();
    await expect(page.getByText(contactName, { exact: true })).not.toBeVisible();

    // Contact name query should filter to the matching contact.
    await searchInput.fill(`contact ${suffix}`);
    await expect(page.getByText(contactName, { exact: true })).toBeVisible();
    await expect(page.getByText(nameA, { exact: true })).not.toBeVisible();
    await expect(page.getByText(nameB, { exact: true })).not.toBeVisible();

    // Contact key prefix query should also match that contact.
    await searchInput.fill(contactKey.slice(0, 12));
    await expect(page.getByText(contactName, { exact: true })).toBeVisible();
    await expect(page.getByText(nameA, { exact: true })).not.toBeVisible();

    // Clear search should restore the full conversation list.
    await page.getByTitle('Clear search').click();
    await expect(page.getByText(nameA, { exact: true })).toBeVisible();
    await expect(page.getByText(nameB, { exact: true })).toBeVisible();
    await expect(page.getByText(contactName, { exact: true })).toBeVisible();
  });
});
