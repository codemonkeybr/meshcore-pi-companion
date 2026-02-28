import { useState, useCallback, useEffect, useRef } from 'react';
import { api } from '../api';
import { takePrefetchOrFetch } from '../prefetch';
import { toast } from '../components/ui/sonner';
import type { HealthStatus, RadioConfig, RadioConfigUpdate } from '../types';

export function useRadioControl() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [config, setConfig] = useState<RadioConfig | null>(null);

  const prevHealthRef = useRef<HealthStatus | null>(null);
  const rebootPollTokenRef = useRef(0);

  // Cancel any in-flight reboot polling on unmount
  useEffect(() => {
    return () => {
      rebootPollTokenRef.current += 1;
    };
  }, []);

  const fetchConfig = useCallback(async () => {
    try {
      const data = await takePrefetchOrFetch('config', api.getRadioConfig);
      setConfig(data);
    } catch (err) {
      console.error('Failed to fetch config:', err);
    }
  }, []);

  const handleSaveConfig = useCallback(
    async (update: RadioConfigUpdate) => {
      await api.updateRadioConfig(update);
      await fetchConfig();
    },
    [fetchConfig]
  );

  const handleSetPrivateKey = useCallback(
    async (key: string) => {
      await api.setPrivateKey(key);
      await fetchConfig();
    },
    [fetchConfig]
  );

  const handleReboot = useCallback(async () => {
    await api.rebootRadio();
    setHealth((prev) => (prev ? { ...prev, radio_connected: false } : prev));
    const pollToken = ++rebootPollTokenRef.current;
    const pollUntilReconnected = async () => {
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        if (rebootPollTokenRef.current !== pollToken) return;
        try {
          const data = await api.getHealth();
          if (rebootPollTokenRef.current !== pollToken) return;
          setHealth(data);
          if (data.radio_connected) {
            fetchConfig();
            return;
          }
        } catch {
          // Keep polling
        }
      }
    };
    pollUntilReconnected();
  }, [fetchConfig]);

  const handleAdvertise = useCallback(async () => {
    try {
      await api.sendAdvertisement();
      toast.success('Advertisement sent');
    } catch (err) {
      console.error('Failed to send advertisement:', err);
      toast.error('Failed to send advertisement', {
        description: err instanceof Error ? err.message : 'Check radio connection',
      });
    }
  }, []);

  const handleHealthRefresh = useCallback(async () => {
    try {
      const data = await api.getHealth();
      setHealth(data);
    } catch (err) {
      console.error('Failed to refresh health:', err);
    }
  }, []);

  return {
    health,
    setHealth,
    config,
    setConfig,
    prevHealthRef,
    fetchConfig,
    handleSaveConfig,
    handleSetPrivateKey,
    handleReboot,
    handleAdvertise,
    handleHealthRefresh,
  };
}
