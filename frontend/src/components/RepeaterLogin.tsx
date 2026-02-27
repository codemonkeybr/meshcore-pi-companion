import { useState, useCallback, type FormEvent } from 'react';
import { Input } from './ui/input';
import { Button } from './ui/button';

interface RepeaterLoginProps {
  repeaterName: string;
  loading: boolean;
  error: string | null;
  onLogin: (password: string) => Promise<void>;
  onLoginAsGuest: () => Promise<void>;
}

export function RepeaterLogin({
  repeaterName,
  loading,
  error,
  onLogin,
  onLoginAsGuest,
}: RepeaterLoginProps) {
  const [password, setPassword] = useState('');

  const handleSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      if (loading) return;
      await onLogin(password.trim());
    },
    [password, loading, onLogin]
  );

  return (
    <div className="flex-1 flex items-center justify-center p-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center space-y-1">
          <h2 className="text-lg font-semibold">{repeaterName}</h2>
          <p className="text-sm text-muted-foreground">Log in to access repeater dashboard</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4" autoComplete="off">
          <Input
            type="password"
            autoComplete="off"
            name="repeater-password"
            data-lpignore="true"
            data-1p-ignore="true"
            data-bwignore="true"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Repeater password..."
            disabled={loading}
            autoFocus
          />

          {error && <p className="text-sm text-destructive text-center">{error}</p>}

          <div className="flex flex-col gap-2">
            <Button type="submit" disabled={loading} className="w-full">
              {loading ? 'Logging in...' : 'Login with Password'}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={loading}
              className="w-full"
              onClick={onLoginAsGuest}
            >
              Login as Guest / ACLs
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
