import { useEffect, useState } from 'react';

/**
 * Returns `false` until `open` has been continuously true for `delayMs`, then
 * `true`; resets to `false` whenever `open` becomes false.
 *
 * Used to defer mounting layout-measuring children (notably Recharts
 * `ResponsiveContainer`) inside sliding drawers until the entrance animation has
 * settled. Mounting a `ResponsiveContainer` while its box is still being
 * transformed drives a `ResizeObserver` -> setState storm that Safari resolves
 * into React error #185 ("Maximum update depth exceeded"), blanking the page.
 * The default delay matches the `Sheet` open animation (`duration-500`) plus a
 * small buffer. See issue #317.
 */
export function useEntranceSettled(open: boolean, delayMs = 550): boolean {
  const [settled, setSettled] = useState(false);

  useEffect(() => {
    if (!open) {
      setSettled(false);
      return;
    }
    const id = window.setTimeout(() => setSettled(true), delayMs);
    return () => window.clearTimeout(id);
  }, [open, delayMs]);

  return settled;
}
