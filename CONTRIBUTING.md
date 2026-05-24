# Contributing

## Guiding Principles

- In all your interactions with developers, maintainers, and users, be kind.
- Prefer small, comprehensible changes over large sweeping ones. Individual commits should be meaningful atomic chunks of work. Pull requests with many, many commits instead of a phased approach may be declined.
- Pull requests must be fully understood and explicitly endorsed by a human before merge. AI assistance is great, and this repo is optimized for it, but we keep quality by keeping our agents on track to write clear code, useful (not useless) tests, good architecture, and big-picture thinking.
- No pull request should introduce new failing lint, typecheck, test, or build results.
- Every pull request should have an associated issue or discussion thread; a brand new feature appearing first in a PR is an antipattern.
- No truly automated radio traffic. Bot replies are already the practical edge of what this project wants to automate; any kind of traffic that would be intervalized or automated is not what this project is about.
- No ingestion from the internet onto the mesh. This project is a radio client, not a bridge for outside traffic to enter the network. The mesh is strong because it is a radio mesh, not the internet with some weird wireless links.

## Local Development

### Backend

```bash
uv sync
uv run uvicorn app.main:app --reload
```

With an explicit serial port:

```bash
MESHCORE_SERIAL_PORT=/dev/ttyUSB0 uv run uvicorn app.main:app --reload
```

On Windows (PowerShell):

```powershell
uv sync
$env:MESHCORE_SERIAL_PORT="COM8"
uv run uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Run both the backend and `npm run dev` for hot-reloading frontend development.

## Quality Checks

Run the full quality suite before proposing or handing off code changes:

```bash
./scripts/quality/all_quality.sh
```

That runs linting, formatting, type checking, tests, and builds for both backend and frontend.

If you need targeted commands while iterating:

```bash
# backend
uv run ruff check app/ tests/ --fix
uv run ruff format app/ tests/
uv run pyright app/
PYTHONPATH=. uv run pytest tests/ -v

# frontend
cd frontend
npm run lint:fix
npm run format
npm run test:run
npm run build
```

## Quality + Publishing Scripts

<details>
<summary>scripts/quality/</summary>

| Script | Purpose |
|--------|---------|
| `all_quality.sh` | Repo-standard gate: autofix (ruff, eslint, prettier), then pyright, pytest, vitest, and frontend build. Run before finishing any code change. |
| `extended_quality.sh` | `all_quality.sh` plus e2e tests and Docker build matrix. Used for release validation. |
| `e2e.sh` | Thin wrapper that runs Playwright e2e tests from `tests/e2e/`. |
| `docker_ci.sh` | Builds the Docker image and runs a smoke test against it. |
| `test_aur_package.sh` | Builds the AUR package in an Arch container, then installs and boots it in a second container with port 8000 exposed (hang finish). |
| `run_aur_with_radio.sh` | Like `test_aur_package.sh` but passes through the host serial device for testing with a real radio (hang finish). |

</details>

<details>
<summary>scripts/build/</summary>

| Script | Purpose |
|--------|---------|
| `publish.sh` | Full release ceremony: quality gate, version bump, changelog, frontend build, Docker multi-arch push, GitHub release. |
| `release_common.sh` | Shared shell helpers (version validation, formatting) sourced by other build scripts. |
| `package_release_artifact.sh` | Builds the prebuilt-frontend release zip attached to GitHub releases. |
| `push_docker_multiarch.sh` | Builds and pushes multi-arch Docker images (amd64 + arm64). |
| `create_github_release.sh` | Creates a GitHub release with changelog notes and the release artifact. |
| `extract_release_notes.sh` | Extracts the latest version's notes from `CHANGELOG.md` for the release body. |
| `collect_licenses.sh` | Gathers third-party license attributions into `LICENSES.md`. |
| `print_frontend_licenses.cjs` | Helper that extracts frontend npm dependency licenses. |
| `dump_api_specs.py` | Dumps the OpenAPI spec from the running backend (developer utility). |

</details>

## E2E Testing

E2E tests exercise the full stack (backend + frontend + real radio hardware) via Playwright.

> [!WARNING]
> E2E tests are **not part of the normal development path** — most contributors will never need to run them. They exist to catch integration issues that unit tests can't and generally only need to be run by maintainers.

### Hardware requirements

- A MeshCore radio connected via serial (auto-detected, or set `MESHCORE_SERIAL_PORT`)
- The radio must be powered on and past its startup sequence before tests begin

### Running

```bash
cd tests/e2e
npm install
npx playwright install chromium  # first time only
npx playwright test              # headless
npx playwright test --headed     # watch it run
```

The test harness starts its own uvicorn instance on port 8001 with a fresh temporary database. Your development server (port 8000) is unaffected.

### Test tiers

**Most tests (22 of 28) are fully self-contained.** They seed their own data via API calls or direct DB writes and need only a connected radio. These cover messaging, pagination, search, favorites, settings, fanout integrations, historical decryption, and all UI-only views.

**Mesh-traffic tests (tagged `@mesh-traffic`)** wait up to 3 minutes for an incoming message from another node on the network. If no traffic arrives, they fail with an advisory that the failure may be RF conditions, not a bug. These are: `incoming-message` and `packet-feed` (second test only).

**The partner-radio DM ACK test (tagged `@partner-radio`)** validates direct-route learning by sending a DM and waiting for an ACK. It requires a second radio in range that has your test radio in its contacts. Configure the partner node's public key and name via `E2E_PARTNER_RADIO_PUBKEY` and `E2E_PARTNER_RADIO_NAME`.

### Making mesh-traffic tests reliable: the echo bot

The most practical way to guarantee incoming traffic is to run an **echo bot on a second radio** monitoring a known channel. When the test suite starts a `@mesh-traffic` test, it sends a trigger message to that channel. If a bot on another radio is listening, it replies — generating the incoming RF packet the test needs within seconds instead of waiting for organic mesh traffic.

The test suite sends `!echo please give incoming message` to the echo channel (default `#flightless`) at the start of each `@mesh-traffic` test. The trigger message is configurable via `E2E_ECHO_TRIGGER_MESSAGE`.

