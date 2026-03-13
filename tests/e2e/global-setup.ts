import type { FullConfig } from '@playwright/test';

const BASE_URL = 'http://localhost:8001';
const MAX_RETRIES = 10;
const RETRY_DELAY_MS = 2000;

interface HealthStatus {
  radio_connected: boolean;
  radio_initializing: boolean;
  connection_info: string | null;
}

export default async function globalSetup(_config: FullConfig) {
  // Wait for the backend to be fully ready and radio setup complete
  let lastError: Error | null = null;

  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    try {
      const res = await fetch(`${BASE_URL}/api/health`);
      if (!res.ok) {
        throw new Error(`Health check returned ${res.status}`);
      }
      const health = (await res.json()) as HealthStatus;

      if (!health.radio_connected) {
        throw new Error(
          'Radio not connected — E2E tests require hardware. ' +
            'Set MESHCORE_SERIAL_PORT if auto-detection fails.'
        );
      }
      if (health.radio_initializing) {
        throw new Error('Radio connected but still initializing');
      }

      console.log(`Radio ready on ${health.connection_info}`);
      return;
    } catch (err) {
      lastError = err instanceof Error ? err : new Error(String(err));
      if (attempt < MAX_RETRIES) {
        console.log(`Waiting for backend (attempt ${attempt}/${MAX_RETRIES})...`);
        await new Promise((r) => setTimeout(r, RETRY_DELAY_MS));
      }
    }
  }

  throw new Error(`Backend not ready after ${MAX_RETRIES} attempts: ${lastError?.message}`);
}
