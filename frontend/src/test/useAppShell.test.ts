import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { useAppShell } from '../hooks/useAppShell';

describe('useAppShell', () => {
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
