# Frontend AGENTS.md

This document is the frontend working guide for agents and developers.
Keep it aligned with `frontend/src` source code.

## Stack

- React 18 + TypeScript
- Vite
- Vitest + Testing Library
- shadcn/ui primitives
- Tailwind utility classes + local CSS (`index.css`, `styles.css`)
- Sonner (toasts)
- Leaflet / react-leaflet (map)
- `meshcore-hashtag-cracker` + `nosleep.js` (channel cracker)

## Frontend Map

```text
frontend/src/
├── main.tsx                # React entry point (StrictMode, root render)
├── App.tsx                 # App shell and orchestration
├── api.ts                  # Typed REST client
├── types.ts                # Shared TS contracts
├── useWebSocket.ts         # WS lifecycle + event dispatch
├── messageCache.ts         # Conversation-scoped cache
├── prefetch.ts             # Consumes prefetched API promises started in index.html
├── index.css               # Global styles/utilities
├── styles.css              # Additional global app styles
├── lib/
│   └── utils.ts            # cn() — clsx + tailwind-merge helper
├── hooks/
│   ├── index.ts            # Central re-export of all hooks
│   ├── useConversationMessages.ts  # Fetch, pagination, dedup, ACK buffering
│   ├── useUnreadCounts.ts          # Unread counters, mentions, recent-sort timestamps
│   ├── useRepeaterDashboard.ts      # Repeater dashboard state (login, panes, console, retries)
│   ├── useRadioControl.ts          # Radio health/config state, reconnection
│   ├── useAppSettings.ts           # Settings, favorites, preferences migration
│   ├── useConversationRouter.ts    # URL hash → active conversation routing
│   └── useContactsAndChannels.ts   # Contact/channel loading, creation, deletion
├── utils/
│   ├── urlHash.ts              # Hash parsing and encoding
│   ├── conversationState.ts    # State keys, in-memory + localStorage helpers
│   ├── favorites.ts            # LocalStorage migration for favorites
│   ├── messageParser.ts        # Message text → rendered segments
│   ├── pathUtils.ts            # Distance/validation helpers for paths + map
│   ├── pubkey.ts               # getContactDisplayName (12-char prefix fallback)
│   ├── contactAvatar.ts        # Avatar color derivation from public key
│   ├── rawPacketIdentity.ts    # observation_id vs id dedup helpers
│   ├── visualizerUtils.ts      # 3D visualizer node types, colors, particles
│   ├── lastViewedConversation.ts   # localStorage for last-viewed conversation
│   ├── contactMerge.ts            # Merge WS contact updates into list
│   ├── localLabel.ts              # Local label (text + color) in localStorage
│   └── radioPresets.ts            # LoRa radio preset configurations
├── components/
│   ├── StatusBar.tsx
│   ├── Sidebar.tsx
│   ├── ChatHeader.tsx          # Conversation header (trace, favorite, delete)
│   ├── MessageList.tsx
│   ├── MessageInput.tsx
│   ├── NewMessageModal.tsx
│   ├── SettingsModal.tsx
│   ├── settingsConstants.ts    # Settings section ordering and labels
│   ├── RawPacketList.tsx
│   ├── MapView.tsx
│   ├── VisualizerView.tsx
│   ├── PacketVisualizer3D.tsx
│   ├── PathModal.tsx
│   ├── CrackerPanel.tsx
│   ├── BotCodeEditor.tsx
│   ├── ContactAvatar.tsx
│   ├── ContactInfoPane.tsx     # Contact detail sheet (stats, name history, paths)
│   ├── RepeaterDashboard.tsx   # Repeater pane-based dashboard (telemetry, neighbors, ACL, etc.)
│   ├── RepeaterLogin.tsx       # Repeater login form (password + guest)
│   ├── NeighborsMiniMap.tsx    # Leaflet mini-map for repeater neighbor locations
│   └── ui/                     # shadcn/ui primitives
├── types/
│   └── d3-force-3d.d.ts       # Type declarations for d3-force-3d
└── test/
    ├── setup.ts
    ├── fixtures/websocket_events.json
    ├── api.test.ts
    ├── appFavorites.test.tsx
    ├── appStartupHash.test.tsx
    ├── contactAvatar.test.ts
    ├── integration.test.ts
    ├── messageCache.test.ts
    ├── messageParser.test.ts
    ├── pathUtils.test.ts
    ├── radioPresets.test.ts
    ├── rawPacketIdentity.test.ts
    ├── repeaterDashboard.test.tsx
    ├── repeaterFormatters.test.ts
    ├── repeaterLogin.test.tsx
    ├── repeaterMessageParsing.test.ts
    ├── localLabel.test.ts
    ├── settingsModal.test.tsx
    ├── sidebar.test.tsx
    ├── unreadCounts.test.ts
    ├── urlHash.test.ts
    ├── useConversationMessages.test.ts
    ├── useConversationMessages.race.test.ts
    ├── useRepeaterDashboard.test.ts
    ├── useWebSocket.dispatch.test.ts
    └── useWebSocket.lifecycle.test.ts
```

