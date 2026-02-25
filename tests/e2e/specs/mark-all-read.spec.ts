import { test, expect } from '@playwright/test';
import { getUnreads } from '../helpers/api';
import { seedChannelUnread } from '../helpers/seed';

const CHANNEL_NAME = '#markread-e2e';

test.describe('Mark all as read', () => {
  test('clears server and UI unread state', async ({ page }) => {
    // Seed a couple of unread channel messages
    seedChannelUnread({ channelName: CHANNEL_NAME, unreadCount: 2 });

    // Sanity: server reports unreads
    const before = await getUnreads();
    expect(Object.values(before.counts).some((c) => c > 0)).toBeTruthy();

    await page.goto('/');
    await expect(page.getByText(CHANNEL_NAME, { exact: true })).toBeVisible({ timeout: 15_000 });

    // Sidebar should show the mark-all control
    const markAll = page.getByText('Mark all as read');
    await expect(markAll).toBeVisible();

    await markAll.click();

    // Server unreads should now be empty
    await expect(async () => {
      const after = await getUnreads();
      expect(Object.keys(after.counts).length).toBe(0);
      expect(Object.keys(after.mentions).length).toBe(0);
    }).toPass({ timeout: 10_000, intervals: [1_000] });

    // Reload to ensure persistence
    await page.reload();
    await expect(page.getByText('Mark all as read')).not.toBeVisible();
  });
});
