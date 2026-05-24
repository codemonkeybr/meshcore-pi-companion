import type { Locator, Page } from '@playwright/test';
import http from 'http';

function escapeRegex(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function createCaptureServer(urlFactory: (port: number) => string) {
  const requests: { body: string; headers: http.IncomingHttpHeaders }[] = [];
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (chunk) => (body += chunk));
    req.on('end', () => {
      requests.push({ body, headers: req.headers });
      res.writeHead(200);
      res.end('ok');
    });
  });

  return {
    requests,
    server,
    async listen(): Promise<string> {
      return await new Promise((resolve) => {
        server.listen(0, '127.0.0.1', () => {
          const addr = server.address();
          if (typeof addr === 'object' && addr) {
            resolve(urlFactory(addr.port));
          }
        });
      });
    },
    close(): void {
      server.close();
    },
  };
}

export async function openFanoutSettings(page: Page): Promise<void> {
  await page.goto('/');
  await page.getByText('Settings').click();
  await page.getByRole('button', { name: /MQTT.*Automation/ }).click();
}

export async function startIntegrationDraft(page: Page, integrationName: string): Promise<void> {
  await page.getByRole('button', { name: 'Add Integration' }).click();
  const dialog = page.getByRole('dialog', { name: 'Create Integration' });
  await dialog
    .getByRole('button', { name: new RegExp(`^${escapeRegex(integrationName)}(?:\\s|$)`) })
    .click();
  await dialog.getByRole('button', { name: 'Create' }).click();
}

export function fanoutHeader(page: Page, name: string): Locator {
  const nameButton = page.getByRole('button', { name, exact: true });
  return page
    .locator('div')
    .filter({ has: nameButton })
    .filter({ has: page.getByRole('button', { name: 'Edit' }) })
    .last();
}
