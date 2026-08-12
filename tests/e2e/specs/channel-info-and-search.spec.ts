import { test, expect } from '@playwright/test';
import { seedChannelMessages } from '../helpers/seed';
import { ensureFlightlessChannel } from '../helpers/api';

const CHANNEL_NAME = '#flightless';
const SEED_COUNT = 30;

/**
 * Seed #flightless with unique, searchable messages.
 * Returns the channel key and a unique search token.
 */
function seedFlightlessMessages() {
  const token = `e2e-search-${Date.now()}`;
  const seeded = seedChannelMessages({
    channelName: CHANNEL_NAME,
    count: SEED_COUNT,
    // Use unique text so search can find them reliably
    // Note: seedChannelMessages uses "seed-N" as text by default,
    // but we need our unique token in there. We'll search for "seed-" instead
    // since that's what the seed helper generates.
    outgoingEvery: 5,
    includePaths: true,
  });
  return { key: seeded.key, token };
}

test.describe('Channel info pane', () => {
  let channelKey: string;

  test.beforeAll(async () => {
    await ensureFlightlessChannel();
    const seeded = seedFlightlessMessages();
    channelKey = seeded.key;
  });

  test('opens channel info pane and shows message activity', async ({ page }) => {
    await page.goto(`/#channel/${channelKey}/flightless`);
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    // Wait for messages to load. Assert on the newest seeded message, not the
    // oldest: the list is virtualized, so it opens at the bottom and only the
    // visible window is in the DOM — seed-0 is 30 rows up and legitimately absent.
    await expect(page.getByText(`seed-${SEED_COUNT - 1}`)).toBeVisible({ timeout: 15_000 });

    // Click the channel name in the header to open info pane
    const headerTitle = page.locator('h2').filter({ hasText: '#flightless' });
    await headerTitle.click();

    // Channel info pane should open as a sheet
    const infoPane = page.getByRole('dialog');
    await expect(infoPane).toBeVisible({ timeout: 10_000 });

    // Should show channel name
    await expect(infoPane.getByText('#flightless')).toBeVisible();

    // Should show channel key
    await expect(infoPane.getByText(channelKey.toLowerCase())).toBeVisible();

    // Should show "Hashtag" badge
    await expect(infoPane.getByText('Hashtag')).toBeVisible();

    // Should show "Message Activity" section with counts
    await expect(infoPane.getByText('Message Activity')).toBeVisible();
    await expect(infoPane.getByText('All Time')).toBeVisible();

    // All Time count should be non-zero (our seeded messages)
    // InfoItem renders: <span>All Time</span><p>VALUE</p> — use CSS sibling selector
    const allTimeValue = infoPane.locator('span:text-is("All Time") + p');
    const count = await allTimeValue.textContent();
    expect(Number(count?.replace(/,/g, ''))).toBeGreaterThanOrEqual(SEED_COUNT);

    // Should show "First Message" section
    await expect(infoPane.getByText('First Message')).toBeVisible();
  });
});

test.describe('Message search and jump-to-message', () => {
  let channelKey: string;

  test.beforeAll(async () => {
    await ensureFlightlessChannel();
    const seeded = seedFlightlessMessages();
    channelKey = seeded.key;
  });

  test('search finds seeded messages', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    // Open search view via sidebar
    await page.getByText('Message Search').click();

    // Should show search input
    const searchInput = page.getByPlaceholder('Search all messages...');
    await expect(searchInput).toBeVisible();

    // Search for seeded messages (seed helper creates "seed-N" text)
    await searchInput.fill('seed-1');

    // Wait for search results to appear
    await expect(page.getByText('seed-1', { exact: false }).first()).toBeVisible({
      timeout: 10_000,
    });

    // Results should show the channel name
    await expect(page.getByText('#flightless').first()).toBeVisible();

    // Results should show "Channel" badge
    await expect(page.getByText('Channel').first()).toBeVisible();
  });

  test('clicking a search result jumps to the message in conversation', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    // Open search
    await page.getByText('Message Search').click();

    const searchInput = page.getByPlaceholder('Search all messages...');
    await searchInput.fill('seed-15');

    // Wait for results
    const result = page.getByText('seed-15', { exact: false }).first();
    await expect(result).toBeVisible({ timeout: 10_000 });

    // Click the search result to jump to it
    await result.click();

    // Should navigate to the #flightless conversation
    await expect(page.getByPlaceholder(/message #flightless/i)).toBeVisible({ timeout: 15_000 });

    // The target message should be visible in the conversation (not search results)
    // Scope to [data-message-id] to avoid matching leftover search <mark> elements
    const messageContainer = page.locator('[data-message-id]').filter({ hasText: 'seed-15' });
    await expect(messageContainer.first()).toBeVisible({ timeout: 10_000 });
  });

  test('search returns no results for nonsense query', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    await page.getByText('Message Search').click();

    const searchInput = page.getByPlaceholder('Search all messages...');
    await searchInput.fill('zzz-nonexistent-query-zzz');

    await expect(page.getByText(/No messages found/)).toBeVisible({ timeout: 10_000 });
  });
});
