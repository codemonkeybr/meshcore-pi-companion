import { useCallback, useEffect, useMemo, useState } from 'react';

type ServerLoginKind = 'repeater' | 'room';

type StoredPassword = {
  password: string;
};

const STORAGE_KEY_PREFIX = 'remoteterm-server-password';
const inMemoryPasswords = new Map<string, StoredPassword>();

function getStorageKey(kind: ServerLoginKind, publicKey: string): string {
  return `${STORAGE_KEY_PREFIX}:${kind}:${publicKey}`;
}

function loadStoredPassword(kind: ServerLoginKind, publicKey: string): StoredPassword | null {
  try {
    const raw = localStorage.getItem(getStorageKey(kind, publicKey));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredPassword>;
    if (typeof parsed.password !== 'string' || parsed.password.length === 0) {
      return null;
    }
    return { password: parsed.password };
  } catch {
    return null;
  }
}

export function useRememberedServerPassword(kind: ServerLoginKind, publicKey: string) {
  const storageKey = useMemo(() => getStorageKey(kind, publicKey), [kind, publicKey]);
  const [password, setPassword] = useState('');
  const [rememberPassword, setRememberPassword] = useState(false);

  useEffect(() => {
    const stored = loadStoredPassword(kind, publicKey);
    if (stored) {
      setPassword(stored.password);
      setRememberPassword(true);
      return;
    }

    const inMemoryStored = inMemoryPasswords.get(storageKey);
    if (inMemoryStored) {
      setPassword(inMemoryStored.password);
      setRememberPassword(false);
      return;
    }

    setPassword('');
    setRememberPassword(false);
  }, [kind, publicKey, storageKey]);

  const persistAfterLogin = useCallback(
    (submittedPassword: string) => {
      const trimmedPassword = submittedPassword.trim();
      if (!trimmedPassword) {
        return;
      }

      inMemoryPasswords.set(storageKey, { password: trimmedPassword });

      if (!rememberPassword) {
        try {
          localStorage.removeItem(storageKey);
        } catch {
          // localStorage may be unavailable
        }
      } else {
        try {
          localStorage.setItem(storageKey, JSON.stringify({ password: trimmedPassword }));
        } catch {
          // localStorage may be unavailable
        }
      }

      setPassword(trimmedPassword);
    },
    [rememberPassword, storageKey]
  );

  return {
    password,
    setPassword,
    rememberPassword,
    setRememberPassword,
    persistAfterLogin,
  };
}
