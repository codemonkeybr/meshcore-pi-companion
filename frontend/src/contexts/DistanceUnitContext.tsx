import { createContext, useContext, type ReactNode } from 'react';

import type { DistanceUnit } from '../utils/distanceUnits';

interface DistanceUnitContextValue {
  distanceUnit: DistanceUnit;
  setDistanceUnit: (unit: DistanceUnit) => void;
}

const noop = () => {};

const DistanceUnitContext = createContext<DistanceUnitContextValue>({
  distanceUnit: 'metric',
  setDistanceUnit: noop,
});

export function DistanceUnitProvider({
  distanceUnit,
  setDistanceUnit,
  children,
}: DistanceUnitContextValue & { children: ReactNode }) {
  return (
    <DistanceUnitContext.Provider value={{ distanceUnit, setDistanceUnit }}>
      {children}
    </DistanceUnitContext.Provider>
  );
}

export function useDistanceUnit() {
  return useContext(DistanceUnitContext);
}
