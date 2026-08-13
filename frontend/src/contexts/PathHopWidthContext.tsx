import { createContext, useContext, type ReactNode } from 'react';

interface PathHopWidthContextValue {
  showPathHopWidth: boolean;
  setShowPathHopWidth: (enabled: boolean) => void;
}

const noop = () => {};

const PathHopWidthContext = createContext<PathHopWidthContextValue>({
  showPathHopWidth: false,
  setShowPathHopWidth: noop,
});

export function PathHopWidthProvider({
  showPathHopWidth,
  setShowPathHopWidth,
  children,
}: PathHopWidthContextValue & { children: ReactNode }) {
  return (
    <PathHopWidthContext.Provider value={{ showPathHopWidth, setShowPathHopWidth }}>
      {children}
    </PathHopWidthContext.Provider>
  );
}

export function usePathHopWidth() {
  return useContext(PathHopWidthContext);
}
