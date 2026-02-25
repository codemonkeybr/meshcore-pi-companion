import { execSync } from 'child_process';
import path from 'path';
import crypto from 'crypto';

const ROOT = path.resolve(__dirname, '..', '..', '..');
const DEFAULT_E2E_DB = path.join(ROOT, 'tests', 'e2e', '.tmp', 'e2e-test.db');
const DB_PATH = process.env.MESHCORE_DATABASE_PATH ?? DEFAULT_E2E_DB;

interface SeedOptions {
  channelName: string;
  count: number;
  startTimestamp?: number;
  outgoingEvery?: number; // mark every Nth message as outgoing
  includePaths?: boolean;
}

interface SeedReadStateOptions {
  channelName: string;
  unreadCount: number;
}

function runPython(payload: object) {
  const b64 = Buffer.from(JSON.stringify(payload), 'utf8').toString('base64');

const script = String.raw`python3 - <<'PY'
import base64, json, os, sqlite3, time

payload = json.loads(base64.b64decode(os.environ['PAYLOAD']).decode())
root = payload['root']
db_path = payload.get('db_path') or os.path.join(root, 'data', 'meshcore.db')
os.makedirs(os.path.dirname(db_path), exist_ok=True)
conn = sqlite3.connect(db_path)
conn.execute('PRAGMA journal_mode=WAL;')
conn.row_factory = sqlite3.Row


def upsert_channel(name: str, key_hex: str):
    conn.execute(
        """
        INSERT INTO channels (key, name, is_hashtag, on_radio)
        VALUES (?, ?, 1, 0)
        ON CONFLICT(key) DO UPDATE SET name=excluded.name
        """,
        (key_hex, name),
    )
    conn.commit()


def clear_channel_messages(key_hex: str):
    conn.execute("DELETE FROM messages WHERE conversation_key = ?", (key_hex,))
    conn.commit()


def seed_messages(key_hex: str, opts: dict):
    start_ts = int(opts.get('start_ts') or time.time())
    count = opts['count']
    outgoing_every = opts.get('out_every') or 0
    include_paths = bool(opts.get('paths'))
    for i in range(count):
        ts = start_ts + i
        text = f"seed-{i}"
        paths_json = None
        if include_paths and i % 5 == 0:
            paths_json = json.dumps([{"path": f"{i:02x}", "received_at": ts}])
        outgoing = 1 if (outgoing_every and (i % outgoing_every == 0)) else 0
        conn.execute(
            """
            INSERT INTO messages (type, conversation_key, text, sender_timestamp, received_at, paths, txt_type, signature, outgoing, acked)
            VALUES ('CHAN', ?, ?, ?, ?, ?, 0, NULL, ?, 0)
            """,
            (key_hex, text, ts, ts, paths_json, outgoing),
        )
    conn.commit()


def set_channel_last_read(key_hex: str, last_read: int | None):
    conn.execute("UPDATE channels SET last_read_at = ? WHERE key = ?", (last_read, key_hex))
    conn.commit()


if payload['action'] == 'seed_channel':
    name = payload['name']
    key_hex = payload['key_hex']
    upsert_channel(name, key_hex)
    clear_channel_messages(key_hex)
    seed_messages(key_hex, payload['opts'])
elif payload['action'] == 'seed_unread':
    name = payload['name']
    key_hex = payload['key_hex']
    upsert_channel(name, key_hex)
    clear_channel_messages(key_hex)
    # create unread messages
    now = int(time.time())
    for i in range(payload['unread']):
        ts = now - i
        text = f"unread-{i}"
        conn.execute(
            """
            INSERT INTO messages (type, conversation_key, text, sender_timestamp, received_at, paths, txt_type, signature, outgoing, acked)
            VALUES ('CHAN', ?, ?, ?, ?, NULL, 0, NULL, 0, 0)
            """,
            (key_hex, text, ts, ts),
        )
    set_channel_last_read(key_hex, now - 10_000)  # ensure unread
else:
    raise SystemExit('unknown action')

conn.close()
PY`;

  execSync(script, {
    env: { ...process.env, PAYLOAD: b64 },
    stdio: 'inherit',
  });
}

function channelKeyFromName(name: string): string {
  // Matches backend: SHA256("#name").digest()[:16]
  const hash = crypto.createHash('sha256').update(name).digest('hex');
  return hash.slice(0, 32).toUpperCase();
}

export function seedChannelMessages(options: SeedOptions) {
  const keyHex = channelKeyFromName(
    options.channelName.startsWith('#') ? options.channelName : `#${options.channelName}`
  );
  runPython({
    action: 'seed_channel',
    root: ROOT,
    db_path: DB_PATH,
    name: options.channelName,
    key_hex: keyHex,
    opts: {
      count: options.count,
      start_ts: options.startTimestamp ?? Math.floor(Date.now() / 1000) - options.count,
      out_every: options.outgoingEvery ?? 0,
      paths: options.includePaths ?? false,
    },
  });
  return { key: keyHex };
}

export function seedChannelUnread(options: SeedReadStateOptions) {
  const keyHex = channelKeyFromName(
    options.channelName.startsWith('#') ? options.channelName : `#${options.channelName}`
  );
  runPython({
    action: 'seed_unread',
    root: ROOT,
    db_path: DB_PATH,
    name: options.channelName,
    key_hex: keyHex,
    unread: options.unreadCount,
  });
  return { key: keyHex };
}
