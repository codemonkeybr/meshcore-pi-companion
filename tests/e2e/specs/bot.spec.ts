import { test, expect } from '@playwright/test';
import {
  ensureFlightlessChannel,
  createFanoutConfig,
  deleteFanoutConfig,
} from '../helpers/api';

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

  test('create a bot via API, verify it in UI, trigger it, and verify response', async ({
    page,
  }) => {
    // --- Step 1: Create and enable bot via fanout API ---
    const bot = await createFanoutConfig({
      type: 'bot',
      name: 'E2E Test Bot',
      config: { code: BOT_CODE },
      enabled: true,
    });
    createdBotId = bot.id;

    // --- Step 2: Verify bot appears in settings UI ---
    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    await page.getByText('Settings').click();
    await page.getByRole('button', { name: /MQTT.*Automation/ }).click();

    // The bot name should be visible in the integration list
    await expect(page.getByText('E2E Test Bot')).toBeVisible();

    // Exit settings page mode
    await page.getByRole('button', { name: /Back to Chat/i }).click();

    // --- Step 3: Trigger the bot ---
    await page.getByText('#flightless', { exact: true }).first().click();

    const triggerMessage = `!e2etest ${Date.now()}`;
    const input = page.getByPlaceholder(/type a message|message #flightless/i);
    await input.fill(triggerMessage);
    await page.getByRole('button', { name: 'Send', exact: true }).click();

    // --- Step 4: Verify bot response appears ---
    // Bot has ~2s delay before responding, plus radio send time
    await expect(page.getByText('[BOT] e2e-ok')).toBeVisible({ timeout: 30_000 });
  });
});
