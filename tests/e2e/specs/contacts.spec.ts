import { test, expect } from '@playwright/test';
import { getContacts, type Contact } from '../helpers/api';

/** Escape special regex characters in a string. */
function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Find a named non-repeater contact (type 2 = repeater). */
function findChatContact(contacts: Contact[]): Contact | undefined {
  return contacts.find((c) => c.name && c.name.trim().length > 0 && c.type !== 2);
}

test.describe('Contacts sidebar & info pane', () => {
  test('contacts appear in sidebar and clicking opens conversation', async ({ page }) => {
    const contacts = await getContacts();
    const named = findChatContact(contacts);
    if (!named) {
      test.skip(true, 'No named non-repeater contacts synced from radio');
      return;
    }

    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    // Click the contact in the sidebar
    await page.getByText(named.name!, { exact: true }).first().click();

    // Verify composer placeholder says "Message [name]..."
    const escapedName = escapeRegex(named.name!.trim());
    await expect(
      page.getByPlaceholder(new RegExp(`message\\s+${escapedName}`, 'i'))
    ).toBeVisible({ timeout: 10_000 });
  });

  test('contact info pane shows profile data', async ({ page }) => {
    const contacts = await getContacts();
    const named = findChatContact(contacts);
    if (!named) {
      test.skip(true, 'No named non-repeater contacts synced from radio');
      return;
    }

    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    // Open contact conversation
    await page.getByText(named.name!, { exact: true }).first().click();
    const escapedName = escapeRegex(named.name!.trim());
    await expect(
      page.getByPlaceholder(new RegExp(`message\\s+${escapedName}`, 'i'))
    ).toBeVisible({ timeout: 10_000 });

    // Click avatar to open contact info sheet
    await page.locator('[title="View contact info"]').click();

    // Verify sheet opens with public key text and type badge
    // Scope to the Contact Info pane to avoid matching the header pubkey
    const infoPane = page.getByLabel('Contact Info');
    await expect(infoPane.locator('[title="Click to copy"]')).toBeVisible({ timeout: 10_000 });
    await expect(infoPane.getByText(named.public_key.slice(0, 8)).first()).toBeVisible();
  });

  test('copy public key from contact info pane', async ({ page }) => {
    const contacts = await getContacts();
    const named = findChatContact(contacts);
    if (!named) {
      test.skip(true, 'No named non-repeater contacts synced from radio');
      return;
    }

    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    await page.getByText(named.name!, { exact: true }).first().click();
    const escapedName = escapeRegex(named.name!.trim());
    await expect(
      page.getByPlaceholder(new RegExp(`message\\s+${escapedName}`, 'i'))
    ).toBeVisible({ timeout: 10_000 });

    await page.locator('[title="View contact info"]').click();

    // Grant clipboard permissions
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);

    // Click public key to copy (scope to Contact Info pane)
    const infoPane = page.getByLabel('Contact Info');
    const pubkeySpan = infoPane.locator('[title="Click to copy"]');
    await expect(pubkeySpan).toBeVisible({ timeout: 10_000 });
    await pubkeySpan.click();

    // Verify toast
    await expect(page.getByText('Public key copied!')).toBeVisible({ timeout: 5_000 });
  });
});
