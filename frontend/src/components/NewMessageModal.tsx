import { useEffect, useRef, useState } from 'react';
import { Dice5 } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Checkbox } from './ui/checkbox';
import { Button } from './ui/button';
import { toast } from './ui/sonner';

type Tab = 'new-contact' | 'new-channel' | 'hashtag' | 'bulk-hashtag';

interface BulkParseResult {
  channelNames: string[];
  invalidNames: string[];
}

interface NewMessageModalProps {
  open: boolean;
  undecryptedCount: number;
  showBulkAddChannelTab?: boolean;
  prefillRequest?: {
    tab: 'hashtag';
    hashtagName: string;
    nonce: number;
  } | null;
  onClose: () => void;
  onCreateContact: (
    name: string,
    publicKey: string,
    tryHistorical: boolean,
    type?: number
  ) => Promise<void>;
  onCreateChannel: (name: string, key: string, tryHistorical: boolean) => Promise<void>;
  onCreateHashtagChannel: (name: string, tryHistorical: boolean) => Promise<void>;
  onBulkAddHashtagChannels: (channelNames: string[], tryHistorical: boolean) => Promise<void>;
}

function validateHashtagName(channelName: string): string | null {
  if (!channelName) {
    return 'Channel name is required';
  }
  if (!/^[a-zA-Z0-9]+(-[a-zA-Z0-9]+)*$/.test(channelName)) {
    return 'Use letters, numbers, and single dashes (no leading/trailing dashes)';
  }
  return null;
}

