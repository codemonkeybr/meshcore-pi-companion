# Upstream Sync Plan

**Branch:** `feat/upstream-sync`
**Upstream:** `jkingsman/Remote-Terminal-for-MeshCore:main`
**Fork point:** `9421c10e` (2026-03-09)
**Status at plan creation:** Merge initiated (`git merge upstream/main --no-commit --no-ff`), 139 conflict files, 198 new upstream files auto-staged, 107 auto-merged clean.

## Context

We are 132 commits ahead (SPI/Pi backend, Pi installer, sync-channels, config fixes) and 619 commits behind upstream. Primary rule: **SPI and Pi functionality is our top priority — never break it when resolving conflicts.**

Our exclusive files (no upstream conflict, just keep):
- `app/backends/` — SpiBackend, ClientBackend, adapters
- `app/radio_backend.py` — RadioBackend ABC
- `app/spi_config_file.py`, `app/spi_identity.py`
- `app/setup_cli.py`
- `app/routers/setup.py`
- `docs/PI_DEPLOYMENT.md`
- `install_remoteterm_pi.sh`, `scripts/manage_remoterm.sh`
- `tests/test_manage_remoterm.py`

## Key Structural Changes in Upstream

| Change | Upstream action | Our resolution |
|--------|----------------|---------------|
| `app/migrations.py` → `app/migrations/` dir | Deleted file, added 62 modules + `__init__.py` | Accept upstream dir, port our SPI migrations as new modules |
| `app/dependencies.py` | Deleted (inlined per router) | Accept deletion, check each router that imported it |
| `scripts/all_quality.sh` | Moved to `scripts/quality/all_quality.sh` | Accept move, keep our Pi scripts in `scripts/` |
| `scripts/publish.sh` | Moved to `scripts/build/publish.sh` | Accept move |
| `scripts/` flat → `scripts/{quality,build,setup}/` | Reorganized | Accept new layout |
| `frontend/src/messageCache.ts` | Deleted (integrated elsewhere) | Accept deletion |
| `remoteterm.service` | Deleted (moved to `scripts/setup/install_service.sh`) | Keep our version (Pi needs it) |
| `Dockerfile` | We deleted it, upstream still has it | Keep upstream's Dockerfile (needed for publish pipeline) |
| New subsystems | Push notifications, HA MQTT, telemetry charts, room server, trace pane, security warning, raw packet stats, favicon badge | Accept all (no Pi conflict) |

## Resolution Rules

1. **`UU` (both modified):** Take upstream version as base, graft our SPI-specific additions back in.
2. **`UD` (we modified, upstream deleted):** Investigate why upstream deleted. If renamed/moved → accept deletion + update callers. If truly removed → keep ours unless it conflicts with SPI.
3. **`DU` (we deleted, upstream modified):** Restore upstream's version (e.g. Dockerfile).
4. **`AA` (both added different content):** Merge both sides, preferring upstream for shared logic, keeping our SPI additions.
5. Never remove: SPI hooks in `app/radio.py`, `app/radio_sync.py`, `app/main.py`, `app/event_handlers.py`.

---

## Phase 1 — Infrastructure & Config

- [x] `.gitignore` — take upstream, keep our Pi-specific ignores
- [x] `.github/workflows/all-quality.yml` — take upstream version (AA conflict)
- [x] `pyproject.toml` — take upstream deps, keep our SPI deps (`pymc_core`, `RPi.GPIO`, etc.)
- [x] `Dockerfile` — restore upstream's version (we deleted it, they kept it — DU)
- [x] `remoteterm.service` — keep our version (UD: upstream deleted, we have Pi systemd unit)
- [x] `scripts/all_quality.sh` + `scripts/publish.sh` — accept upstream deletion (files moved to `scripts/quality/` and `scripts/build/`)

## Phase 2 — Migrations

- [x] `app/migrations.py` — accept upstream deletion (UD); upstream's `app/migrations/` dir is already auto-staged
- [x] Audit our migrations.py for any SPI-specific migrations not present in upstream's 62 modules → port as `_063_*` if needed
- [x] `app/database.py` — already auto-merged (M); verify migration runner import path updated

## Phase 3 — Backend Core

- [x] `app/config.py` — take upstream + keep SPI env vars (`MESHCORE_SPI_*`)
- [x] `app/main.py` — take upstream + re-graft SPI router (`setup.py`) registration and SPI backend imports
- [x] `app/radio.py` — take upstream + preserve `SpiBackend` branch in `RadioManager`
- [x] `app/models.py` — take upstream (auto-merged M; verify)
- [x] `app/dependencies.py` — accept deletion (UD); update any of our files that import from it

## Phase 4 — Backend Services & Packet Pipeline