## Architecture Notes

### State ownership

`App.tsx` orchestrates high-level state and delegates to hooks:
- `useRadioControl`: radio health/config state, reconnect/reboot polling
- `useAppSettings`: settings CRUD, favorites, preferences migration
- `useContactsAndChannels`: contact/channel lists, creation, deletion
- `useConversationRouter`: URL hash → active conversation routing
- `useConversationMessages`: fetch, pagination, dedup/update helpers
- `useUnreadCounts`: unread counters, mention tracking, recent-sort timestamps
- `useRepeaterDashboard`: repeater dashboard state (login, pane data/retries, console, actions)

### Initial load + realtime

- Initial data: REST fetches (`api.ts`) for config/settings/channels/contacts/unreads.
- WebSocket: realtime deltas/events.
- On WS connect, backend sends `health` only; contacts/channels still come from REST.

### New Message modal

`NewMessageModal` intentionally preserves form state (tab, inputs, checkboxes) when closed and reopened. The component instance persists across open/close cycles. This is by design so users don't lose in-progress input if they accidentally dismiss the dialog.

### Message behavior

- Outgoing sends are added to UI after the send API returns (not pre-send optimistic insertion), then persisted server-side.
- Backend also emits WS `message` for outgoing sends so other clients stay in sync.
- ACK/repeat updates arrive as `message_acked` events.
- Outgoing channel messages show a 30-second resend control; resend calls `POST /api/messages/channel/{message_id}/resend`.

### Visualizer behavior

- `VisualizerView.tsx` hosts `PacketVisualizer3D.tsx` (desktop split-pane and mobile tabs).
- `PacketVisualizer3D` uses persistent Three.js geometries for links/highlights/particles and updates typed-array buffers in-place per frame.
- Packet repeat aggregation keys prefer decoder `messageHash` (path-insensitive), with hash fallback for malformed packets.
- Raw packet events carry both:
  - `id`: backend storage row identity (payload-level dedup)
  - `observation_id`: realtime per-arrival identity (session fidelity)
- Packet feed/visualizer render keys and dedup logic should use `observation_id` (fallback to `id` only for older payloads).

## WebSocket (`useWebSocket.ts`)

- Auto reconnect (3s) with cleanup guard on unmount.
- Heartbeat ping every 30s.
- Event handlers: `health`, `message`, `contact`, `raw_packet`, `message_acked`, `error`, `success`, `pong` (ignored).
- For `raw_packet` events, use `observation_id` as event identity; `id` is a storage reference and may repeat.

## URL Hash Navigation (`utils/urlHash.ts`)

Supported routes:
- `#raw`
- `#map`
- `#map/focus/{pubkey_or_prefix}`
- `#visualizer`
- `#channel/{channelKey}`
- `#channel/{channelKey}/{label}`
- `#contact/{publicKey}`
- `#contact/{publicKey}/{label}`

Legacy name-based hashes are still accepted for compatibility.

