import { lazy, Suspense, useRef, type ComponentProps } from 'react';

import { StatusBar } from './StatusBar';
import { Sidebar } from './Sidebar';
import { ConversationPane } from './ConversationPane';
import { NewMessageModal } from './NewMessageModal';
import { ContactInfoPane } from './ContactInfoPane';
import { ChannelInfoPane } from './ChannelInfoPane';
import { Toaster } from './ui/sonner';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from './ui/sheet';
import {
  SETTINGS_SECTION_ICONS,
  SETTINGS_SECTION_LABELS,
  SETTINGS_SECTION_ORDER,
  type SettingsSection,
} from './settings/settingsConstants';
import { getContrastTextColor, type LocalLabel } from '../utils/localLabel';
import type { CrackerPanelProps } from './CrackerPanel';
import type { SearchViewProps } from './SearchView';
import type { SettingsModalProps } from './SettingsModal';
import { cn } from '@/lib/utils';

const SettingsModal = lazy(() =>
  import('./SettingsModal').then((m) => ({ default: m.SettingsModal }))
);
const CrackerPanel = lazy(() =>
  import('./CrackerPanel').then((m) => ({ default: m.CrackerPanel }))
);
const SearchView = lazy(() => import('./SearchView').then((m) => ({ default: m.SearchView })));

type SidebarProps = ComponentProps<typeof Sidebar>;
type ConversationPaneProps = ComponentProps<typeof ConversationPane>;
type NewMessageModalProps = Omit<ComponentProps<typeof NewMessageModal>, 'open' | 'onClose'>;
type ContactInfoPaneProps = ComponentProps<typeof ContactInfoPane>;
type ChannelInfoPaneProps = ComponentProps<typeof ChannelInfoPane>;

interface AppShellProps {
  localLabel: LocalLabel;
  showNewMessage: boolean;
  showSettings: boolean;
  settingsSection: SettingsSection;
  sidebarOpen: boolean;
  showCracker: boolean;
  onSettingsSectionChange: (section: SettingsSection) => void;
  onSidebarOpenChange: (open: boolean) => void;
  onCrackerRunningChange: (running: boolean) => void;
  onToggleSettingsView: () => void;
  onCloseSettingsView: () => void;
  onCloseNewMessage: () => void;
  onLocalLabelChange: (label: LocalLabel) => void;
  statusProps: Pick<ComponentProps<typeof StatusBar>, 'health' | 'config'>;
  sidebarProps: SidebarProps;
  conversationPaneProps: ConversationPaneProps;
  searchProps: SearchViewProps;
  settingsProps: Omit<
    SettingsModalProps,
    'open' | 'pageMode' | 'externalSidebarNav' | 'desktopSection' | 'onClose' | 'onLocalLabelChange'
  >;
  crackerProps: Omit<CrackerPanelProps, 'visible' | 'onRunningChange'>;
  newMessageModalProps: NewMessageModalProps;
  contactInfoPaneProps: ContactInfoPaneProps;
  channelInfoPaneProps: ChannelInfoPaneProps;
}