- [x] `app/event_handlers.py` — take upstream + ensure SPI event adapter hooks not broken
- [x] `app/events.py` — AA conflict; merge both sides
- [x] `app/packet_processor.py` — take upstream
- [x] `app/decoder.py` — take upstream
- [x] `app/websocket.py` — take upstream
- [x] `app/keystore.py` — already auto-merged (M; verify)
- [x] `app/radio_sync.py` — take upstream + preserve SPI periodic task differences
- [x] `app/frontend_static.py` — take upstream (new multi-frontend static route function)
- [x] `app/services/messages.py` — take upstream
- [x] `app/services/message_send.py` — take upstream + verify SPI send path
- [x] `app/services/dm_ack_tracker.py` — take upstream
- [x] `app/services/radio_lifecycle.py` — take upstream + preserve SPI lifecycle hooks
- [x] `app/services/radio_commands.py` — take upstream + preserve SPI command paths
- [x] `app/services/radio_runtime.py` — take upstream

## Phase 5 — Backend Routers

- [x] `app/routers/health.py` — take upstream
- [x] `app/routers/radio.py` — take upstream
- [x] `app/routers/contacts.py` — take upstream
- [x] `app/routers/channels.py` — take upstream
- [x] `app/routers/messages.py` — take upstream
- [x] `app/routers/debug.py` — take upstream
- [x] `app/routers/settings.py` — take upstream
- [x] `app/routers/fanout.py` — take upstream
- [x] `app/routers/repeaters.py` — take upstream
- [x] `app/routers/read_state.py` — take upstream (auto-merged? verify)
- [x] `app/routers/ws.py` — take upstream

## Phase 6 — Fanout Subsystem

- [x] `app/fanout/AGENTS_fanout.md` — merge both
- [x] `app/fanout/community_mqtt.py` — take upstream
- [x] `app/fanout/mqtt_base.py` — take upstream
- [x] `app/fanout/sqs.py` — AA conflict; merge both

## Phase 7 — Frontend Types & Core

- [x] `frontend/package.json` — take upstream deps; keep any Pi-specific additions
- [x] `frontend/src/types.ts` — take upstream (auto-merged M; verify)
- [x] `frontend/src/wsEvents.ts` — take upstream
- [x] `frontend/src/api.ts` — take upstream + keep our `syncChannels()` addition
- [x] `frontend/src/App.tsx` — take upstream + keep our `handleSyncChannels` + `onSyncChannels` prop
- [x] `frontend/src/useWebSocket.ts` — take upstream (auto-merged M; verify)
- [x] `frontend/src/messageCache.ts` — accept upstream deletion (UD)
- [x] `frontend/src/themes.css` — take upstream
- [x] `frontend/src/utils/theme.ts` — take upstream
- [x] `frontend/src/utils/visualizerUtils.ts` — take upstream
- [x] `frontend/src/messageCache.ts` — accept deletion

## Phase 8 — Frontend Components

- [x] `frontend/src/components/AppShell.tsx` — take upstream
- [x] `frontend/src/components/ConversationPane.tsx` — take upstream
- [x] `frontend/src/components/Sidebar.tsx` — take upstream
- [x] `frontend/src/components/StatusBar.tsx` — take upstream
- [x] `frontend/src/components/ChatHeader.tsx` — take upstream
- [x] `frontend/src/components/MessageList.tsx` — take upstream
- [x] `frontend/src/components/MessageInput.tsx` — take upstream (auto-merged M; verify)
- [x] `frontend/src/components/SearchView.tsx` — take upstream
- [x] `frontend/src/components/SettingsModal.tsx` — take upstream + keep sync-channels section wiring
- [x] `frontend/src/components/NewMessageModal.tsx` — take upstream
- [x] `frontend/src/components/MapView.tsx` — take upstream
- [x] `frontend/src/components/ChannelInfoPane.tsx` — take upstream
- [x] `frontend/src/components/ContactInfoPane.tsx` — take upstream
- [x] `frontend/src/components/RepeaterDashboard.tsx` — take upstream
- [x] `frontend/src/components/repeater/RepeaterNeighborsPane.tsx` — take upstream
- [x] `frontend/src/components/repeater/RepeaterNodeInfoPane.tsx` — take upstream
- [x] `frontend/src/components/repeater/repeaterPaneShared.tsx` — take upstream
- [x] `frontend/src/components/visualizer/VisualizerControls.tsx` — take upstream

## Phase 9 — Frontend Settings Components

- [x] `frontend/src/components/settings/settingsConstants.ts` — take upstream
- [x] `frontend/src/components/settings/SettingsRadioSection.tsx` — take upstream + keep sync-channels button
- [x] `frontend/src/components/settings/SettingsFanoutSection.tsx` — take upstream
- [x] `frontend/src/components/settings/SettingsLocalSection.tsx` — take upstream
- [x] `frontend/src/components/settings/SettingsAboutSection.tsx` — take upstream

