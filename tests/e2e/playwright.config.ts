import { defineConfig } from '@playwright/test';
import path from 'path';

const projectRoot = path.resolve(__dirname, '..', '..');
const tmpDir = path.resolve(__dirname, '.tmp');

// When E2E_BASE_URL is set, tests run against an existing server (e.g. the Pi
// test rig at http://192.168.1.188:8000) and no local server is started.
const externalBaseURL = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: './specs',
  globalSetup: './global-setup.ts',

  // Radio operations are slow — generous timeouts
  timeout: 60_000,
  expect: { timeout: 15_000 },

  // Give hardware-backed flows one automatic retry before marking the test failed.
  retries: 1,

  // Run tests serially — single radio means no parallelism
  fullyParallel: false,
  workers: 1,

  reporter: [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: externalBaseURL ?? 'http://localhost:8001',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    // Dismiss the security warning modal that blocks interaction on fresh browser contexts
    storageState: {
      cookies: [],
      origins: [
        {
          origin: externalBaseURL ?? 'http://localhost:8001',
          localStorage: [{ name: 'meshcore_security_warning_acknowledged', value: 'true' }],
        },
      ],
    },
  },

  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' },
    },
  ],

  // Skip local server when targeting an external URL (e.g. the Pi test rig).
  ...(externalBaseURL
    ? {}
    : {
        webServer: {
          command: `bash -c '
      echo "[e2e] $(date +%T.%3N) Starting webServer command..."
      if [ ! -d frontend/dist ]; then
        echo "[e2e] $(date +%T.%3N) frontend/dist missing — running npm ci + build"
        cd frontend && npm ci && npm run build
        echo "[e2e] $(date +%T.%3N) Frontend build complete"
      else
        echo "[e2e] $(date +%T.%3N) frontend/dist exists — skipping build"
      fi
      echo "[e2e] $(date +%T.%3N) Launching uvicorn..."
      uv run uvicorn app.main:app --host 127.0.0.1 --port 8001
    '`,
          cwd: projectRoot,
          port: 8001,
          reuseExistingServer: false,
          timeout: 180_000,
          env: {
            MESHCORE_DATABASE_PATH: path.join(tmpDir, 'e2e-test.db'),
            // Pass through the serial port from the environment
            ...(process.env.MESHCORE_SERIAL_PORT
              ? { MESHCORE_SERIAL_PORT: process.env.MESHCORE_SERIAL_PORT }
              : {}),
          },
        },
      }),
});
