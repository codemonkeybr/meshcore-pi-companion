import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { useRememberedServerPassword } from '../hooks/useRememberedServerPassword';

describe('useRememberedServerPassword', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('restores the last in-memory password when local remember is disabled', () => {
    const { result, unmount } = renderHook(() =>
      useRememberedServerPassword('room', 'aa'.repeat(32))
    );

    act(() => {
      result.current.setPassword('room-secret');
      result.current.persistAfterLogin('room-secret');
    });

    expect(result.current.password).toBe('room-secret');
    unmount();

    const { result: remounted } = renderHook(() =>
      useRememberedServerPassword('room', 'aa'.repeat(32))
    );

    expect(remounted.current.password).toBe('room-secret');
    expect(remounted.current.rememberPassword).toBe(false);
  });
});