## Phase 10 — Frontend Hooks

- [x] `frontend/src/hooks/index.ts` — take upstream
- [x] `frontend/src/hooks/useAppShell.ts` — take upstream
- [x] `frontend/src/hooks/useConversationActions.ts` — take upstream + keep sync-channels action
- [x] `frontend/src/hooks/useConversationMessages.ts` — take upstream
- [x] `frontend/src/hooks/useRadioControl.ts` — take upstream
- [x] `frontend/src/hooks/useRealtimeAppState.ts` — take upstream
- [x] `frontend/src/hooks/useRepeaterDashboard.ts` — take upstream
- [x] `frontend/src/hooks/useUnreadCounts.ts` — take upstream
- [x] `frontend/src/hooks/useBrowserNotifications.ts` — AA conflict; take upstream

## Phase 11 — AGENTS.md & Docs

- [x] `AGENTS.md` — merge both; keep SPI architecture diagram, update with upstream additions
- [x] `app/AGENTS.md` — merge both; keep SPI backend map entries
- [x] `frontend/AGENTS.md` — merge both
- [x] `app/fanout/AGENTS_fanout.md` — merge both
- [x] `CHANGELOG.md` — take upstream; our version-specific entries are in git history
- [x] `README.md` — take upstream; our Pi-specific docs are in `docs/PI_DEPLOYMENT.md`

## Phase 12 — Tests

- [x] `tests/test_api.py` — take upstream + keep our SPI-related test cases
- [x] `tests/test_config.py` — take upstream + keep SPI env var tests
- [x] `tests/test_radio.py` — take upstream + keep SPI backend tests
- [x] `tests/test_radio_lifecycle_service.py` — AA conflict; merge both
- [x] `tests/test_radio_runtime_service.py` — AA conflict; merge both
- [x] `tests/test_radio_operation.py` — take upstream
- [x] `tests/test_radio_router.py` — take upstream
- [x] `tests/test_radio_sync.py` — take upstream
- [x] `tests/test_radio_commands_service.py` — take upstream
- [x] `tests/test_event_handlers.py` — take upstream
- [x] `tests/test_decoder.py` — take upstream
- [x] `tests/test_repository.py` — take upstream
- [x] `tests/test_send_messages.py` — take upstream
- [x] `tests/test_ack_tracking_wiring.py` — take upstream
- [x] `tests/test_channels_router.py` — take upstream
- [x] `tests/test_contacts_router.py` — take upstream
- [x] `tests/test_settings_router.py` — take upstream
- [x] `tests/test_repeater_routes.py` — take upstream
- [x] `tests/test_community_mqtt.py` — take upstream
- [x] `tests/test_fanout.py` — take upstream
- [x] `tests/test_block_lists.py` — take upstream
- [x] `tests/test_http_quality.py` — take upstream
- [x] `tests/test_frontend_static.py` — take upstream
- [x] Frontend test files (all `frontend/src/test/*.test.*`) — take upstream

## Phase 13 — Lock File & Final Build

- [x] `uv.lock` — regenerate with `uv lock` after `pyproject.toml` is resolved
- [x] `frontend/package-lock.json` — take upstream (regenerated by `npm ci`)
- [x] Run `./scripts/quality/all_quality.sh` — all checks must pass green
- [x] Fix any type/lint/test failures introduced by the merge

## Phase 14 — SPI Smoke Check

- [x] Confirm `app/radio.py` still has `SpiBackend` branch
- [x] Confirm `app/main.py` still registers `app.routers.setup` router
- [x] Confirm `app/routers/setup.py` is untouched
- [x] Confirm `app/backends/` directory is untouched
- [x] Confirm `install_remoteterm_pi.sh` is untouched
- [x] Confirm `scripts/manage_remoterm.sh` is untouched

---

## Resume Instructions

1. `cd /home/tesso/dev/piMCCompanion_env/remote-terminal-fork`
2. `git status` — merge is already in progress on `feat/upstream-sync`
3. Check this file for last completed phase
4. Continue from the first unchecked item
5. After all phases done: `git add -A && git merge --continue` (user commits)

## Notes

- The merge was initiated with `git merge upstream/main --no-commit --no-ff`
- Do NOT run `git merge --abort` — work in progress
- For `UD` conflicts (we modified, upstream deleted): use `git rm <file>` to accept deletion
- For `DU` conflicts (we deleted, upstream modified): use `git add <file>` to accept upstream's version
- For `UU` conflicts: edit the file to remove conflict markers, then `git add <file>`
- All `git add` staging is fine; the human does the final `git commit`
