import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { ContactInfoPane } from '../components/ContactInfoPane';
import type { Contact, ContactAnalytics } from '../types';

const { getContactAnalytics } = vi.hoisted(() => ({
  getContactAnalytics: vi.fn(),
}));

vi.mock('../api', () => ({
  api: {
    getContactAnalytics,
  },
}));

vi.mock('../components/ui/sheet', () => ({
  Sheet: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('../components/ContactAvatar', () => ({
  ContactAvatar: () => <div data-testid="contact-avatar" />,
}));

vi.mock('../components/ui/sonner', () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

function createContact(overrides: Partial<Contact> = {}): Contact {
  return {
    public_key: 'AA'.repeat(32),
    name: 'Alice',
    type: 1,
    flags: 0,
    last_path: null,
    last_path_len: 0,
    out_path_hash_mode: 0,
    last_advert: null,
    lat: null,
    lon: null,
    last_seen: 1700000000,
    on_radio: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: 1699990000,
    ...overrides,
  };
}

function createAnalytics(
  contact: Contact | null,
  overrides: Partial<ContactAnalytics> = {}
): ContactAnalytics {
  return {
    lookup_type: contact ? 'contact' : 'name',
    name: contact?.name ?? 'Mystery',
    contact,
    name_first_seen_at: null,
    name_history: [],
    dm_message_count: 0,
    channel_message_count: 0,
    includes_direct_messages: Boolean(contact),
    most_active_rooms: [],
    advert_paths: [],
    advert_frequency: null,
    nearest_repeaters: [],
    hourly_activity: Array.from({ length: 24 }, (_, index) => ({
      bucket_start: 1_700_000_000 + index * 3600,
      last_24h_count: 0,
      last_week_average: 0,
      all_time_average: 0,
    })),
    weekly_activity: Array.from({ length: 26 }, (_, index) => ({
      bucket_start: 1_700_000_000 + index * 604800,
      message_count: 0,
    })),
    ...overrides,
  };
}

const baseProps = {
  fromChannel: false,
  onClose: () => {},
  contacts: [] as Contact[],
  config: null,
  favorites: [],
  onToggleFavorite: () => {},
  onSearchMessagesByKey: vi.fn(),
  onSearchMessagesByName: vi.fn(),
};

describe('ContactInfoPane', () => {
  beforeEach(() => {
    getContactAnalytics.mockReset();
    baseProps.onSearchMessagesByKey = vi.fn();
    baseProps.onSearchMessagesByName = vi.fn();
  });

  it('shows hop width when contact has a stored path hash mode', async () => {
    const contact = createContact({ out_path_hash_mode: 1 });
    getContactAnalytics.mockResolvedValue(createAnalytics(contact));

    render(<ContactInfoPane {...baseProps} contactKey={contact.public_key} />);

    await screen.findByText(contact.public_key);
    await waitFor(() => {
      expect(screen.getByText('Hop Width')).toBeInTheDocument();
      expect(screen.getByText('2-byte IDs')).toBeInTheDocument();
    });
  });

  it('does not show hop width for flood-routed contacts', async () => {
    const contact = createContact({ last_path_len: -1, out_path_hash_mode: -1 });
    getContactAnalytics.mockResolvedValue(createAnalytics(contact));

    render(<ContactInfoPane {...baseProps} contactKey={contact.public_key} />);

    await screen.findByText('Alice');
    await waitFor(() => {
      expect(screen.queryByText('Hop Width')).not.toBeInTheDocument();
      expect(screen.getByText('Flood')).toBeInTheDocument();
    });
  });

  it('shows forced routing override and learned route separately', async () => {
    const contact = createContact({
      last_path_len: 1,
      out_path_hash_mode: 0,
      route_override_path: 'ae92f13e',
      route_override_len: 2,
      route_override_hash_mode: 1,
    });
    getContactAnalytics.mockResolvedValue(createAnalytics(contact));

    render(<ContactInfoPane {...baseProps} contactKey={contact.public_key} />);

    await screen.findByText('Alice');
    await waitFor(() => {
      expect(screen.getByText('Routing')).toBeInTheDocument();
      expect(screen.getByText('(forced)')).toBeInTheDocument();
      expect(screen.getByText('Learned Route')).toBeInTheDocument();
      expect(screen.getByText('1 hop')).toBeInTheDocument();
    });
  });

  it('loads name-only channel stats and most active rooms', async () => {
    getContactAnalytics.mockResolvedValue(
      createAnalytics(null, {
        lookup_type: 'name',
        name: 'Mystery',
        name_first_seen_at: 1_699_999_000,
        channel_message_count: 4,
        most_active_rooms: [
          {
            channel_key: 'ab'.repeat(16),
            channel_name: '#ops',
            message_count: 3,
          },
        ],
        hourly_activity: Array.from({ length: 24 }, (_, index) => ({
          bucket_start: 1_700_000_000 + index * 3600,
          last_24h_count: index === 23 ? 2 : 0,
          last_week_average: index === 23 ? 1.5 : 0,
          all_time_average: index === 23 ? 1.2 : 0,
        })),
        weekly_activity: Array.from({ length: 26 }, (_, index) => ({
          bucket_start: 1_700_000_000 + index * 604800,
          message_count: index === 25 ? 4 : 0,
        })),
      })
    );

    render(<ContactInfoPane {...baseProps} contactKey="name:Mystery" fromChannel />);

    await screen.findByText('Mystery');
    await waitFor(() => {
      expect(getContactAnalytics).toHaveBeenCalledWith({ name: 'Mystery' });
      expect(screen.getByText('Messages')).toBeInTheDocument();
      expect(screen.getByText('Channel Messages')).toBeInTheDocument();
      expect(screen.getByText('4', { selector: 'p' })).toBeInTheDocument();
      expect(screen.getByText('Name First In Use')).toBeInTheDocument();
      expect(screen.getByText('Messages Per Hour')).toBeInTheDocument();
      expect(screen.getByText('Messages Per Week')).toBeInTheDocument();
      expect(screen.getByText('Most Active Rooms')).toBeInTheDocument();
      expect(screen.getByText('#ops')).toBeInTheDocument();
      expect(
        screen.getByText(/Name-only analytics include channel messages only/i)
      ).toBeInTheDocument();
      expect(screen.getByText(/same sender name/i)).toBeInTheDocument();
      expect(screen.getByText("Search user's messages by name")).toBeInTheDocument();
    });
  });

  it('fires the name search callback from the name-only pane', async () => {
    getContactAnalytics.mockResolvedValue(
      createAnalytics(null, { lookup_type: 'name', name: 'Mystery' })
    );

    render(<ContactInfoPane {...baseProps} contactKey="name:Mystery" fromChannel />);

    const button = await screen.findByRole('button', { name: "Search user's messages by name" });
    button.click();

    expect(baseProps.onSearchMessagesByName).toHaveBeenCalledWith('Mystery');
  });

  it('shows alias note in the channel attribution warning for keyed contacts', async () => {
    const contact = createContact();
    getContactAnalytics.mockResolvedValue(
      createAnalytics(contact, {
        name_history: [
          { name: 'Alice', first_seen: 1000, last_seen: 2000 },
          { name: 'AliceOld', first_seen: 900, last_seen: 999 },
        ],
      })
    );

    render(<ContactInfoPane {...baseProps} contactKey={contact.public_key} fromChannel />);

    await screen.findByText(contact.public_key);
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Also Known As' })).toBeInTheDocument();
      expect(
        screen.getByText(
          /may include messages previously attributed under names shown in Also Known As/i
        )
      ).toBeInTheDocument();
      expect(screen.getByText("Search user's messages by key")).toBeInTheDocument();
    });
  });

  it('fires the key search callback from the keyed pane', async () => {
    const contact = createContact();
    getContactAnalytics.mockResolvedValue(createAnalytics(contact));

    render(<ContactInfoPane {...baseProps} contactKey={contact.public_key} />);

    const button = await screen.findByRole('button', { name: "Search user's messages by key" });
    button.click();

    expect(baseProps.onSearchMessagesByKey).toHaveBeenCalledWith(contact.public_key);
  });
});
