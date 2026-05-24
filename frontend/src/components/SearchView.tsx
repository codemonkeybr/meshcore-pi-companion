import { useState, useEffect, useRef, useCallback } from 'react';
import { api, isAbortError } from '../api';
import type { Contact, Channel } from '../types';
import { formatTime } from '../utils/messageParser';
import { Input } from './ui/input';
import { Button } from './ui/button';
import { cn } from '@/lib/utils';

const SEARCH_PAGE_SIZE = 50;
const DEBOUNCE_MS = 300;

interface SearchResult {
  id: number;
  type: 'PRIV' | 'CHAN';
  conversation_key: string;
  text: string;
  received_at: number;
  outgoing: boolean;
  sender_name: string | null;
}

const SEARCH_OPERATOR_RE = /(?<!\S)(user|channel):(?:"((?:[^"\\]|\\.)*)"|(\S+))/gi;

export interface SearchNavigateTarget {
  id: number;
  type: 'PRIV' | 'CHAN';
  conversation_key: string;
  conversation_name: string;
}

export interface SearchViewProps {
  contacts: Contact[];
  channels: Channel[];
  visibilityVersion?: number;
  onNavigateToMessage: (target: SearchNavigateTarget) => void;
  prefillRequest?: {
    query: string;
    nonce: number;
  } | null;
}

function highlightMatch(text: string, query: string): React.ReactNode[] {
  if (!query) return [text];
  const parts: React.ReactNode[] = [];
  const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
  const segments = text.split(regex);
  for (let i = 0; i < segments.length; i++) {
    if (regex.test(segments[i])) {
      parts.push(
        <mark key={i} className="bg-primary/30 text-foreground rounded-sm px-0.5">
          {segments[i]}
        </mark>
      );
    } else {
      parts.push(segments[i]);
    }
    // Reset lastIndex since we're using test() in a loop
    regex.lastIndex = 0;
  }
  return parts;
}

function getHighlightQuery(query: string): string {
  const fragments: string[] = [];
  let lastIndex = 0;
  let foundOperator = false;

  for (const match of query.matchAll(SEARCH_OPERATOR_RE)) {
    foundOperator = true;
    fragments.push(query.slice(lastIndex, match.index));
    lastIndex = (match.index ?? 0) + match[0].length;
  }

  if (!foundOperator) {
    return query;
  }

  fragments.push(query.slice(lastIndex));
  return fragments
    .map((fragment) => fragment.trim())
    .filter(Boolean)
    .join(' ');
}