## Conversation State Keys (`utils/conversationState.ts`)

`getStateKey(type, id)` produces:
- channels: `channel-{channelKey}`
- contacts: `contact-{publicKey}`

Use full contact public key here (not 12-char prefix).

`conversationState.ts` keeps an in-memory cache and localStorage helpers used for migration/compatibility.
Canonical persistence for unread and sort metadata is server-side (`app_settings` + read-state endpoints).

## Utilities

### `utils/pubkey.ts`

Current public export:
- `getContactDisplayName(name, pubkey)`

It falls back to a 12-char prefix when `name` is missing.

### `utils/pathUtils.ts`

Distance/validation helpers used by path + map UI.

### `utils/favorites.ts`

LocalStorage migration helpers for favorites; canonical favorites are server-side.

## Types and Contracts (`types.ts`)

`AppSettings` currently includes:
- `max_radio_contacts`
- `favorites`
- `auto_decrypt_dm_on_advert`
- `sidebar_sort_order`
- `last_message_times`
- `preferences_migrated`
- `advert_interval`
- `last_advert_time`
- `bots`

## Contact Info Pane

Clicking a contact's avatar in `ChatHeader` or `MessageList` opens a `ContactInfoPane` sheet (right drawer) showing comprehensive contact details fetched from `GET /api/contacts/{key}/detail`:

- Header: avatar, name, public key, type badge, on-radio badge
- Info grid: last seen, first heard, last contacted, distance, hops
- GPS location (clickable → map)
- Favorite toggle
- Name history ("Also Known As") — shown only when the contact has used multiple names
- Message stats: DM count, channel message count
- Most active rooms (clickable → navigate to channel)
- Advert observation rate
- Nearest repeaters (resolved from first-hop path prefixes)
- Recent advert paths

State: `infoPaneContactKey` in App.tsx controls open/close. Live contact data from WebSocket updates is preferred over the initial detail snapshot.

## Repeater Dashboard

For repeater contacts (`type=2`), App.tsx renders `RepeaterDashboard` instead of the normal chat UI (ChatHeader + MessageList + MessageInput).

**Login**: `RepeaterLogin` component — password or guest login via `POST /api/contacts/{key}/repeater/login`.

**Dashboard panes** (after login): Telemetry, Neighbors, ACL, Radio Settings, Advert Intervals, Owner Info — each fetched via granular `POST /api/contacts/{key}/repeater/{pane}` endpoints. Panes retry up to 3 times client-side. "Load All" fetches all panes serially (parallel would queue behind the radio lock).

**Actions pane**: Send Advert, Sync Clock, Reboot — all send CLI commands via `POST /api/contacts/{key}/command`.

**Console pane**: Full CLI access via the same command endpoint. History is ephemeral (not persisted to DB).

All state is managed by `useRepeaterDashboard` hook. State resets on conversation change.

## Styling

UI styling is mostly utility-class driven (Tailwind-style classes in JSX) plus shared globals in `index.css` and `styles.css`.
Do not rely on old class-only layout assumptions.

## Security Posture (intentional)

- No authentication UI.
- Frontend assumes trusted network usage.
- Bot editor intentionally allows arbitrary backend bot code configuration.

## Testing

```bash
cd frontend
npm run test:run
npm run build
```

When touching cross-layer contracts, also run backend tests from repo root:

```bash
PYTHONPATH=. uv run pytest tests/ -v
```

## Errata & Known Non-Issues

### RawPacketList always scrolls to bottom

`RawPacketList` unconditionally scrolls to the latest packet on every update. This is intentional — the packet feed is a live status display, not an interactive log meant for lingering or long-term analysis. Users watching it want to see the newest packet, not hold a scroll position.

## Editing Checklist

1. If API/WS payloads change, update `types.ts`, handlers, and tests.
2. If URL/hash behavior changes, update `utils/urlHash.ts` tests.
3. If read/unread semantics change, update `useUnreadCounts` tests.
4. Keep this file concise; prefer source links over speculative detail.
