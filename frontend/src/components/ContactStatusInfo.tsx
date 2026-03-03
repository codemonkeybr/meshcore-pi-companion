import type { ReactNode } from 'react';
import { toast } from './ui/sonner';
import { api } from '../api';
import { formatTime } from '../utils/messageParser';
import { isValidLocation, calculateDistance, formatDistance } from '../utils/pathUtils';
import { getMapFocusHash } from '../utils/urlHash';
import type { Contact } from '../types';

interface ContactStatusInfoProps {
  contact: Contact;
  ourLat: number | null;
  ourLon: number | null;
}

/**
 * Renders the "(Last heard: ..., N hops, lat, lon (dist))" status line
 * shared between ChatHeader and RepeaterDashboard.
 */
export function ContactStatusInfo({ contact, ourLat, ourLon }: ContactStatusInfoProps) {
  const parts: ReactNode[] = [];

  if (contact.last_seen) {
    parts.push(`Last heard: ${formatTime(contact.last_seen)}`);
  }

  if (contact.last_path_len === -1) {
    parts.push('flood');
  } else if (contact.last_path_len === 0) {
    parts.push(
      <span
        key="path"
        className="cursor-pointer hover:text-primary hover:underline"
        onClick={(e) => {
          e.stopPropagation();
          if (window.confirm('Reset path to flood?')) {
            api.resetContactPath(contact.public_key).then(
              () => toast.success('Path reset to flood'),
              () => toast.error('Failed to reset path')
            );
          }
        }}
        title="Click to reset path to flood"
      >
        direct
      </span>
    );
  } else if (contact.last_path_len > 0) {
    parts.push(
      <span
        key="path"
        className="cursor-pointer hover:text-primary hover:underline"
        onClick={(e) => {
          e.stopPropagation();
          if (window.confirm('Reset path to flood?')) {
            api.resetContactPath(contact.public_key).then(
              () => toast.success('Path reset to flood'),
              () => toast.error('Failed to reset path')
            );
          }
        }}
        title="Click to reset path to flood"
      >
        {contact.last_path_len} hop{contact.last_path_len > 1 ? 's' : ''}
      </span>
    );
  }

  if (isValidLocation(contact.lat, contact.lon)) {
    const distFromUs =
      ourLat != null && ourLon != null && isValidLocation(ourLat, ourLon)
        ? calculateDistance(ourLat, ourLon, contact.lat, contact.lon)
        : null;
    parts.push(
      <span key="coords">
        <span
          className="font-mono cursor-pointer hover:text-primary hover:underline"
          onClick={(e) => {
            e.stopPropagation();
            const url =
              window.location.origin +
              window.location.pathname +
              getMapFocusHash(contact.public_key);
            window.open(url, '_blank');
          }}
          title="View on map"
        >
          {contact.lat!.toFixed(3)}, {contact.lon!.toFixed(3)}
        </span>
        {distFromUs !== null && ` (${formatDistance(distFromUs)})`}
      </span>
    );
  }

  if (parts.length === 0) return null;

  return (
    <span className="font-normal text-sm text-muted-foreground flex-shrink-0">
      (
      {parts.map((part, i) => (
        <span key={i}>
          {i > 0 && ', '}
          {part}
        </span>
      ))}
      )
    </span>
  );
}
