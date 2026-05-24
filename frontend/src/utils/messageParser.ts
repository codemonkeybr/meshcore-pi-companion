/**
 * Parse sender from channel message text.
 * Channel messages have format "sender: message".
 */
export const HASHTAG_CHANNEL_NAME_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const HASHTAG_CHANNEL_REFERENCE_PATTERN = /(^|\s)(#[a-z0-9]+(?:-[a-z0-9]+)*)(?=$|[\s.,;:])/g;

export function parseSenderFromText(text: string): { sender: string | null; content: string } {
  const colonIndex = text.indexOf(': ');
  if (colonIndex > 0 && colonIndex < 50) {
    const potentialSender = text.substring(0, colonIndex);
    // Check for colon in potential sender (would indicate it's not a simple name)
    if (!potentialSender.includes(':')) {
      return {
        sender: potentialSender,
        content: text.substring(colonIndex + 2),
      };
    }
  }
  return { sender: null, content: text };
}

export interface HashtagChannelReference {
  label: string;
  start: number;
  end: number;
}

export function findLinkedChannelReferences(text: string): HashtagChannelReference[] {
  const references: HashtagChannelReference[] = [];
  let match: RegExpExecArray | null;

  HASHTAG_CHANNEL_REFERENCE_PATTERN.lastIndex = 0;
  while ((match = HASHTAG_CHANNEL_REFERENCE_PATTERN.exec(text)) !== null) {
    const prefix = match[1];
    const label = match[2];
    const start = match.index + prefix.length;
    references.push({
      label,
      start,
      end: start + label.length,
    });
  }

  return references;
}

/**
 * Format a Unix timestamp to a time string.
 * Shows date for messages not from today.
 */
export function formatTime(timestamp: number): string {
  const date = new Date(timestamp * 1000);
  const now = new Date();
  const isToday = date.toDateString() === now.toDateString();

  const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });

  if (isToday) {
    return time;
  }

  // Show short date for older messages
  const dateStr = date.toLocaleDateString([], { month: 'short', day: 'numeric' });
  return `${dateStr} ${time}`;
}

/** Check if a message text contains a mention of the given name in @[name] format. */
export function messageContainsMention(text: string, name: string | null): boolean {
  if (!name) return false;
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const mentionPattern = new RegExp(`@\\[${escaped}\\]`, 'i');
  return mentionPattern.test(text);
}