Setup:
1. Set up a second MeshCore radio within RF range of your test radio
2. Run a RemoteTerm instance on the second radio
3. Configure a bot on the second radio that monitors the echo channel and replies when it sees the trigger. Example bot code:
   ```python
   def bot(sender_name, sender_key, message_text, is_dm,
           channel_key, channel_name, sender_timestamp, path):
       if "!echo" in message_text.lower():
           return f"[ECHO] {message_text}"
       return None
   ```
4. The test suite calls `nudgeEchoBot()` automatically — no manual intervention needed

Without the echo bot, `@mesh-traffic` tests rely on organic traffic from other nodes. In a quiet RF environment they will time out.

### Environment variables

All E2E environment configuration is centralized in `tests/e2e/helpers/env.ts` with defaults that work for the maintainer's test rig. Override via environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `MESHCORE_SERIAL_PORT` | auto-detect | Serial port for the test radio |
| `E2E_ECHO_CHANNEL` | `#flightless` | Channel the echo bot monitors for traffic generation |
| `E2E_ECHO_TRIGGER_MESSAGE` | `!echo please give incoming message` | Message sent to nudge the echo bot |
| `E2E_PARTNER_RADIO_PUBKEY` | *(maintainer's test node)* | 64-char hex public key of a node that will ACK DMs from your radio |
| `E2E_PARTNER_RADIO_NAME` | *(maintainer's test node)* | Display name of that node (used in UI assertions) |

Example for a contributor with their own two-radio setup:

```bash
E2E_ECHO_CHANNEL="#mytest" \
E2E_PARTNER_RADIO_PUBKEY="abcd1234...full64charhexkey..." \
E2E_PARTNER_RADIO_NAME="MyTestNode" \
npx playwright test
```

## Pull Request Expectations

- Keep scope tight.
- Explain why the change is needed.
- Link the issue or discussion where the behavior was agreed on.
- Call out any follow-up work left intentionally undone.
- Do not treat code review as the place where the app's direction is first introduced or debated

## Notes For Agent-Assisted Work

Before making non-trivial changes, read:

- `./AGENTS.md`
- `./app/AGENTS.md`
- `./frontend/AGENTS.md`

Read these only when working in those areas:

- `./app/fanout/AGENTS_fanout.md`
- `./frontend/src/components/visualizer/AGENTS_packet_visualizer.md`

- Agent output is welcome, but human review is mandatory.
- Agents should start with the AGENTS files above before making architectural changes.
- If a change touches advanced areas like fanout or the visualizer, read the area-specific AGENTS file before editing.
