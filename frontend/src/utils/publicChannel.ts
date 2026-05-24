import type { Channel } from '../types';

export const PUBLIC_CHANNEL_KEY = '8B3387E9C5CDEA6AC9E5EDBAA115CD72';
export const PUBLIC_CHANNEL_NAME = 'Public';

export function isPublicChannelKey(key: string): boolean {
  return key.toUpperCase() === PUBLIC_CHANNEL_KEY;
}

export function findPublicChannel(channels: Channel[]): Channel | undefined {
  return channels.find((channel) => isPublicChannelKey(channel.key));
}
