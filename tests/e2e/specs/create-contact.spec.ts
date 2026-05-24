import { test, expect } from '@playwright/test';
import { deleteContact } from '../helpers/api';

test.describe('Create contact flow', () => {
  // A random 64-char hex key for the test contact
  const testKey = 'A'.repeat(64);
  const testName = `e2econtact${Date.now().toString().slice(-6)}`;

  test.afterAll(async () => {
    try {
      await deleteContact(testKey);
    } catch {
      // Best-effort cleanup
    }
  });

  test('create a new contact via the new message modal', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    // Open new message modal
    await page.getByRole('button', { name: /add channel or contact/i }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();

    // Click "Contact" tab
    await dialog.getByRole('tab', { name: /Contact/i }).click();

    // Fill in contact name and key
    await dialog.locator('#contact-name').fill(testName);
    await dialog.locator('#contact-key').fill(testKey);

    // Submit
    await dialog.getByRole('button', { name: /^Create$/ }).click();

    // Verify contact appears (sidebar or header)
    await expect(page.getByText(testName, { exact: true }).first()).toBeVisible({ timeout: 10_000 });
  });
});
