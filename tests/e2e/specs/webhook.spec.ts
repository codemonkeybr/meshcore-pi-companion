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

test.describe('Webhook integration settings', () => {
  let createdWebhookId: string | null = null;
  let receiver: ReturnType<typeof createCaptureServer>;
  let webhookUrl: string;

  test.beforeAll(async () => {
    receiver = createCaptureServer((port) => `http://127.0.0.1:${port}`);
    webhookUrl = await receiver.listen();
  });

  test.afterAll(async () => {
    receiver.close();
  });

  test.afterEach(async () => {
    if (createdWebhookId) {
      try {
        await deleteFanoutConfig(createdWebhookId);
      } catch {
        console.warn('Failed to delete test webhook');
      }
      createdWebhookId = null;
    }
  });

  test('create webhook via UI, configure, save as enabled, verify in list', async ({ page }) => {
    await openFanoutSettings(page);
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    await startIntegrationDraft(page, 'Webhook');

    // Should navigate to the detail/edit view with a numbered default name
    await expect(page.locator('#fanout-edit-name')).toHaveValue(/Webhook #\d+/);

    // Fill in webhook URL
    const urlInput = page.locator('#fanout-webhook-url');
    await urlInput.fill(webhookUrl);

    // Verify method defaults to POST
    await expect(page.locator('#fanout-webhook-method')).toHaveValue('POST');

    // Rename it
    const nameInput = page.locator('#fanout-edit-name');
    await nameInput.clear();
    await nameInput.fill('E2E Webhook');

    // Save as enabled
    await page.getByRole('button', { name: /Save as Enabled/i }).click();
    await expect(page.getByText('Integration saved and enabled')).toBeVisible();

    // Should be back on list view with our webhook visible
    await expect(page.getByText('E2E Webhook')).toBeVisible();
    await expect(page.getByText(webhookUrl)).toBeVisible();

    // Clean up via API
    const configs = await getFanoutConfigs();
    const webhook = configs.find((c) => c.name === 'E2E Webhook');
    if (webhook) {
      createdWebhookId = webhook.id;
    }
  });

  test('leaving a new webhook draft does not create a persisted config', async ({ page }) => {
    const existingConfigs = await getFanoutConfigs();
    const existingIds = new Set(existingConfigs.map((cfg) => cfg.id));

    await openFanoutSettings(page);
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    await startIntegrationDraft(page, 'Webhook');
    await expect(page.locator('#fanout-edit-name')).toHaveValue(/Webhook #\d+/);

    await page.locator('#fanout-edit-name').fill('Unsaved Webhook Draft');
    await page.locator('#fanout-webhook-url').fill(webhookUrl);

    page.once('dialog', (dialog) => dialog.accept());
    await page.getByText('← Back to list').click();
    await expect(page.getByText('Unsaved Webhook Draft')).not.toBeVisible();

    const updatedConfigs = await getFanoutConfigs();
    const newConfigs = updatedConfigs.filter((cfg) => !existingIds.has(cfg.id));
    expect(newConfigs).toHaveLength(0);
    expect(updatedConfigs.find((cfg) => cfg.name === 'Unsaved Webhook Draft')).toBeUndefined();
  });

  test('create webhook via API, edit in UI, save as disabled', async ({ page }) => {
    // Create via API
    const webhook = await createFanoutConfig({
      type: 'webhook',
      name: 'API Webhook',
      config: { url: webhookUrl, method: 'POST', headers: {} },
      enabled: true,
    });
    createdWebhookId = webhook.id;

    await openFanoutSettings(page);
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    // Click Edit on our webhook
    const row = fanoutHeader(page, 'API Webhook');
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: 'Edit' }).click();

    // Should be in edit view
    await expect(page.locator('#fanout-edit-name')).toHaveValue('API Webhook');

    // Change method to PUT
    await page.locator('#fanout-webhook-method').selectOption('PUT');

    // Save as disabled
    await page.getByRole('button', { name: /Save as Disabled/i }).click();
    await expect(page.locator('#fanout-edit-name')).not.toBeVisible();
    await expect(row).toContainText('Disabled');

    // Verify it's now disabled in the list
    const configs = await getFanoutConfigs();
    const updated = configs.find((c) => c.id === webhook.id);
    expect(updated?.enabled).toBe(false);
    expect(updated?.config.method).toBe('PUT');
  });

  test('webhook shows scope selector with channel/contact options', async ({ page }) => {
    const webhook = await createFanoutConfig({
      type: 'webhook',
      name: 'Scope Webhook',
      config: { url: webhookUrl, method: 'POST', headers: {} },
    });
    createdWebhookId = webhook.id;

    await openFanoutSettings(page);
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    // Click Edit
    const row = fanoutHeader(page, 'Scope Webhook');
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: 'Edit' }).click();

    // Verify scope selector is visible with the three webhook-applicable modes
    await expect(page.getByText('Message Scope')).toBeVisible();
    await expect(page.getByText('All messages')).toBeVisible();
    await expect(page.getByText('Only listed channels/contacts')).toBeVisible();
    await expect(page.getByText('All except listed channels/contacts')).toBeVisible();

    // Select "Only listed" to see channel/contact checkboxes
    await page.getByText('Only listed channels/contacts').click();

    // Should show Channels section (Contacts only appears if non-repeater contacts exist)
    await expect(page.getByText('Channels (include)')).toBeVisible();

    // Go back without saving
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByText('← Back to list').click();
    await expect(row).toBeVisible();
  });

  test('delete webhook via UI', async ({ page }) => {
    const webhook = await createFanoutConfig({
      type: 'webhook',
      name: 'Delete Me Webhook',
      config: { url: webhookUrl, method: 'POST', headers: {} },
    });
    createdWebhookId = webhook.id;

    await openFanoutSettings(page);
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    // Click Edit
    const row = fanoutHeader(page, 'Delete Me Webhook');
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: 'Edit' }).click();

    // Accept the confirmation dialog
    page.once('dialog', (dialog) => dialog.accept());

    // Click Delete
    await page.getByRole('button', { name: 'Delete' }).click();

    // Should be back on list, webhook gone
    await expect(row).not.toBeVisible();

    // Already deleted, clear the cleanup reference
    createdWebhookId = null;
  });
});
