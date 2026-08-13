import { createContext, useContext, type ReactNode } from 'react';

interface RichPayloadContextValue {
  renderRichPayloads: boolean;
  setRenderRichPayloads: (enabled: boolean) => void;
}

const noop = () => {};

const RichPayloadContext = createContext<RichPayloadContextValue>({
  renderRichPayloads: false,
  setRenderRichPayloads: noop,
});

export function RichPayloadProvider({
  renderRichPayloads,
  setRenderRichPayloads,
  children,
}: RichPayloadContextValue & { children: ReactNode }) {
  return (
    <RichPayloadContext.Provider value={{ renderRichPayloads, setRenderRichPayloads }}>
      {children}
    </RichPayloadContext.Provider>
  );
}

export function useRichPayloads() {
  return useContext(RichPayloadContext);
}
