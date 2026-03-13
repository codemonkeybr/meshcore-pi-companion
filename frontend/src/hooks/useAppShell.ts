import { startTransition, useCallback, useState } from 'react';

import { getLocalLabel, type LocalLabel } from '../utils/localLabel';
import type { SettingsSection } from '../components/settings/settingsConstants';

interface UseAppShellResult {
  showNewMessage: boolean;
  showSettings: boolean;
  settingsSection: SettingsSection;
  sidebarOpen: boolean;
  showCracker: boolean;
  crackerRunning: boolean;
  localLabel: LocalLabel;
  setSettingsSection: (section: SettingsSection) => void;
  setSidebarOpen: (open: boolean) => void;
  setCrackerRunning: (running: boolean) => void;
  setLocalLabel: (label: LocalLabel) => void;
  handleCloseSettingsView: () => void;
  handleToggleSettingsView: () => void;
  handleOpenNewMessage: () => void;
  handleCloseNewMessage: () => void;
  handleToggleCracker: () => void;
}

export function useAppShell(): UseAppShellResult {
  const [showNewMessage, setShowNewMessage] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [settingsSection, setSettingsSection] = useState<SettingsSection>('radio');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showCracker, setShowCracker] = useState(false);
  const [crackerRunning, setCrackerRunning] = useState(false);
  const [localLabel, setLocalLabel] = useState(getLocalLabel);

  const handleCloseSettingsView = useCallback(() => {
    startTransition(() => setShowSettings(false));
    setSidebarOpen(false);
  }, []);

  const handleToggleSettingsView = useCallback(() => {
    startTransition(() => {
      setShowSettings((prev) => !prev);
    });
    setSidebarOpen(false);
  }, []);

  const handleOpenNewMessage = useCallback(() => {
    setShowNewMessage(true);
    setSidebarOpen(false);
  }, []);

  const handleCloseNewMessage = useCallback(() => {
    setShowNewMessage(false);
  }, []);

  const handleToggleCracker = useCallback(() => {
    setShowCracker((prev) => !prev);
  }, []);

  return {
    showNewMessage,
    showSettings,
    settingsSection,
    sidebarOpen,
    showCracker,
    crackerRunning,
    localLabel,
    setSettingsSection,
    setSidebarOpen,
    setCrackerRunning,
    setLocalLabel,
    handleCloseSettingsView,
    handleToggleSettingsView,
    handleOpenNewMessage,
    handleCloseNewMessage,
    handleToggleCracker,
  };
}
