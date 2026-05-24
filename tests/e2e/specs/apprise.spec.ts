import { test, expect } from '@playwright/test';
import {
  createFanoutConfig,
  deleteFanoutConfig,
  getFanoutConfigs,
} from '../helpers/api';
import {
  createCaptureServer,
  fanoutHeader,
  openFanoutSettings,
  startIntegrationDraft,
} from '../helpers/fanout';

test.describe('Apprise integration settings', () => {
  let createdAppriseId: string | null = null;
  let receiver: ReturnType<typeof createCaptureServer>;
  let appriseUrl: string;

  test.beforeAll(async () => {
    receiver = createCaptureServer((port) => `json://127.0.0.1:${port}`);
    appriseUrl = await receiver.listen();
  });

  test.afterAll(async () => {
    receiver.close();
  });

  test.beforeEach(async () => {
    // Clean up any stale configs from previous failed runs
    const configs = await getFanoutConfigs();
    for (const c of configs.filter((c) => c.name === 'E2E Apprise')) {
      try {
        await deleteFanoutConfig(c.id);
      } catch { /* ignore */ }
    }
  });

  test.afterEach(async () => {
    if (createdAppriseId) {
      try {
        await deleteFanoutConfig(createdAppriseId);
      } catch {
        console.warn('Failed to delete test apprise config');
      }
      createdAppriseId = null;
    }
  });

  test('create apprise via UI, configure URLs, save as enabled', async ({ page }) => {
    await openFanoutSettings(page);
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    await startIntegrationDraft(page, 'Apprise');

    // Should navigate to the detail/edit view with a numbered default name
    await expect(page.locator('#fanout-edit-name')).toHaveValue(/Apprise #\d+/);

    // Fill in notification URL
    const urlsTextarea = page.locator('#fanout-apprise-urls');
    await urlsTextarea.fill(appriseUrl);

    // Verify preserve identity checkbox is checked by default
    const preserveIdentity = page.getByText('Preserve identity on Discord');
    await expect(preserveIdentity).toBeVisible();

    // Verify format textareas are present under Message Format heading
    await expect(page.getByText('Message Format')).toBeVisible();
    await expect(page.locator('#fanout-apprise-fmt-dm')).toBeVisible();
    await expect(page.locator('#fanout-apprise-fmt-chan')).toBeVisible();

    // Rename it
    const nameInput = page.locator('#fanout-edit-name');
    await nameInput.clear();
    await nameInput.fill('E2E Apprise');

    // Save as enabled
    await page.getByRole('button', { name: /Save as Enabled/i }).click();
    await expect(page.getByText('Integration saved and enabled')).toBeVisible();

    // Capture ID for cleanup before assertions that might fail
    const configs = await getFanoutConfigs();
    const apprise = configs.find((c) => c.name === 'E2E Apprise');
    if (apprise) {
      createdAppriseId = apprise.id;
    }

    // Should be back on list view with our apprise config visible
    await expect(fanoutHeader(page, 'E2E Apprise')).toBeVisible();
  });

  test('create apprise via API, verify options persist after edit', async ({ page }) => {
    const apprise = await createFanoutConfig({
      type: 'apprise',
      name: 'API Apprise',
      config: {
        urls: `${appriseUrl}\nslack://token_a/token_b/token_c`,
        preserve_identity: false,
        body_format_dm: '{sender_name}: {text}',
        body_format_channel: '{channel_name} | {sender_name}: {text}',
      },
      enabled: true,
    });
    createdAppriseId = apprise.id;

    await openFanoutSettings(page);
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    // Click Edit on our apprise config
    const row = fanoutHeader(page, 'API Apprise');
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: 'Edit' }).click();

    // Verify the URLs textarea has our content
    const urlsTextarea = page.locator('#fanout-apprise-urls');
    await expect(urlsTextarea).toHaveValue(new RegExp(appriseUrl.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    await expect(urlsTextarea).toHaveValue(/slack:\/\/token_a/);

    // Verify preserve identity checkbox reflects our config (unchecked)
    const preserveCheckbox = page
      .getByText('Preserve identity on Discord')
      .locator('xpath=ancestor::label[1]')
      .locator('input[type="checkbox"]');
    await expect(preserveCheckbox).not.toBeChecked();

    // Verify format textareas reflect our custom formats
    const dmFormat = page.locator('#fanout-apprise-fmt-dm');
    await expect(dmFormat).toHaveValue('{sender_name}: {text}');
    const chanFormat = page.locator('#fanout-apprise-fmt-chan');
    await expect(chanFormat).toHaveValue('{channel_name} | {sender_name}: {text}');

    // Go back
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByText('← Back to list').click();
    await expect(row).toBeVisible();
  });

  test('apprise shows scope selector', async ({ page }) => {
    const apprise = await createFanoutConfig({
      type: 'apprise',
      name: 'Scope Apprise',
      config: { urls: appriseUrl },
    });
    createdAppriseId = apprise.id;

    await openFanoutSettings(page);
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    const row = fanoutHeader(page, 'Scope Apprise');
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: 'Edit' }).click();

    // Verify scope selector is present
    await expect(page.getByText('Message Scope')).toBeVisible();
    await expect(page.getByText('All messages')).toBeVisible();

    // Select "All except listed" mode
    await page.getByText('All except listed channels/contacts').click();

    // Should show channel and contact lists with exclude label
    await expect(page.getByText('Channels (exclude)')).toBeVisible();

    // Go back
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByText('← Back to list').click();
    await expect(row).toBeVisible();
  });

  test('apprise disabled config shows disabled status and can be enabled via save button', async ({
    page,
  }) => {
    const apprise = await createFanoutConfig({
      type: 'apprise',
      name: 'Disabled Apprise',
      config: { urls: appriseUrl },
      enabled: false,
    });
    createdAppriseId = apprise.id;

    await openFanoutSettings(page);
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    // Should show "Disabled" status text
    const row = fanoutHeader(page, 'Disabled Apprise');
    await expect(row).toContainText('Disabled');

    // Edit it
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: 'Edit' }).click();

    // Save as enabled
    await page.getByRole('button', { name: /Save as Enabled/i }).click();
    await expect(page.getByText('Integration saved and enabled')).toBeVisible();

    // Verify it's now enabled via API
    const configs = await getFanoutConfigs();
    const updated = configs.find((c) => c.id === apprise.id);
    expect(updated?.enabled).toBe(true);
  });

  test('delete apprise via UI', async ({ page }) => {
    const apprise = await createFanoutConfig({
      type: 'apprise',
      name: 'Delete Me Apprise',
      config: { urls: appriseUrl },
    });
    createdAppriseId = apprise.id;

    await openFanoutSettings(page);
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    const row = fanoutHeader(page, 'Delete Me Apprise');
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: 'Edit' }).click();

    // Accept the confirmation dialog
    page.once('dialog', (dialog) => dialog.accept());

    await page.getByRole('button', { name: 'Delete' }).click();

    // Should be back on list, apprise gone
    await expect(row).not.toBeVisible();
    createdAppriseId = null;
  });
});
