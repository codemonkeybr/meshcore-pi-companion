import { startTransition, useCallback, useEffect, useRef, useState } from 'react';

import { getLocalLabel, type LocalLabel } from '../utils/localLabel';
import { getSavedDistanceUnit, type DistanceUnit } from '../utils/distanceUnits';
import type { SettingsSection } from '../components/settings/settingsConstants';
import { parseHashSettingsSection, updateSettingsHash } from '../utils/urlHash';

interface UseAppShellResult {
  showNewMessage: boolean;
  showSettings: boolean;
  settingsSection: SettingsSection;
  sidebarOpen: boolean;
  showCracker: boolean;
  crackerRunning: boolean;
  localLabel: LocalLabel;
  distanceUnit: DistanceUnit;
  setSettingsSection: (section: SettingsSection) => void;
  setSidebarOpen: (open: boolean) => void;
  setCrackerRunning: (running: boolean) => void;
  setLocalLabel: (label: LocalLabel) => void;
  setDistanceUnit: (unit: DistanceUnit) => void;
  handleCloseSettingsView: () => void;
  handleToggleSettingsView: () => void;
  handleOpenNewMessage: () => void;
  handleCloseNewMessage: () => void;
  handleToggleCracker: () => void;
}

export function useAppShell(): UseAppShellResult {
  const initialSettingsSection = typeof window === 'undefined' ? null : parseHashSettingsSection();
  const [showNewMessage, setShowNewMessage] = useState(false);
  const [showSettings, setShowSettings] = useState(() => initialSettingsSection !== null);
  const [settingsSection, setSettingsSection] = useState<SettingsSection>(
    () => initialSettingsSection ?? 'radio'
  );
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showCracker, setShowCracker] = useState(false);
  const [crackerRunning, setCrackerRunning] = useState(false);
  const [localLabel, setLocalLabel] = useState(getLocalLabel);
  const [distanceUnit, setDistanceUnit] = useState(getSavedDistanceUnit);
  const previousHashRef = useRef('');

  useEffect(() => {
    if (showSettings) {
      updateSettingsHash(settingsSection);
    }
  }, [settingsSection, showSettings]);

  const handleCloseSettingsView = useCallback(() => {
    if (typeof window !== 'undefined' && parseHashSettingsSection() !== null) {
      window.history.replaceState(null, '', previousHashRef.current || window.location.pathname);
    }
    startTransition(() => setShowSettings(false));
    setSidebarOpen(false);
  }, []);

  const handleToggleSettingsView = useCallback(() => {
    if (showSettings) {
      handleCloseSettingsView();
      return;
    }

    if (typeof window !== 'undefined') {
      previousHashRef.current =
        parseHashSettingsSection() === null ? window.location.hash : previousHashRef.current;
    }
    startTransition(() => {
      setShowSettings(true);
    });
    setSidebarOpen(false);
  }, [handleCloseSettingsView, showSettings]);

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
    distanceUnit,
    setSettingsSection,
    setSidebarOpen,
    setCrackerRunning,
    setLocalLabel,
    setDistanceUnit,
    handleCloseSettingsView,
    handleToggleSettingsView,
    handleOpenNewMessage,
    handleCloseNewMessage,
    handleToggleCracker,
  };
}
