/**
 * Generate consistent profile "images" for contacts.
 *
 * Uses the contact's public key to generate a consistent background color,
 * and extracts initials or emoji from the name for display.
 * Repeaters (type=2) and room servers (type=3) always show a fixed glyph.
 */

import { CONTACT_TYPE_REPEATER, CONTACT_TYPE_ROOM } from '../types';

// Fixed contact-type avatar styling
const REPEATER_AVATAR = {
  text: '🛜',
  background: '#444444',
  textColor: '#ffffff',
};

const ROOM_AVATAR = {
  text: '🛖',
  background: '#6b4f2a',
  textColor: '#ffffff',
};

// DJB2 hash function for strings
export function hashString(str: string): number {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = (hash << 5) - hash + char;
    hash = hash & hash; // Convert to 32-bit integer
  }
  return Math.abs(hash);
}

// Regex to match emoji (covers most common emoji ranges)
// Flag emojis (e.g., 🇺🇸) are TWO consecutive regional indicator symbols, so we match those first
const emojiRegex =
  /[\u{1F1E0}-\u{1F1FF}]{2}|[\u{1F300}-\u{1F9FF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\u{1F600}-\u{1F64F}]|[\u{1F680}-\u{1F6FF}]/u;

/**
 * Extract display characters from a contact name.
 * Priority:
 * 1. First emoji in the name
 * 2. First letter + first letter after first space (initials)
 * 3. First letter only
 */
function getAvatarText(name: string | null, publicKey: string): string {
  if (!name) {
    // Use first 2 chars of public key as fallback
    return publicKey.slice(0, 2).toUpperCase();
  }

  // Check for emoji first
  const emojiMatch = name.match(emojiRegex);
  if (emojiMatch) {
    return emojiMatch[0];
  }

  // Find first letter
  const letters = name.match(/[a-zA-Z]/g);
  if (!letters || letters.length === 0) {
    // No letters, use first 2 chars of public key
    return publicKey.slice(0, 2).toUpperCase();
  }

  // Check for space - get initials
  const spaceIndex = name.indexOf(' ');
  if (spaceIndex !== -1) {
    const firstLetter = letters[0];
    // Find first letter after the space
    const afterSpace = name.slice(spaceIndex + 1).match(/[a-zA-Z]/);
    if (afterSpace) {
      return (firstLetter + afterSpace[0]).toUpperCase();
    }
  }

  // Single letter
  return letters[0].toUpperCase();
}

/**
 * Generate a consistent HSL color from a public key.
 * Uses saturation and lightness ranges that work well for backgrounds.
 */
function getAvatarColor(publicKey: string): {
  background: string;
  text: string;
} {
  const hash = hashString(publicKey);

  // Use hash to generate hue (0-360)
  const hue = hash % 360;

  // Use different bits of hash for saturation variation (50-80%)
  const saturation = 50 + ((hash >> 8) % 30);

  // Lightness in a range that allows readable text (35-55%)
  const lightness = 35 + ((hash >> 16) % 20);

  const background = `hsl(${hue}, ${saturation}%, ${lightness}%)`;

  // Calculate perceived luminance to determine text color
  // For HSL, we can approximate: if lightness < 50%, use white text
  // We'll use a slightly lower threshold since saturated colors appear darker
  const textColor = lightness < 45 ? '#ffffff' : '#000000';

  return { background, text: textColor };
}

/**
 * Get all avatar properties for a contact.
 * Repeaters and room servers always get a special fixed avatar.
 */
export function getContactAvatar(
  name: string | null,
  publicKey: string,
  contactType?: number
): {
  text: string;
  background: string;
  textColor: string;
} {
  if (contactType === CONTACT_TYPE_REPEATER) {
    return REPEATER_AVATAR;
  }
  if (contactType === CONTACT_TYPE_ROOM) {
    return ROOM_AVATAR;
  }

  const text = getAvatarText(name, publicKey);
  const colors = getAvatarColor(publicKey);

  return {
    text,
    background: colors.background,
    textColor: colors.text,
  };
}
