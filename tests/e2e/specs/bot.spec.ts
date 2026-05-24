import { test, expect } from '@playwright/test';
import {
  ensureFlightlessChannel,
  deleteFanoutConfig,
  getFanoutConfigs,
} from '../helpers/api';
import { openFanoutSettings, startIntegrationDraft } from '../helpers/fanout';

const BOT_CODE = `def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path):
    if channel_name == "#flightless" and "!e2etest" in message_text.lower():
        return "[BOT] e2e-ok"
    return None`;

test.describe('Bot functionality', () => {
  let createdBotId: string | null = null;

  test.beforeAll(async () => {
    await ensureFlightlessChannel();
  });

  test.afterAll(async () => {
    // Clean up the bot we created
    if (createdBotId) {
      try {
        await deleteFanoutConfig(createdBotId);
      } catch {
        console.warn('Failed to delete test bot');
      }
    }
  });

  test('create a bot via UI, trigger it, and verify response', async ({
    page,
  }) => {
    await openFanoutSettings(page);
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    await startIntegrationDraft(page, 'Python Bot');
    await expect(page.locator('#fanout-edit-name')).toHaveValue(/Python Bot #\d+/);

    await page.locator('#fanout-edit-name').fill('E2E Test Bot');

    const codeEditor = page.locator('[aria-label="Bot code editor"] [contenteditable]');
    await codeEditor.click();
    await codeEditor.press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
    await codeEditor.fill(BOT_CODE);

    await page.getByRole('button', { name: /Save as Enabled/i }).click();
    await expect(page.getByText('Integration saved and enabled')).toBeVisible();

    await expect(page.getByText('E2E Test Bot')).toBeVisible();

    const configs = await getFanoutConfigs();
    const createdBot = configs.find((config) => config.name === 'E2E Test Bot');
    if (createdBot) {
      createdBotId = createdBot.id;
    }

    await page.getByRole('button', { name: /Back to Chat/i }).click();

    await page.getByText('#flightless', { exact: true }).first().click();

    const triggerMessage = `!e2etest ${Date.now()}`;
    const input = page.getByPlaceholder(/type a message|message #flightless/i);
    await input.fill(triggerMessage);
    await page.getByRole('button', { name: 'Send', exact: true }).click();

    await expect(page.getByText('[BOT] e2e-ok')).toBeVisible({ timeout: 30_000 });
  });
});
