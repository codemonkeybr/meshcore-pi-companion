import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { MessageList } from '../components/MessageList';
import type { Message } from '../types';

const scrollIntoViewMock = vi.fn();
const originalGetBoundingClientRect = HTMLElement.prototype.getBoundingClientRect;

function createMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 1,
    type: 'CHAN',
    conversation_key: 'C3B889530D4F02DB5662EA13C417F530',
    text: 'Alice: hello world',
    sender_timestamp: 1700000000,
    received_at: 1700000001,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: false,
    acked: 0,
    sender_name: null,
    ...overrides,
  };
}

describe('MessageList channel sender rendering', () => {
  beforeEach(() => {
    scrollIntoViewMock.mockReset();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoViewMock,
      writable: true,
    });
    Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect', {
      configurable: true,
      value: originalGetBoundingClientRect,
      writable: true,
    });
  });

  it('renders explicit corrupt placeholder and warning avatar for unnamed corrupt channel packets', () => {
    render(
      <MessageList
        messages={[
          createMessage({
            text: "Nv\x0ek\x16ɩ'\x7fg:",
            sender_name: null,
            sender_key: null,
          }),
        ]}
        contacts={[]}
        loading={false}
      />
    );

    expect(screen.getByText('<No name -- corrupt packet?>')).toBeInTheDocument();
    expect(screen.getByTestId('corrupt-avatar')).toBeInTheDocument();
  });

  it('prefers stored sender_name for channel messages even when text is not sender-prefixed', () => {
    render(
      <MessageList
        messages={[
          createMessage({
            text: 'garbled payload with no sender prefix',
            sender_name: 'Alice',
            sender_key: 'ab'.repeat(32),
          }),
        ]}
        contacts={[]}
        loading={false}
      />
    );

    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.getByText('A')).toBeInTheDocument();
  });

  it('gives clickable sender avatars an accessible label', () => {
    render(
      <MessageList
        messages={[
          createMessage({
            text: 'garbled payload with no sender prefix',
            sender_name: 'Alice',
            sender_key: 'ab'.repeat(32),
          }),
        ]}
        contacts={[]}
        loading={false}
        onOpenContactInfo={() => {}}
      />
    );

    expect(screen.getByRole('button', { name: 'View info for Alice' })).toBeInTheDocument();
  });

  it('renders and dismisses an unread marker at the first unread message boundary', async () => {
    const user = userEvent.setup();
    const messages = [
      createMessage({ id: 1, received_at: 1700000001, text: 'Alice: older' }),
      createMessage({ id: 2, received_at: 1700000010, text: 'Alice: newer' }),
    ];

    function DismissibleUnreadMarkerList() {
      const [unreadMarkerLastReadAt, setUnreadMarkerLastReadAt] = useState<number | undefined>(
        1700000005
      );

      return (
        <MessageList
          messages={messages}
          contacts={[]}
          loading={false}
          unreadMarkerLastReadAt={unreadMarkerLastReadAt}
          onDismissUnreadMarker={() => setUnreadMarkerLastReadAt(undefined)}
        />
      );
    }

    render(<DismissibleUnreadMarkerList />);

    const marker = screen.getByRole('button', { name: /Unread messages/i });
    expect(marker).toBeInTheDocument();
    expect(screen.getByText('older')).toBeInTheDocument();
    expect(screen.getByText('newer')).toBeInTheDocument();

    await user.click(marker);

    expect(screen.queryByRole('button', { name: /Unread messages/i })).not.toBeInTheDocument();
  });

  it('shows a jump-to-unread button and dismisses it after use without hiding the marker', async () => {
    const user = userEvent.setup();
    const messages = [
      createMessage({ id: 1, received_at: 1700000001, text: 'Alice: older' }),
      createMessage({ id: 2, received_at: 1700000010, text: 'Alice: newer' }),
    ];

    render(
      <MessageList
        messages={messages}
        contacts={[]}
        loading={false}
        unreadMarkerLastReadAt={1700000005}
      />
    );

    const jumpButton = screen.getByRole('button', { name: 'Jump to unread' });
    expect(jumpButton).toBeInTheDocument();
    expect(screen.getByText('Unread messages')).toBeInTheDocument();

    await user.click(jumpButton);

    expect(screen.queryByRole('button', { name: 'Jump to unread' })).not.toBeInTheDocument();
    expect(screen.getByText('Unread messages')).toBeInTheDocument();
    expect(scrollIntoViewMock).toHaveBeenCalled();
  });

  it('lets the user dismiss the jump-to-unread button without scrolling or hiding the marker', async () => {
    const user = userEvent.setup();
    const messages = [
      createMessage({ id: 1, received_at: 1700000001, text: 'Alice: older' }),
      createMessage({ id: 2, received_at: 1700000010, text: 'Alice: newer' }),
    ];

    render(
      <MessageList
        messages={messages}
        contacts={[]}
        loading={false}
        unreadMarkerLastReadAt={1700000005}
      />
    );

    await user.click(screen.getByRole('button', { name: 'Dismiss jump to unread' }));

    expect(screen.queryByRole('button', { name: 'Jump to unread' })).not.toBeInTheDocument();
    expect(screen.getByText('Unread messages')).toBeInTheDocument();
    expect(scrollIntoViewMock).not.toHaveBeenCalled();
  });

  it('hides the jump-to-unread button when the unread marker is already visible', () => {
    Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect', {
      configurable: true,
      writable: true,
      value: function () {
        const element = this as HTMLElement;
        if (element.textContent?.includes('Unread messages')) {
          return {
            top: 200,
            bottom: 240,
            left: 0,
            right: 300,
            width: 300,
            height: 40,
            x: 0,
            y: 200,
            toJSON: () => '',
          };
        }
        if (element.className.includes('overflow-y-auto')) {
          return {
            top: 100,
            bottom: 500,
            left: 0,
            right: 400,
            width: 400,
            height: 400,
            x: 0,
            y: 100,
            toJSON: () => '',
          };
        }
        return {
          top: 0,
          bottom: 0,
          left: 0,
          right: 0,
          width: 0,
          height: 0,
          x: 0,
          y: 0,
          toJSON: () => '',
        };
      },
    });

    const messages = [
      createMessage({ id: 1, received_at: 1700000001, text: 'Alice: older' }),
      createMessage({ id: 2, received_at: 1700000010, text: 'Alice: newer' }),
    ];

    render(
      <MessageList
        messages={messages}
        contacts={[]}
        loading={false}
        unreadMarkerLastReadAt={1700000005}
      />
    );

    expect(screen.getByText('Unread messages')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Jump to unread' })).not.toBeInTheDocument();
  });
});
