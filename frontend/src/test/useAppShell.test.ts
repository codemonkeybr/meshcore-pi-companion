import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useAppShell } from '../hooks/useAppShell';

describe('useAppShell', () => {
  let originalHash: string;

  beforeEach(() => {
    originalHash = window.location.hash;
  });

  afterEach(() => {
    window.location.hash = originalHash;
  });

  it('opens new-message modal and closes the sidebar', () => {
    const { result } = renderHook(() => useAppShell());

    act(() => {
      result.current.setSidebarOpen(true);
      result.current.handleOpenNewMessage();
    });

    expect(result.current.showNewMessage).toBe(true);
    expect(result.current.sidebarOpen).toBe(false);
  });

  it('toggles settings mode and closes the sidebar', () => {
    const { result } = renderHook(() => useAppShell());

    act(() => {
      result.current.setSidebarOpen(true);
      result.current.handleToggleSettingsView();
    });

    expect(result.current.showSettings).toBe(true);
    expect(result.current.sidebarOpen).toBe(false);

    act(() => {
      result.current.handleCloseSettingsView();
    });

    expect(result.current.showSettings).toBe(false);
  });

  it('initializes settings mode from the URL hash', () => {
    window.location.hash = '#settings/database';

    const { result } = renderHook(() => useAppShell());

    expect(result.current.showSettings).toBe(true);
    expect(result.current.settingsSection).toBe('database');
  });

  it('syncs the selected settings section into the URL hash', async () => {
    const { result } = renderHook(() => useAppShell());

    act(() => {
      result.current.handleToggleSettingsView();
    });

    await waitFor(() => {
      expect(window.location.hash).toBe('#settings/radio');
    });

    act(() => {
      result.current.setSettingsSection('fanout');
    });

    await waitFor(() => {
      expect(window.location.hash).toBe('#settings/fanout');
    });
  });

  it('restores the previous hash when settings close', async () => {
    window.location.hash = '#channel/test/Public';

    const { result } = renderHook(() => useAppShell());

    act(() => {
      result.current.handleToggleSettingsView();
    });

    await waitFor(() => {
      expect(window.location.hash).toBe('#settings/radio');
    });

    act(() => {
      result.current.handleCloseSettingsView();
    });

    expect(window.location.hash).toBe('#channel/test/Public');
  });

  it('toggles the cracker shell without affecting sidebar state', () => {
    const { result } = renderHook(() => useAppShell());

    act(() => {
      result.current.setSidebarOpen(true);
      result.current.handleToggleCracker();
    });

    expect(result.current.showCracker).toBe(true);
    expect(result.current.sidebarOpen).toBe(true);
  });
});
