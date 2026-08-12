/**
 * The unread divider is anchored to a message id from the server. These cover the
 * one case where that id is deliberately overridden: a channel that has never
 * been read, whose true boundary is the start of history and therefore a useless
 * jump target.
 */
import { describe, expect, it } from 'vitest';

import { resolveUnreadMarkerId } from '../App';
import type { Message } from '../types';

function msg(id: number, receivedAt: number): Message {
  return {
    id,
    type: 'CHAN',
    conversation_key: 'CHAN1',
    text: `Alice: m${id}`,
    sender_timestamp: receivedAt,
    received_at: receivedAt,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: false,
    acked: 0,
    sender_name: 'Alice',
  };
}

describe('resolveUnreadMarkerId', () => {
  const loaded = [msg(50, 1700000050), msg(51, 1700000051), msg(52, 1700000052)];

  it('uses the server boundary when the channel has been read before', () => {
    expect(resolveUnreadMarkerId(9, 1700000000, loaded)).toBe(9);
  });

  it('uses the server boundary when it is inside the loaded window', () => {
    expect(resolveUnreadMarkerId(51, null, loaded)).toBe(51);
  });

  it('anchors a never-read channel to the top of the loaded window', () => {
    // Boundary 1 is the first message ever sent; jumping there would dump the
    // reader at the start of history. Everything loaded is unread, so the top of
    // the window is both true and useful.
    expect(resolveUnreadMarkerId(1, null, loaded)).toBe(50);
  });

  it('picks the oldest loaded message regardless of array order', () => {
    expect(resolveUnreadMarkerId(1, null, [loaded[2], loaded[0], loaded[1]])).toBe(50);
  });

  it('passes through when there is no boundary or no messages', () => {
    expect(resolveUnreadMarkerId(null, null, loaded)).toBeNull();
    expect(resolveUnreadMarkerId(7, null, [])).toBe(7);
  });
});
