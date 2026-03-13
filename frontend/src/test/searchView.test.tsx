import { fireEvent, render, screen, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Message } from '../types';

const mockGetMessages = vi.fn<(...args: unknown[]) => Promise<Message[]>>();

vi.mock('../api', () => ({
  api: {
    getMessages: (...args: unknown[]) => mockGetMessages(...args),
  },
  isAbortError: (err: unknown) => err instanceof DOMException && err.name === 'AbortError',
}));

import { SearchView } from '../components/SearchView';

function createSearchResult(overrides: Partial<Message> = {}): Message {
  return {
    id: 1,
    type: 'CHAN',
    conversation_key: 'ABC123',
    text: 'hello world',
    sender_timestamp: 1700000000,
    received_at: 1700000000,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: false,
    acked: 0,
    sender_name: 'Alice',
    ...overrides,
  };
}

const defaultProps = {
  contacts: [],
  channels: [
    { key: 'ABC123', name: 'Public', is_hashtag: true, on_radio: false, last_read_at: null },
  ],
  onNavigateToMessage: vi.fn(),
};

/** Type the query into the search input and wait for debounced results to render. */
async function typeAndWaitForResults(query: string) {
  const input = screen.getByLabelText('Search messages');
  // Use fake timers only for the debounce, then switch to real timers for
  // React's async state updates and waitFor polling.
  vi.useFakeTimers();
  await act(async () => {
    fireEvent.change(input, { target: { value: query } });
    vi.advanceTimersByTime(350);
  });
  vi.useRealTimers();
  // Wait for the mock API promise to resolve and React to commit
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

describe('SearchView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders empty state with prompt text', () => {
    mockGetMessages.mockResolvedValue([]);
    render(<SearchView {...defaultProps} />);
    expect(screen.getByText('Type to search across all messages')).toBeInTheDocument();
    expect(screen.getByText(/Tip: use/i)).toBeInTheDocument();
    expect(
      screen.getByText(/User-key linkage for group messages is best-effort/i)
    ).toBeInTheDocument();
  });

  it('focuses input on mount', () => {
    mockGetMessages.mockResolvedValue([]);
    render(<SearchView {...defaultProps} />);
    expect(screen.getByLabelText('Search messages')).toHaveFocus();
  });

  it('debounces search input', async () => {
    mockGetMessages.mockResolvedValue([]);
    vi.useFakeTimers();
    render(<SearchView {...defaultProps} />);

    const input = screen.getByLabelText('Search messages');
    await act(async () => {
      fireEvent.change(input, { target: { value: 'hello' } });
    });

    // Should not have called API yet (within debounce window)
    expect(mockGetMessages).not.toHaveBeenCalled();

    // Advance past debounce timer
    await act(async () => {
      vi.advanceTimersByTime(350);
    });
    vi.useRealTimers();

    expect(mockGetMessages).toHaveBeenCalledTimes(1);
    expect(mockGetMessages).toHaveBeenCalledWith(
      expect.objectContaining({ q: 'hello' }),
      expect.any(AbortSignal)
    );
  });

  it('displays search results', async () => {
    mockGetMessages.mockResolvedValue([
      createSearchResult({ id: 1, text: 'hello world', sender_name: 'Alice' }),
      createSearchResult({ id: 2, text: 'hello there', sender_name: 'Bob' }),
    ]);
    render(<SearchView {...defaultProps} />);

    await typeAndWaitForResults('hello');

    // Text is split by highlightMatch into segments, so use container text content
    const buttons = screen.getAllByRole('button');
    const texts = buttons.map((b) => b.textContent);
    expect(texts.some((t) => t?.includes('hello world') || t?.includes('world'))).toBe(true);
    expect(texts.some((t) => t?.includes('hello there') || t?.includes('there'))).toBe(true);
  });

  it('shows no-results message when search returns empty', async () => {
    mockGetMessages.mockResolvedValue([]);
    render(<SearchView {...defaultProps} />);

    await typeAndWaitForResults('nonexistent');

    expect(screen.getByText(/No messages found/)).toBeInTheDocument();
  });

  it('navigates to message on click', async () => {
    const result = createSearchResult({
      id: 42,
      type: 'CHAN',
      conversation_key: 'ABC123',
      text: 'click me',
    });
    mockGetMessages.mockResolvedValue([result]);
    const onNavigate = vi.fn();

    render(<SearchView {...defaultProps} onNavigateToMessage={onNavigate} />);

    await typeAndWaitForResults('click');

    const resultBtn = screen.getAllByRole('button').find((b) => b.textContent?.includes('me'));
    expect(resultBtn).toBeDefined();

    fireEvent.click(resultBtn!);

    expect(onNavigate).toHaveBeenCalledWith({
      id: 42,
      type: 'CHAN',
      conversation_key: 'ABC123',
      conversation_name: 'Public',
    });
  });

  it('navigates on Enter key', async () => {
    mockGetMessages.mockResolvedValue([createSearchResult({ id: 10, text: 'keyboard nav' })]);
    const onNavigate = vi.fn();
    render(<SearchView {...defaultProps} onNavigateToMessage={onNavigate} />);

    await typeAndWaitForResults('keyboard');

    const resultEl = screen.getByRole('button', { name: /keyboard nav/i });
    fireEvent.keyDown(resultEl, { key: 'Enter' });

    expect(onNavigate).toHaveBeenCalled();
  });

  it('shows load more button when results fill a page', async () => {
    const pageResults = Array.from({ length: 50 }, (_, i) =>
      createSearchResult({ id: i + 1, text: `result ${i}` })
    );
    mockGetMessages.mockResolvedValueOnce(pageResults);

    render(<SearchView {...defaultProps} />);

    await typeAndWaitForResults('result');

    expect(screen.getByText('Load more results')).toBeInTheDocument();
  });

  it('does not show load more when results are less than page size', async () => {
    mockGetMessages.mockResolvedValue([createSearchResult({ id: 1, text: 'only one' })]);

    render(<SearchView {...defaultProps} />);

    await typeAndWaitForResults('only');

    const resultBtns = screen.getAllByRole('button');
    expect(resultBtns.some((b) => b.textContent?.includes('one'))).toBe(true);
    expect(screen.queryByText('Load more results')).not.toBeInTheDocument();
  });

  it('resolves channel name from channels prop', async () => {
    mockGetMessages.mockResolvedValue([
      createSearchResult({ id: 1, type: 'CHAN', conversation_key: 'ABC123', text: 'test' }),
    ]);

    render(<SearchView {...defaultProps} />);

    await typeAndWaitForResults('test');

    expect(screen.getByText('Public')).toBeInTheDocument();
  });

  it('resolves contact name from contacts prop', async () => {
    const contactKey = 'aa'.repeat(32);
    mockGetMessages.mockResolvedValue([
      createSearchResult({
        id: 1,
        type: 'PRIV',
        conversation_key: contactKey,
        text: 'dm test',
      }),
    ]);

    render(
      <SearchView
        {...defaultProps}
        contacts={[
          {
            public_key: contactKey,
            name: 'Bob',
            type: 1,
            flags: 0,
            last_path: null,
            last_path_len: -1,
            out_path_hash_mode: 0,
            last_advert: null,
            lat: null,
            lon: null,
            last_seen: null,
            on_radio: false,
            last_contacted: null,
            first_seen: null,
            last_read_at: null,
          },
        ]}
      />
    );

    await typeAndWaitForResults('dm');

    expect(screen.getByText('Bob')).toBeInTheDocument();
  });

  it('passes raw operator queries to the API and highlights only free text', async () => {
    mockGetMessages.mockResolvedValue([createSearchResult({ text: 'hello world' })]);

    render(<SearchView {...defaultProps} />);

    await typeAndWaitForResults('user:Alice hello');

    expect(mockGetMessages).toHaveBeenCalledWith(
      expect.objectContaining({ q: 'user:Alice hello' }),
      expect.any(AbortSignal)
    );
    expect(screen.getByText('hello', { selector: 'mark' })).toBeInTheDocument();
    expect(screen.queryByText('user:Alice', { selector: 'mark' })).not.toBeInTheDocument();
  });

  it('runs a prefilled search immediately', async () => {
    mockGetMessages.mockResolvedValue([createSearchResult({ text: 'prefilled result' })]);

    render(
      <SearchView {...defaultProps} prefillRequest={{ query: 'user:"Alice Smith"', nonce: 1 }} />
    );

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(screen.getByLabelText('Search messages')).toHaveValue('user:"Alice Smith"');
    expect(mockGetMessages).toHaveBeenCalledWith(
      expect.objectContaining({ q: 'user:"Alice Smith"' }),
      expect.any(AbortSignal)
    );
  });

  it('aborts the load-more request on unmount', async () => {
    const pageResults = Array.from({ length: 50 }, (_, i) =>
      createSearchResult({ id: i + 1, text: `result ${i}` })
    );
    let resolveLoadMore: ((value: Message[]) => void) | null = null;
    mockGetMessages.mockResolvedValueOnce(pageResults).mockImplementationOnce(
      () =>
        new Promise<Message[]>((resolve) => {
          resolveLoadMore = resolve;
        })
    );

    const { unmount } = render(<SearchView {...defaultProps} />);

    await typeAndWaitForResults('result');
    fireEvent.click(screen.getByText('Load more results'));

    const loadMoreSignal = mockGetMessages.mock.calls[1]?.[1] as AbortSignal | undefined;
    expect(loadMoreSignal).toBeInstanceOf(AbortSignal);
    expect(loadMoreSignal?.aborted).toBe(false);

    unmount();

    expect(loadMoreSignal?.aborted).toBe(true);

    await act(async () => {
      resolveLoadMore?.([createSearchResult({ id: 99, text: 'late result' })]);
      await Promise.resolve();
    });
  });
});
