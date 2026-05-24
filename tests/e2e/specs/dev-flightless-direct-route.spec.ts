import { test, expect } from '@playwright/test';
import {
  createContact,
  deleteContact,
  getContactByKey,
  getMessages,
  setContactRoutingOverride,
} from '../helpers/api';
import {
  E2E_PARTNER_RADIO_PUBKEY,
  E2E_PARTNER_RADIO_NAME,
} from '../helpers/env';

const PARTNER_RADIO_NOTICE =
  `Partner-radio hardware test. Requires a nearby node "${E2E_PARTNER_RADIO_NAME}" ` +
  `(${E2E_PARTNER_RADIO_PUBKEY.slice(0, 12)}...) that will ACK DMs from this radio. ` +
  `Set E2E_USE_PARTNER_RADIO_FOR_DM_ACK_TEST=1 to run, and override ` +
  `E2E_PARTNER_RADIO_PUBKEY / E2E_PARTNER_RADIO_NAME to match your hardware.`;

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

test.describe('Partner-radio direct-route learning via DM ACK', () => {
  test('zero-hop adverts then DM ACK learns a direct route', { tag: '@partner-radio' }, async ({
    page,
  }, testInfo) => {
    testInfo.annotations.push({ type: 'notice', description: PARTNER_RADIO_NOTICE });
    test.setTimeout(180_000);

    try {
      await deleteContact(E2E_PARTNER_RADIO_PUBKEY);
    } catch {
      // Best-effort reset; the contact may not exist yet in the temp E2E DB.
    }

    await createContact(E2E_PARTNER_RADIO_PUBKEY, E2E_PARTNER_RADIO_NAME);
    await setContactRoutingOverride(E2E_PARTNER_RADIO_PUBKEY, '');

    await expect
      .poll(
        async () => {
          const contact = await getContactByKey(E2E_PARTNER_RADIO_PUBKEY);
          return contact?.direct_path_len ?? null;
        },
        {
          timeout: 10_000,
          message: 'Waiting for recreated partner contact to start in flood mode',
        }
      )
      .toBe(-1);

    await page.goto('/#settings/radio');
    await expect(page.getByRole('status', { name: 'Radio OK' })).toBeVisible();

    const zeroHopButton = page.getByRole('button', { name: 'Send Zero-Hop Advertisement' });
    await expect(zeroHopButton).toBeVisible();

    await zeroHopButton.click();
    await expect(page.getByText('Zero-hop advertisement sent')).toBeVisible({ timeout: 15_000 });

    await page.waitForTimeout(5_000);

    await zeroHopButton.click();
    await expect(page.getByText('Zero-hop advertisement sent')).toBeVisible({ timeout: 15_000 });

    await page.getByRole('button', { name: /Back to Chat/i }).click();
    await expect(page.getByRole('button', { name: /Back to Chat/i })).toBeHidden({
      timeout: 15_000,
    });

    const searchInput = page.getByLabel('Search conversations');
    await searchInput.fill(E2E_PARTNER_RADIO_PUBKEY.slice(0, 12));
    await expect(page.getByText(E2E_PARTNER_RADIO_NAME, { exact: true })).toBeVisible({
      timeout: 15_000,
    });
    await page.getByText(E2E_PARTNER_RADIO_NAME, { exact: true }).click();
    await expect
      .poll(() => page.url(), {
        timeout: 15_000,
        message: 'Waiting for partner contact conversation route to load',
      })
      .toContain(`#contact/${encodeURIComponent(E2E_PARTNER_RADIO_PUBKEY)}`);
    await expect(
      page.getByPlaceholder(new RegExp(`message\\s+${escapeRegex(E2E_PARTNER_RADIO_NAME)}`, 'i'))
    ).toBeVisible({ timeout: 15_000 });

    const text = `dm-ack-route-test-${Date.now()}`;
    const input = page.getByPlaceholder(/message/i);
    await input.fill(text);
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect(page.getByText(text)).toBeVisible({ timeout: 15_000 });

    await expect
      .poll(
        async () => {
          const messages = await getMessages({
            type: 'PRIV',
            conversation_key: E2E_PARTNER_RADIO_PUBKEY,
            limit: 25,
          });
          const match = messages.find((message) => message.outgoing && message.text === text);
          return match?.acked ?? 0;
        },
        {
          timeout: 90_000,
          message: 'Waiting for partner radio DM ACK',
        }
      )
      .toBeGreaterThan(0);

    await expect
      .poll(
        async () => {
          const contact = await getContactByKey(E2E_PARTNER_RADIO_PUBKEY);
          return contact?.direct_path_len ?? null;
        },
        {
          timeout: 90_000,
          message: 'Waiting for partner radio route to update from flood to direct',
        }
      )
      .toBe(0);

    const learnedContact = await getContactByKey(E2E_PARTNER_RADIO_PUBKEY);
    expect(learnedContact?.direct_path ?? '').toBe('');

    await page.locator('[title="View contact info"]').click();
    await expect(page.getByLabel('Contact Info')).toBeVisible({ timeout: 15_000 });
  });
});