export function AppShell({
  localLabel,
  showNewMessage,
  showSettings,
  settingsSection,
  sidebarOpen,
  showCracker,
  onSettingsSectionChange,
  onSidebarOpenChange,
  onCrackerRunningChange,
  onToggleSettingsView,
  onCloseSettingsView,
  onCloseNewMessage,
  onLocalLabelChange,
  statusProps,
  sidebarProps,
  conversationPaneProps,
  searchProps,
  settingsProps,
  crackerProps,
  newMessageModalProps,
  contactInfoPaneProps,
  channelInfoPaneProps,
}: AppShellProps) {
  const searchMounted = useRef(false);
  if (conversationPaneProps.activeConversation?.type === 'search') {
    searchMounted.current = true;
  }

  const crackerMounted = useRef(false);
  if (showCracker) {
    crackerMounted.current = true;
  }

  const settingsSidebarContent = (
    <nav
      className="sidebar w-60 h-full min-h-0 overflow-hidden bg-card border-r border-border flex flex-col"
      aria-label="Settings"
    >
      <div className="flex justify-between items-center px-3 py-2.5 border-b border-border">
        <h2 className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium">
          Settings
        </h2>
        <button
          type="button"
          onClick={onCloseSettingsView}
          className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-status-connected/15 border border-status-connected/30 text-status-connected hover:bg-status-connected/25 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          title="Back to conversations"
          aria-label="Back to conversations"
        >
          &larr; Back to Chat
        </button>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto py-1 [contain:layout_paint]">
        {SETTINGS_SECTION_ORDER.map((section) => {
          const Icon = SETTINGS_SECTION_ICONS[section];
          return (
            <button
              key={section}
              type="button"
              className={cn(
                'w-full px-3 py-2 text-left text-[13px] border-l-2 border-transparent hover:bg-accent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
                settingsSection === section && 'bg-accent border-l-primary'
              )}
              aria-current={settingsSection === section ? 'true' : undefined}
              onClick={() => onSettingsSectionChange(section)}
            >
              <span className="flex items-center gap-2">
                <Icon className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                <span>{SETTINGS_SECTION_LABELS[section]}</span>
              </span>
            </button>
          );
        })}
      </div>
    </nav>
  );

  const activeSidebarContent = showSettings ? (
    settingsSidebarContent
  ) : (
    <Sidebar {...sidebarProps} />
  );

  return (
    <div className="flex flex-col h-full">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:p-2 focus:bg-primary focus:text-primary-foreground"
      >
        Skip to content
      </a>
      {localLabel.text && (
        <div
          style={{
            backgroundColor: localLabel.color,
            color: getContrastTextColor(localLabel.color),
          }}
          className="px-4 py-1 text-center text-sm font-medium"
        >
          {localLabel.text}
        </div>
      )}

      <StatusBar
        health={statusProps.health}
        config={statusProps.config}
        settingsMode={showSettings}
        onSettingsClick={onToggleSettingsView}
        onMenuClick={showSettings ? undefined : () => onSidebarOpenChange(true)}
      />

      <div className="flex flex-1 overflow-hidden">
        <div className="hidden md:block min-h-0 overflow-hidden">{activeSidebarContent}</div>

        <Sheet open={sidebarOpen} onOpenChange={onSidebarOpenChange}>
          <SheetContent
            side="left"
            className="w-[280px] p-0 flex flex-col"
            hideCloseButton
            onOpenAutoFocus={(event) => {
              event.preventDefault();
            }}
          >
            <SheetHeader className="sr-only">
              <SheetTitle>Navigation</SheetTitle>
              <SheetDescription>Sidebar navigation</SheetDescription>
            </SheetHeader>
            <div className="flex-1 overflow-hidden">{activeSidebarContent}</div>
          </SheetContent>
        </Sheet>

        <main id="main-content" className="flex-1 flex flex-col bg-background min-w-0">
          <div
            className={cn(
              'flex-1 flex flex-col min-h-0',
              (showSettings || conversationPaneProps.activeConversation?.type === 'search') &&
                'hidden'
            )}
          >
            <ConversationPane {...conversationPaneProps} />
          </div>

          {searchMounted.current && (
            <div
              className={cn(
                'flex-1 flex flex-col min-h-0',
                (conversationPaneProps.activeConversation?.type !== 'search' || showSettings) &&
                  'hidden'
              )}
            >
              <Suspense
                fallback={
                  <div className="flex-1 flex items-center justify-center text-muted-foreground">
                    Loading search...
                  </div>
                }
              >
                <SearchView {...searchProps} />
              </Suspense>
            </div>
          )}

          {showSettings && (
            <div className="flex-1 flex flex-col min-h-0">
              <div className="flex-1 min-h-0 overflow-hidden">
                <Suspense
                  fallback={
                    <div className="flex-1 flex items-center justify-center p-8 text-muted-foreground">
                      Loading settings...
                    </div>
                  }
                >
                  <SettingsModal
                    {...settingsProps}
                    open={showSettings}
                    pageMode
                    externalSidebarNav
                    desktopSection={settingsSection}
                    onClose={onCloseSettingsView}
                    onLocalLabelChange={onLocalLabelChange}
                  />
                </Suspense>
              </div>
            </div>
          )}
        </main>
      </div>

      <div
        ref={(el) => {
          if (showCracker && el) {
            const focusable = el.querySelector<HTMLElement>('input, button:not([disabled])');
            if (focusable) {
              setTimeout(() => focusable.focus(), 210);
            }
          }
        }}
        className={cn(
          'border-t border-border bg-background transition-all duration-200 overflow-hidden',
          showCracker ? 'h-[275px]' : 'h-0'
        )}
      >
        {crackerMounted.current && (
          <Suspense
            fallback={
              <div className="flex items-center justify-center h-full text-muted-foreground">
                Loading cracker...
              </div>
            }
          >
            <CrackerPanel
              {...crackerProps}
              visible={showCracker}
              onRunningChange={onCrackerRunningChange}
            />
          </Suspense>
        )}
      </div>

      <NewMessageModal
        {...newMessageModalProps}
        open={showNewMessage}
        onClose={onCloseNewMessage}
        onSelectConversation={(conv) => {
          newMessageModalProps.onSelectConversation(conv);
          onCloseNewMessage();
        }}
      />

      <ContactInfoPane {...contactInfoPaneProps} />
      <ChannelInfoPane {...channelInfoPaneProps} />
      <Toaster position="top-right" />
    </div>
  );
}
