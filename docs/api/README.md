# RemoteTerm API Reference

REST and WebSocket APIs for the RemoteTerm for MeshCore backend. All REST endpoints are prefixed with `/api`.

## Base URL

- Local: `http://localhost:8000/api`
- On Pi / network: `http://<host>:8000/api`

Interactive OpenAPI docs (when the server is running): **http://localhost:8000/docs**

## Authentication

There is no authentication. The app is intended for trusted networks only. Do not expose it to the public internet.

## Conventions

- **Public keys:** 64-character hex string; 12-character prefix is often used for lookups.
- **Channel keys:** 32-character hex string (TEXT primary key).
- **Timestamps:** Unix seconds unless noted otherwise.
- **Errors:** JSON body with `detail` (string or list) on 4xx/5xx.

## API index

| Area | Description | Doc |
|------|-------------|-----|
| [Health](#health) | Server and radio status, `setup_required` (SPI) | [health.md](health.md) |
| [Setup](#setup) | SPI provisioning (status, hardware profiles, radio presets, provision) | [setup.md](setup.md) |
| [Radio](#radio) | Config, advertise, reboot, reconnect, private key | [radio.md](radio.md) |
| [Contacts](#contacts) | List, create, delete, sync, add/remove from radio, repeater ops, trace | [contacts.md](contacts.md) |
| [Channels](#channels) | List, create, delete, sync, mark-read, flood-scope override | [channels.md](channels.md) |
| [Messages](#messages) | List, around, send direct/channel, resend | [messages.md](messages.md) |
| [Packets](#packets) | Undecrypted count, historical decrypt, maintenance | [packets.md](packets.md) |
| [Read state](#read-state) | Unread counts, mark-all-read | [read-state.md](read-state.md) |
| [Settings](#settings) | App settings, favorites, blocked keys/names, migrate | [settings.md](settings.md) |
| [Fanout](#fanout) | MQTT, bots, webhooks, Apprise configs | [fanout.md](fanout.md) |
| [Statistics](#statistics) | Aggregated mesh statistics | [statistics.md](statistics.md) |
| [WebSocket](#websocket) | Real-time events | [websocket.md](websocket.md) |
