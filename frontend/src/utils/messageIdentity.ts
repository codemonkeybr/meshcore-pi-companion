import type { Message } from '../types';

// Content identity matches the backend's message-level dedup indexes.
export function getMessageContentKey(msg: Message): string {
  // When sender_timestamp exists, dedup by content (catches radio-path duplicates with different IDs).
  // When null, include msg.id so each message gets a unique key — avoids silently dropping
  // different messages that share the same text and received_at second.
  const ts = msg.sender_timestamp ?? `r${msg.received_at}-${msg.id}`;
  // For incoming PRIV messages (room-server posts), include sender_key so that
  // two different room participants sending identical text in the same second
  // are not collapsed.  Mirrors idx_messages_incoming_priv_dedup.
  const senderSuffix = msg.type === 'PRIV' && msg.sender_key ? `-${msg.sender_key}` : '';
  return `${msg.type}-${msg.conversation_key}-${msg.text}-${ts}${senderSuffix}`;
}