export function SearchView({
  contacts,
  channels,
  visibilityVersion = 0,
  onNavigateToMessage,
  prefillRequest = null,
}: SearchViewProps) {
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const highlightQuery = getHighlightQuery(debouncedQuery);

  // Debounce query
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(query.trim());
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  // Reset results when query changes
  useEffect(() => {
    setResults([]);
    setOffset(0);
    setHasMore(false);
  }, [debouncedQuery, visibilityVersion]);

  useEffect(() => {
    if (!prefillRequest) {
      return;
    }

    const nextQuery = prefillRequest.query.trim();
    setQuery(nextQuery);
    setDebouncedQuery(nextQuery);
    inputRef.current?.focus();
  }, [prefillRequest]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // Fetch search results
  useEffect(() => {
    if (!debouncedQuery) {
      setResults([]);
      setHasMore(false);
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    api
      .getMessages({ q: debouncedQuery, limit: SEARCH_PAGE_SIZE, offset: 0 }, controller.signal)
      .then((data) => {
        setResults(data as SearchResult[]);
        setHasMore(data.length >= SEARCH_PAGE_SIZE);
        setOffset(data.length);
      })
      .catch((err) => {
        if (!isAbortError(err)) {
          console.error('Search failed:', err);
        }
      })
      .finally(() => {
        setLoading(false);
      });

    return () => controller.abort();
  }, [debouncedQuery, visibilityVersion]);

  const loadMore = useCallback(() => {
    if (!debouncedQuery || loading) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    api
      .getMessages({ q: debouncedQuery, limit: SEARCH_PAGE_SIZE, offset }, controller.signal)
      .then((data) => {
        setResults((prev) => {
          const existingIds = new Set(prev.map((r) => r.id));
          const unique = (data as SearchResult[]).filter((r) => !existingIds.has(r.id));
          return [...prev, ...unique];
        });
        setHasMore(data.length >= SEARCH_PAGE_SIZE);
        setOffset((prev) => prev + data.length);
      })
      .catch((err) => {
        if (!isAbortError(err)) {
          console.error('Search load more failed:', err);
        }
      })
      .finally(() => {
        setLoading(false);
      });
  }, [debouncedQuery, loading, offset]);

  // Resolve conversation name from contacts/channels
  const getConversationName = useCallback(
    (result: SearchResult): string => {
      if (result.type === 'CHAN') {
        const channel = channels.find(
          (c) => c.key.toUpperCase() === result.conversation_key.toUpperCase()
        );
        return channel?.name || result.conversation_key.slice(0, 8);
      }
      const contact = contacts.find(
        (c) => c.public_key.toLowerCase() === result.conversation_key.toLowerCase()
      );
      return contact?.name || result.conversation_key.slice(0, 12);
    },
    [contacts, channels]
  );

  const handleResultClick = useCallback(
    (result: SearchResult) => {
      onNavigateToMessage({
        id: result.id,
        type: result.type,
        conversation_key: result.conversation_key,
        conversation_name: getConversationName(result),
      });
    },
    [onNavigateToMessage, getConversationName]
  );

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <h2 className="flex justify-between items-center px-4 py-2.5 border-b border-border font-semibold text-base">
        Message Search
      </h2>

      {/* Search input */}
      <div className="px-4 py-3 border-b border-border">
        <Input
          ref={inputRef}
          type="text"
          placeholder="Search all messages..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="h-9 text-sm"
          aria-label="Search messages"
        />
      </div>

      {/* Results */}
      <div className="flex-1 overflow-y-auto">
        {!debouncedQuery && (
          <div className="p-8 text-center text-muted-foreground text-sm">
            <p>Type to search across all messages</p>
            <p className="mt-2 text-xs">
              Tip: use <code>user:</code> or <code>channel:</code> for keys or names, and wrap names
              with spaces in them in quotes.
            </p>
            <p className="mt-2 text-xs">
              Warning: User-key linkage for group messages is best-effort and based on correlation
              at advertise time. It does not account for multiple users with the same name, and
              should be considered unreliable.
            </p>
          </div>
        )}

        {debouncedQuery && results.length === 0 && !loading && (
          <div className="p-8 text-center text-muted-foreground text-sm">
            No messages found for &ldquo;{debouncedQuery}&rdquo;
          </div>
        )}

        {results.map((result) => {
          const convName = getConversationName(result);
          const typeBadge = result.type === 'CHAN' ? 'Channel' : 'DM';

          return (
            <div
              key={result.id}
              className="px-4 py-3 border-b border-border/50 cursor-pointer hover:bg-accent/50 transition-colors"
              role="button"
              tabIndex={0}
              onClick={() => handleResultClick(result)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  handleResultClick(result);
                }
              }}
            >
              <div className="flex items-center gap-2 mb-1">
                <span
                  className={cn(
                    'text-[0.625rem] font-medium px-1.5 py-0.5 rounded',
                    result.type === 'CHAN'
                      ? 'bg-primary/20 text-primary'
                      : 'bg-secondary text-secondary-foreground'
                  )}
                >
                  {typeBadge}
                </span>
                <span className="text-xs font-medium text-foreground truncate">{convName}</span>
                <span className="text-[0.6875rem] text-muted-foreground ml-auto flex-shrink-0">
                  {formatTime(result.received_at)}
                </span>
              </div>
              <div className="text-[0.8125rem] text-foreground/80 line-clamp-2 break-words">
                {result.sender_name && !result.outgoing && (
                  <span className="text-muted-foreground">{result.sender_name}: </span>
                )}
                {result.outgoing && <span className="text-muted-foreground">You: </span>}
                {highlightMatch(
                  result.sender_name && result.text.startsWith(`${result.sender_name}: `)
                    ? result.text.slice(result.sender_name.length + 2)
                    : result.text,
                  highlightQuery
                )}
              </div>
            </div>
          );
        })}

        {loading && (
          <div className="p-4 text-center text-muted-foreground text-sm" role="status">
            Searching...
          </div>
        )}

        {hasMore && !loading && (
          <div className="p-4 text-center">
            <Button variant="outline" size="sm" onClick={loadMore}>
              Load more results
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
