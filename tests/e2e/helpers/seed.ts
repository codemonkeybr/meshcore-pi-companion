import { execSync } from 'child_process';
import path from 'path';
import crypto from 'crypto';

const ROOT = path.resolve(__dirname, '..', '..', '..');
const DEFAULT_E2E_DB = path.join(ROOT, 'tests', 'e2e', '.tmp', 'e2e-test.db');
const DB_PATH = process.env.MESHCORE_DATABASE_PATH ?? DEFAULT_E2E_DB;

// Pi remote seed support: when E2E_BASE_URL points to a remote host,
// run the Python injection via SSH instead of locally.
const E2E_BASE_URL = process.env.E2E_BASE_URL;
const PI_SSH_HOST = (() => {
  if (!E2E_BASE_URL) return null;
  try {
    return new URL(E2E_BASE_URL).hostname;
  } catch {
    return null;
  }
})();
const PI_SSH_USER = process.env.E2E_PI_USER ?? 'tesso';
const PI_DB_PATH = process.env.E2E_PI_DB_PATH ?? '/opt/remoteterm/data/meshcore.db';

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
  // Override db_path for remote Pi runs
  const enrichedPayload = PI_SSH_HOST
    ? { ...payload, db_path: PI_DB_PATH }
    : payload;
  const b64 = Buffer.from(JSON.stringify(enrichedPayload), 'utf8').toString('base64');

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


def inject_raw_packet(hex_data: str, payload_hash: str):
    ts = int(time.time())
    data_blob = bytes.fromhex(hex_data)
    hash_blob = bytes.fromhex(payload_hash)
    conn.execute(
        """
        INSERT OR IGNORE INTO raw_packets (timestamp, data, payload_hash)
        VALUES (?, ?, ?)
        """,
        (ts, data_blob, hash_blob),
    )
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
elif payload['action'] == 'inject_raw_packet':
    inject_raw_packet(payload['hex_data'], payload['payload_hash'])
else:
    raise SystemExit('unknown action')

conn.close()
PY`;

  if (PI_SSH_HOST) {
    // Run injection on the Pi via SSH using sudo so we can write to the DB
    // which is owned by the remoteterm service user.
    // Pass the Python script body via stdin to avoid heredoc quoting issues.
    const pyBody = script
      .replace(/^python3 - <<'PY'\n/, '')
      .replace(/\nPY$/, '');
    const sshCmd = `ssh -o StrictHostKeyChecking=no -o BatchMode=yes ${PI_SSH_USER}@${PI_SSH_HOST} 'PAYLOAD="${b64}" sudo -E python3 -'`;
    execSync(sshCmd, {
      input: pyBody,
      env: { ...process.env },
      stdio: ['pipe', 'inherit', 'inherit'],
    });
  } else {
    execSync(script, {
      env: { ...process.env, PAYLOAD: b64 },
      stdio: 'inherit',
    });
  }
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

interface EncryptedGroupTextOptions {
  channelName: string; // e.g. "test" — will be prefixed with # if needed
  senderName: string;
  messageText: string;
  timestamp?: number;
}

/**
 * Build a raw MeshCore GROUP_TEXT packet encrypted with the channel key,
 * matching the format expected by decoder.py `decrypt_group_text`.
 *
 * Packet layout:
 *   header(1) + path_len(1) + payload
 * Where payload = channel_hash(1) + mac(2) + ciphertext
 *
 * Header byte for FLOOD + GROUP_TEXT: route_type=0x01, payload_type=0x05 → (0x05 << 2) | 0x01 = 0x15
 */
function buildEncryptedGroupTextPacket(options: EncryptedGroupTextOptions): {
  rawHex: string;
  payloadHash: string;
} {
  const hashName = options.channelName.startsWith('#')
    ? options.channelName
    : `#${options.channelName}`;

  // channel_key = SHA256("#name")[:16]
  const channelKeyFull = crypto.createHash('sha256').update(hashName).digest();
  const channelKey = channelKeyFull.subarray(0, 16);

  // channel_hash = SHA256(channel_key)[0]
  const channelHash = crypto.createHash('sha256').update(channelKey).digest()[0];

  // Build plaintext: timestamp(4 LE) + flags(1) + "sender: message\0"
  const ts = options.timestamp ?? Math.floor(Date.now() / 1000);
  const tsBuf = Buffer.alloc(4);
  tsBuf.writeUInt32LE(ts, 0);
  const flagsBuf = Buffer.from([0x00]);
  const textStr = `${options.senderName}: ${options.messageText}\0`;
  const textBuf = Buffer.from(textStr, 'utf-8');

  const plainLen = 4 + 1 + textBuf.length;
  const paddedLen = Math.ceil(plainLen / 16) * 16;
  const plaintext = Buffer.alloc(paddedLen, 0);
  tsBuf.copy(plaintext, 0);
  flagsBuf.copy(plaintext, 4);
  textBuf.copy(plaintext, 5);

  // Encrypt: AES-128-ECB
  const cipher = crypto.createCipheriv('aes-128-ecb', channelKey, null);
  cipher.setAutoPadding(false);
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);

  // MAC: HMAC-SHA256(channel_key + 16_zero_bytes, ciphertext)[:2]
  const channelSecret = Buffer.concat([channelKey, Buffer.alloc(16, 0)]);
  const mac = crypto.createHmac('sha256', channelSecret).update(ciphertext).digest().subarray(0, 2);

  // Payload: channel_hash + mac + ciphertext
  const payload = Buffer.concat([Buffer.from([channelHash]), mac, ciphertext]);

  // Raw packet: header(0x15 = FLOOD + GROUP_TEXT) + path_len(0x00) + payload
  const rawPacket = Buffer.concat([Buffer.from([0x15, 0x00]), payload]);

  // payload_hash for dedup: SHA256 of the payload portion
  const payloadHash = crypto.createHash('sha256').update(payload).digest().toString('hex');

  return {
    rawHex: rawPacket.toString('hex'),
    payloadHash,
  };
}

/**
 * Build an encrypted GROUP_TEXT packet and inject it into the raw_packets table.
 */
export function injectEncryptedGroupText(options: EncryptedGroupTextOptions) {
  const { rawHex, payloadHash } = buildEncryptedGroupTextPacket(options);
  runPython({
    action: 'inject_raw_packet',
    root: ROOT,
    db_path: DB_PATH,
    hex_data: rawHex,
    payload_hash: payloadHash,
  });
  return { rawHex, payloadHash };
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
