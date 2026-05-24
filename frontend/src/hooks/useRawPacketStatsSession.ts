import { useCallback, useState } from 'react';

import type { RawPacket } from '../types';
import {
  MAX_RAW_PACKET_STATS_OBSERVATIONS,
  summarizeRawPacketForStats,
  type RawPacketStatsSessionState,
} from '../utils/rawPacketStats';

export function useRawPacketStatsSession() {
  const [session, setSession] = useState<RawPacketStatsSessionState>(() => ({
    sessionStartedAt: Date.now(),
    totalObservedPackets: 0,
    trimmedObservationCount: 0,
    observations: [],
  }));

  const recordRawPacketObservation = useCallback((packet: RawPacket) => {
    setSession((prev) => {
      const observation = summarizeRawPacketForStats(packet);
      if (
        prev.observations.some(
          (candidate) => candidate.observationKey === observation.observationKey
        )
      ) {
        return prev;
      }

      const observations = [...prev.observations, observation];
      if (observations.length <= MAX_RAW_PACKET_STATS_OBSERVATIONS) {
        return {
          ...prev,
          totalObservedPackets: prev.totalObservedPackets + 1,
          observations,
        };
      }

      const overflow = observations.length - MAX_RAW_PACKET_STATS_OBSERVATIONS;
      return {
        ...prev,
        totalObservedPackets: prev.totalObservedPackets + 1,
        trimmedObservationCount: prev.trimmedObservationCount + overflow,
        observations: observations.slice(overflow),
      };
    });
  }, []);

  return {
    rawPacketStatsSession: session,
    recordRawPacketObservation,
  };
}
