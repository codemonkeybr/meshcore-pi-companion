import { useState, useCallback, useEffect, useRef } from 'react';
import { api } from '../api';
import { takePrefetchOrFetch } from '../prefetch';
import { toast } from '../components/ui/sonner';
import type {
  HealthStatus,
  RadioAdvertMode,
  RadioConfig,
  RadioConfigUpdate,
  RadioDiscoveryResponse,
  RadioDiscoveryTarget,
} from '../types';

export function useRadioControl() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [config, setConfig] = useState<RadioConfig | null>(null);
  const [meshDiscovery, setMeshDiscovery] = useState<RadioDiscoveryResponse | null>(null);
  const [meshDiscoveryLoadingTarget, setMeshDiscoveryLoadingTarget] =
    useState<RadioDiscoveryTarget | null>(null);

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
    setHealth((prev) =>
      prev ? { ...prev, radio_connected: false, radio_initializing: false } : prev
    );
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

  const handleDisconnect = useCallback(async () => {
    await api.disconnectRadio();
    const pausedHealth = await api.getHealth();
    setHealth(pausedHealth);
  }, []);

  const handleReconnect = useCallback(async () => {
    await api.reconnectRadio();
    const refreshedHealth = await api.getHealth();
    setHealth(refreshedHealth);
    if (refreshedHealth.radio_connected) {
      await fetchConfig();
    }
  }, [fetchConfig]);

  const handleAdvertise = useCallback(async (mode: RadioAdvertMode = 'flood') => {
    try {
      await api.sendAdvertisement(mode);
      toast.success(mode === 'zero_hop' ? 'Zero-hop advertisement sent' : 'Advertisement sent');
    } catch (err) {
      const label = mode === 'zero_hop' ? 'zero-hop advertisement' : 'advertisement';
      console.error(`Failed to send ${label}:`, err);
      toast.error(`Failed to send ${label}`, {
        description: err instanceof Error ? err.message : 'Check radio connection',
      });
    }
  }, []);

  const handleDiscoverMesh = useCallback(async (target: RadioDiscoveryTarget) => {
    setMeshDiscoveryLoadingTarget(target);
    try {
      const data = await api.discoverMesh(target);
      setMeshDiscovery(data);
      toast.success(
        data.results.length === 0
          ? 'No nearby nodes responded'
          : `Found ${data.results.length} nearby node${data.results.length === 1 ? '' : 's'}`
      );
    } catch (err) {
      console.error('Failed to discover nearby nodes:', err);
      toast.error('Failed to run mesh discovery', {
        description: err instanceof Error ? err.message : 'Check radio connection',
      });
    } finally {
      setMeshDiscoveryLoadingTarget(null);
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
    handleDisconnect,
    handleReconnect,
    handleAdvertise,
    meshDiscovery,
    meshDiscoveryLoadingTarget,
    handleDiscoverMesh,
    handleHealthRefresh,
  };
}
