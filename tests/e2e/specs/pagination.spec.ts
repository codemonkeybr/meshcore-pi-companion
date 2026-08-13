import { test, expect } from '@playwright/test';
import { seedChannelMessages } from '../helpers/seed';

const CHANNEL_NAME = '#pagination-e2e';

test.describe('Message pagination ordering/dedup', () => {
  test('loads older pages without duplicates or ordering issues', async ({ page }) => {
    // Seed 250 messages; latest has highest index
    const seeded = seedChannelMessages({
      channelName: CHANNEL_NAME,
      count: 250,
      startTimestamp: Math.floor(Date.now() / 1000) - 260,
      outgoingEvery: 10,
      includePaths: true,
    });

    // Directly open the channel via URL hash to avoid sidebar filtering
    await page.goto(`/#channel/${seeded.key}/${CHANNEL_NAME.replace('#', '')}`);
    await expect(page.getByPlaceholder(/message/i)).toBeVisible({ timeout: 15_000 });

    // Latest message should be visible
    await expect(page.getByText('seed-249', { exact: true })).toBeVisible({ timeout: 15_000 });
    // Oldest message should not be in the initial page (limit 200)
    await expect(page.getByText('seed-0', { exact: true })).toHaveCount(0);

    const list = page.locator('div.h-full.overflow-y-auto').first();

    // Scroll to top repeatedly until the oldest message renders.
    //
    // One scroll is not enough with a virtualized list: reaching the top fetches
    // the older page, and the list then anchors to the message that was on top so
    // the reader keeps their place, which leaves seed-0 fifty rows further up and
    // unmounted. A reader scrolls again to get there; so does this. (Before
    // virtualization every loaded row was in the DOM, so a single scroll made
    // seed-0 findable regardless of where the viewport actually was.)
    await expect(async () => {
      await list.evaluate((el) => {
        el.scrollTop = 0;
      });
      await expect(page.getByText('seed-0')).toBeVisible({ timeout: 1_000 });
    }).toPass({ timeout: 20_000 });

    // Spot-check ordering: seed-249 appears above seed-200; seed-50 above seed-10
    // Fetch from API to validate ordering and dedup
    const texts = await page.evaluate(async (key) => {
      const res = await fetch(`/api/messages?type=CHAN&conversation_key=${key}&limit=300`);
      const data = await res.json();
      return data.map((m: any) => m.text);
    }, seeded.key);

    expect(texts.length).toBeGreaterThanOrEqual(250);
    expect(texts[0]).toContain('seed-249');
    expect(texts[texts.length - 1]).toContain('seed-0');
    expect(new Set(texts).size).toBe(texts.length);
  });
});