function parseBulkHashtagNames(rawText: string, permitCapitals: boolean): BulkParseResult {
  const tokens = rawText
    .split(/[\s,]+/)
    .map((token) => token.trim())
    .filter(Boolean);

  const invalidNames: string[] = [];
  const channelNames: string[] = [];
  const seen = new Set<string>();

  for (const token of tokens) {
    const stripped = token.replace(/^#+/, '');
    const validationError = validateHashtagName(stripped);
    if (validationError) {
      invalidNames.push(token);
      continue;
    }

    const normalized = permitCapitals ? stripped : stripped.toLowerCase();
    const channelName = `#${normalized}`;
    if (seen.has(channelName)) {
      continue;
    }
    seen.add(channelName);
    channelNames.push(channelName);
  }

  return { channelNames, invalidNames };
}

export function NewMessageModal({
  open,
  undecryptedCount,
  showBulkAddChannelTab = false,
  prefillRequest = null,
  onClose,
  onCreateContact,
  onCreateChannel,
  onCreateHashtagChannel,
  onBulkAddHashtagChannels,
}: NewMessageModalProps) {
  const [tab, setTab] = useState<Tab>('new-contact');
  const [name, setName] = useState('');
  const [contactType, setContactType] = useState(1);
  const [contactKey, setContactKey] = useState('');
  const [channelKey, setChannelKey] = useState('');
  const [bulkChannelText, setBulkChannelText] = useState('');
  const [tryHistorical, setTryHistorical] = useState(false);
  const [permitCapitals, setPermitCapitals] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const hashtagInputRef = useRef<HTMLInputElement>(null);
  const bulkTextareaRef = useRef<HTMLTextAreaElement>(null);

  const resetForm = () => {
    setName('');
    setContactType(1);
    setContactKey('');
    setChannelKey('');
    setBulkChannelText('');
    setTryHistorical(false);
    setPermitCapitals(false);
    setError('');
  };

  useEffect(() => {
    if (!open) {
      return;
    }

    if (prefillRequest) {
      setTab(prefillRequest.tab);
      setName(prefillRequest.hashtagName);
      setContactKey('');
      setChannelKey('');
      setBulkChannelText('');
      setTryHistorical(false);
      setPermitCapitals(false);
      setError('');
      setLoading(false);
      requestAnimationFrame(() => {
        hashtagInputRef.current?.focus();
      });
      return;
    }

    if (showBulkAddChannelTab) {
      setTab('bulk-hashtag');
      setName('');
      setContactKey('');
      setChannelKey('');
      setBulkChannelText('');
      setTryHistorical(false);
      setPermitCapitals(false);
      setError('');
      setLoading(false);
      requestAnimationFrame(() => {
        bulkTextareaRef.current?.focus();
      });
      return;
    }

    setTab('new-contact');
  }, [open, prefillRequest, showBulkAddChannelTab]);

  const handleCreate = async () => {
    setError('');
    setLoading(true);

    try {
      if (tab === 'new-contact') {
        if (!name.trim() || !contactKey.trim()) {
          setError('Name and public key are required');
          return;
        }
        await onCreateContact(name.trim(), contactKey.trim(), tryHistorical, contactType);
      } else if (tab === 'new-channel') {
        if (!name.trim() || !channelKey.trim()) {
          setError('Channel name and key are required');
          return;
        }
        await onCreateChannel(name.trim(), channelKey.trim(), tryHistorical);
      } else if (tab === 'hashtag') {
        const channelName = name.trim();
        const validationError = validateHashtagName(channelName);
        if (validationError) {
          setError(validationError);
          return;
        }
        const normalizedName = permitCapitals ? channelName : channelName.toLowerCase();
        await onCreateHashtagChannel(`#${normalizedName}`, tryHistorical);
      } else {
        const { channelNames, invalidNames } = parseBulkHashtagNames(
          bulkChannelText,
          permitCapitals
        );
        if (channelNames.length === 0) {
          setError('Enter at least one valid channel name');
          return;
        }
        if (invalidNames.length > 0) {
          setError(`Invalid channel names: ${invalidNames.join(', ')}`);
          return;
        }
        await onBulkAddHashtagChannels(channelNames, tryHistorical);
      }

      resetForm();
      onClose();
    } catch (err) {
      toast.error('Failed to create conversation', {
        description: err instanceof Error ? err.message : undefined,
      });
      setError(err instanceof Error ? err.message : 'Failed to create');
    } finally {
      setLoading(false);
    }
  };

  const handleCreateAndAddAnother = async () => {
    setError('');
    const channelName = name.trim();
    const validationError = validateHashtagName(channelName);
    if (validationError) {
      setError(validationError);
      return;
    }

    setLoading(true);
    try {
      const normalizedName = permitCapitals ? channelName : channelName.toLowerCase();
      await onCreateHashtagChannel(`#${normalizedName}`, tryHistorical);
      setName('');
      hashtagInputRef.current?.focus();
    } catch (err) {
      toast.error('Failed to create conversation', {
        description: err instanceof Error ? err.message : undefined,
      });
      setError(err instanceof Error ? err.message : 'Failed to create');
    } finally {
      setLoading(false);
    }
  };

  const showHistoricalOption = undecryptedCount > 0;

  return (
    <Dialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          resetForm();
          onClose();
        }
      }}
    >
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>New Conversation</DialogTitle>
          <DialogDescription className="sr-only">
            {tab === 'new-contact' && 'Add a new contact by entering their name and public key'}
            {tab === 'new-channel' && 'Create a private channel with a shared encryption key'}
            {tab === 'hashtag' && 'Join a public hashtag channel'}
            {tab === 'bulk-hashtag' && 'Paste multiple hashtag channels to add them in one batch'}
          </DialogDescription>
        </DialogHeader>

        <Tabs
          value={tab}
          onValueChange={(value) => {
            setTab(value as Tab);
            resetForm();
          }}
          className="w-full"
        >
          <TabsList
            className={
              showBulkAddChannelTab ? 'grid w-full grid-cols-4' : 'grid w-full grid-cols-3'
            }
          >
            <TabsTrigger value="new-contact">Contact</TabsTrigger>
            <TabsTrigger value="new-channel">Private Channel</TabsTrigger>
            <TabsTrigger value="hashtag">Hashtag Channel</TabsTrigger>
            {showBulkAddChannelTab && (
              <TabsTrigger value="bulk-hashtag">Bulk Add Channel</TabsTrigger>
            )}
          </TabsList>

          <TabsContent value="new-contact" className="mt-4 space-y-4">
            <div className="space-y-2">
              <Label htmlFor="contact-name">Name</Label>
              <Input
                id="contact-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Contact name"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="contact-key">Public Key</Label>
              <Input
                id="contact-key"
                value={contactKey}
                onChange={(e) => setContactKey(e.target.value)}
                placeholder="64-character hex public key"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="contact-type">Type</Label>
              <select
                id="contact-type"
                value={contactType}
                onChange={(e) => setContactType(Number(e.target.value))}
                className="block h-9 w-full rounded-md border border-input bg-background px-3 text-sm shadow-sm"
              >
                <option value={1}>Client</option>
                <option value={2}>Repeater</option>
                <option value={3}>Room Server</option>
              </select>
            </div>
          </TabsContent>

          <TabsContent value="new-channel" className="mt-4 space-y-4">
            <div className="space-y-2">
              <Label htmlFor="channel-name">Channel Name</Label>
              <Input
                id="channel-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Channel name"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="channel-key">Channel Key</Label>
              <div className="flex gap-2">
                <Input
                  id="channel-key"
                  value={channelKey}
                  onChange={(e) => setChannelKey(e.target.value)}
                  placeholder="Pre-shared key (hex)"
                  className="flex-1"
                />
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={() => {
                    const bytes = new Uint8Array(16);
                    crypto.getRandomValues(bytes);
                    const hex = Array.from(bytes)
                      .map((byte) => byte.toString(16).padStart(2, '0'))
                      .join('');
                    setChannelKey(hex);
                  }}
                  title="Generate random key"
                  aria-label="Generate random key"
                >
                  <Dice5 className="h-4 w-4" aria-hidden="true" />
                </Button>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="hashtag" className="mt-4">
            <div className="space-y-2">
              <Label htmlFor="hashtag-name">Hashtag Channel</Label>
              <div className="flex items-center gap-1">
                <span className="text-sm text-muted-foreground">#</span>
                <Input
                  ref={hashtagInputRef}
                  id="hashtag-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="channel-name"
                  className="flex-1"
                />
              </div>
            </div>
            <div className="mt-3 space-y-1">
              <label className="flex cursor-pointer items-center gap-3">
                <input
                  type="checkbox"
                  checked={permitCapitals}
                  onChange={(e) => setPermitCapitals(e.target.checked)}
                  className="h-4 w-4 rounded border-input accent-primary"
                />
                <span className="text-sm">Permit capitals in channel key derivation</span>
              </label>
              <p className="pl-7 text-xs text-muted-foreground">
                Not recommended; most companions normalize to lowercase
              </p>
            </div>
          </TabsContent>

          {showBulkAddChannelTab && (
            <TabsContent value="bulk-hashtag" className="mt-4 space-y-4">
              <div className="space-y-2">
                <Label htmlFor="bulk-hashtag-names">Bulk Add Channel</Label>
                <textarea
                  ref={bulkTextareaRef}
                  id="bulk-hashtag-names"
                  aria-label="Bulk channel names"
                  value={bulkChannelText}
                  onChange={(e) => setBulkChannelText(e.target.value)}
                  placeholder={'#ops\nmesh-chat\nanother-channel'}
                  className="min-h-48 w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
                />
                <p className="text-xs text-muted-foreground">
                  Paste channel names separated by lines, spaces, or commas. Leading # marks are
                  stripped automatically.
                </p>
              </div>
              <div className="space-y-1">
                <label className="flex cursor-pointer items-center gap-3">
                  <input
                    type="checkbox"
                    checked={permitCapitals}
                    onChange={(e) => setPermitCapitals(e.target.checked)}
                    className="h-4 w-4 rounded border-input accent-primary"
                  />
                  <span className="text-sm">Permit capitals in channel key derivation</span>
                </label>
                <p className="pl-7 text-xs text-muted-foreground">
                  Not recommended; most companions normalize to lowercase
                </p>
              </div>
            </TabsContent>
          )}
        </Tabs>

        {showHistoricalOption && (
          <div className="space-y-1">
            <div className="flex items-center justify-end space-x-2">
              <Label
                htmlFor="try-historical"
                className="cursor-pointer text-sm text-muted-foreground"
              >
                Try decrypting {undecryptedCount.toLocaleString()} stored packet
                {undecryptedCount !== 1 ? 's' : ''}
              </Label>
              <Checkbox
                id="try-historical"
                checked={tryHistorical}
                onCheckedChange={(checked) => setTryHistorical(checked === true)}
              />
            </div>
            {tryHistorical && (
              <p className="text-right text-xs text-muted-foreground">
                Messages will stream in as they decrypt in the background
              </p>
            )}
          </div>
        )}

        {error && (
          <div className="text-sm text-destructive" role="alert">
            {error}
          </div>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => {
              resetForm();
              onClose();
            }}
          >
            Cancel
          </Button>
          {tab === 'hashtag' && (
            <Button variant="secondary" onClick={handleCreateAndAddAnother} disabled={loading}>
              {loading ? 'Creating...' : 'Create & Add Another'}
            </Button>
          )}
          <Button onClick={handleCreate} disabled={loading}>
            {loading
              ? tab === 'bulk-hashtag'
                ? 'Adding...'
                : 'Creating...'
              : tab === 'bulk-hashtag'
                ? 'Add Channels'
                : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
