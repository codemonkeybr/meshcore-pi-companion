# Statistics API

Aggregated mesh network statistics (message counts, contacts, channels, path stats, etc.).

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/statistics` | Aggregated statistics |

## GET /api/statistics

**Response (200):** Object with fields such as total messages, contacts count, channels count, undecrypted count, path/multibyte stats, etc. Exact shape is defined by `StatisticsResponse` in the backend.
