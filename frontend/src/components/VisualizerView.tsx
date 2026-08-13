import { useCallback, useEffect, useRef, useState } from 'react';
import { Maximize2, Minimize2 } from 'lucide-react';
import type { Channel, Contact, RawPacket, RadioConfig } from '../types';
import { PacketVisualizer3D } from './PacketVisualizer3D';
import { RawPacketList } from './RawPacketList';
import { RawPacketInspectorDialog } from './RawPacketDetailModal';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { cn } from '@/lib/utils';
import { getVisualizerSettings, saveVisualizerSettings } from '../utils/visualizerSettings';
import { useRawPackets } from '../stores/rawPacketStore';

interface VisualizerViewProps {
  contacts: Contact[];
  channels: Channel[];
  config: RadioConfig | null;
}

export function VisualizerView({ contacts, channels, config }: VisualizerViewProps) {
  const packets = useRawPackets();
  const [fullScreen, setFullScreen] = useState(() => getVisualizerSettings().hidePacketFeed);
  const [paneFullScreen, setPaneFullScreen] = useState(false);
  const [selectedPacket, setSelectedPacket] = useState<RawPacket | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Persist packet feed visibility to localStorage
  useEffect(() => {
    const current = getVisualizerSettings();
    if (current.hidePacketFeed !== fullScreen) {
      saveVisualizerSettings({ ...current, hidePacketFeed: fullScreen });
    }
  }, [fullScreen]);

  // Sync state when browser exits fullscreen (Escape, F11, etc.)
  useEffect(() => {
    const handler = () => {
      if (!document.fullscreenElement) setPaneFullScreen(false);
    };
    document.addEventListener('fullscreenchange', handler);
    return () => document.removeEventListener('fullscreenchange', handler);
  }, []);

  const toggleFullScreen = useCallback(() => {
    if (!document.fullscreenElement) {
      containerRef.current?.requestFullscreen();
      setPaneFullScreen(true);
    } else {
      document.exitFullscreen();
      // State synced via fullscreenchange handler
    }
  }, []);

  return (
    <div ref={containerRef} className="flex flex-col h-full bg-background">
      {/* Header */}
      <div className="flex justify-between items-center px-4 py-3 border-b border-border font-medium text-lg">
        <span>{paneFullScreen ? 'RemoteTerm MeshCore Visualizer' : 'Mesh Visualizer'}</span>
        <button
          className="hidden md:inline-flex items-center justify-center rounded-md p-1.5 text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={toggleFullScreen}
          title={paneFullScreen ? 'Exit fullscreen' : 'Fullscreen'}
          aria-label={paneFullScreen ? 'Exit fullscreen' : 'Enter fullscreen'}
        >
          {paneFullScreen ? <Minimize2 size={18} /> : <Maximize2 size={18} />}
        </button>
      </div>

      {/* Mobile: Tabbed interface */}
      <div className="flex-1 overflow-hidden md:hidden">
        <Tabs defaultValue="visualizer" className="h-full flex flex-col">
          <TabsList className="mx-4 mt-2 grid grid-cols-2">
            <TabsTrigger value="visualizer">Visualizer</TabsTrigger>
            <TabsTrigger value="packets">Packet Feed</TabsTrigger>
          </TabsList>
          <TabsContent value="visualizer" className="flex-1 m-0 overflow-hidden">
            <PacketVisualizer3D packets={packets} contacts={contacts} config={config} />
          </TabsContent>
          <TabsContent value="packets" className="flex-1 m-0 overflow-hidden">
            <RawPacketList
              packets={packets}
              channels={channels}
              onPacketClick={setSelectedPacket}
            />
          </TabsContent>
        </Tabs>
      </div>

      {/* Desktop: Split screen (or full screen if toggled) */}
      <div className="hidden md:flex flex-1 overflow-hidden">
        {/* Visualizer panel */}
        <div
          className={cn(
            'overflow-hidden transition-all duration-200',
            fullScreen ? 'flex-1' : 'flex-1 border-r border-border'
          )}
        >
          <PacketVisualizer3D
            packets={packets}
            contacts={contacts}
            config={config}
            fullScreen={fullScreen}
            onFullScreenChange={setFullScreen}
          />
        </div>

        {/* Packet feed panel - hidden when full screen */}
        <div
          className={cn(
            'overflow-hidden transition-all duration-200',
            fullScreen ? 'w-0' : 'w-[31rem] lg:w-[38rem]'
          )}
        >
          <div className="h-full flex flex-col">
            <div className="px-3 py-2 border-b border-border text-sm font-medium text-muted-foreground">
              Packet Feed
            </div>
            <div className="flex-1 overflow-hidden">
              <RawPacketList
                packets={packets}
                channels={channels}
                onPacketClick={setSelectedPacket}
              />
            </div>
          </div>
        </div>
      </div>

      {/* While natively fullscreened, portal into the fullscreen element so the
          dialog stays visible; otherwise keep the default document.body target. */}
      <RawPacketInspectorDialog
        open={selectedPacket !== null}
        onOpenChange={(isOpen) => !isOpen && setSelectedPacket(null)}
        channels={channels}
        container={paneFullScreen ? containerRef.current : undefined}
        source={
          selectedPacket
            ? { kind: 'packet', packet: selectedPacket }
            : { kind: 'loading', message: 'Loading packet...' }
        }
        title="Packet Details"
        description="Detailed byte and field breakdown for the selected raw packet."
      />
    </div>
  );
}
