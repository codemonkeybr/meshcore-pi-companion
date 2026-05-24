import { test, expect } from '@playwright/test';
import { createChannel, deleteChannel } from '../helpers/api';

const REOPEN_LAST_CONVERSATION_KEY = 'remoteterm-reopen-last-conversation';
const LAST_VIEWED_CONVERSATION_KEY = 'remoteterm-last-viewed-conversation';

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

test.describe('Reopen last conversation (device-local)', () => {
  let channelName = '';
  let channelKey = '';

  test.beforeAll(async () => {
    channelName = `#e2ereopen${Date.now().toString().slice(-6)}`;
    const channel = await createChannel(channelName);
    channelKey = channel.key;
  });

  test.afterAll(async () => {
    try {
      await deleteChannel(channelKey);
    } catch {
      // Best-effort cleanup
    }
  });

  test('reopens last viewed conversation on startup when enabled', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();
    await expect.poll(() => new URL(page.url()).hash).toBe('');

    await page.getByText(channelName, { exact: true }).first().click();
    await expect(
      page.getByPlaceholder(new RegExp(`message\\s+${escapeRegex(channelName)}`, 'i'))
    ).toBeVisible();

    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByRole('button', { name: /Local Configuration/i }).click();
    await page.getByLabel('Reopen Last Conversation').check();
    await page.getByRole('button', { name: 'Back to Chat' }).click();

    // Fresh launch path without hash should restore the saved conversation.
    await page.goto('/');
    await expect(
      page.getByPlaceholder(new RegExp(`message\\s+${escapeRegex(channelName)}`, 'i'))
    ).toBeVisible();
    await expect.poll(() => new URL(page.url()).hash).toBe('');
  });

  test('clears local storage and falls back to default when disabled', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    await page.getByText(channelName, { exact: true }).first().click();
    await expect(
      page.getByPlaceholder(new RegExp(`message\\s+${escapeRegex(channelName)}`, 'i'))
    ).toBeVisible();

    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByRole('button', { name: /Local Configuration/i }).click();

    const reopenToggle = page.getByLabel('Reopen Last Conversation');
    await reopenToggle.check();
    await reopenToggle.uncheck();

    const localState = await page.evaluate(
      ([enabledKey, lastViewedKey]) => ({
        enabled: localStorage.getItem(enabledKey),
        lastViewed: localStorage.getItem(lastViewedKey),
      }),
      [REOPEN_LAST_CONVERSATION_KEY, LAST_VIEWED_CONVERSATION_KEY]
    );
    expect(localState.enabled).toBeNull();
    expect(localState.lastViewed).toBeNull();

    await page.getByRole('button', { name: 'Back to Chat' }).click();
    await page.goto('/');
    await expect(page.getByPlaceholder(/message\s+Public/i)).toBeVisible();
    await expect.poll(() => new URL(page.url()).hash).toBe('');
  });
});
