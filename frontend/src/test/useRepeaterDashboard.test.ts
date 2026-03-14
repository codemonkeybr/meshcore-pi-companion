import { StrictMode, createElement, type ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import {
  resetRepeaterDashboardCacheForTests,
  useRepeaterDashboard,
} from '../hooks/useRepeaterDashboard';
import type { Conversation } from '../types';

// Mock the api module
vi.mock('../api', () => ({
  api: {
    repeaterLogin: vi.fn(),
    repeaterStatus: vi.fn(),
    repeaterNodeInfo: vi.fn(),
    repeaterNeighbors: vi.fn(),
    repeaterAcl: vi.fn(),
    repeaterRadioSettings: vi.fn(),
    repeaterAdvertIntervals: vi.fn(),
    repeaterOwnerInfo: vi.fn(),
    repeaterLppTelemetry: vi.fn(),
    sendRepeaterCommand: vi.fn(),
  },
}));

// Mock sonner toast
vi.mock('../components/ui/sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
}));

// Get mock reference — cast to Record<string, Mock> for type-safe mock method access
const { api: _rawApi } = await import('../api');
const mockApi = _rawApi as unknown as Record<string, Mock>;

const REPEATER_KEY = 'aa'.repeat(32);

const repeaterConversation: Conversation = {
  type: 'contact',
  id: REPEATER_KEY,
  name: 'TestRepeater',
};

describe('useRepeaterDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetRepeaterDashboardCacheForTests();
  });

  it('starts with logged out state', () => {
    const { result } = renderHook(() => useRepeaterDashboard(repeaterConversation));
    expect(result.current.loggedIn).toBe(false);
    expect(result.current.loginLoading).toBe(false);
    expect(result.current.loginError).toBe(null);
  });

  it('login sets loggedIn on success', async () => {
    mockApi.repeaterLogin.mockResolvedValueOnce({ status: 'ok' });

    const { result } = renderHook(() => useRepeaterDashboard(repeaterConversation));

    await act(async () => {
      await result.current.login('secret');
    });

    expect(result.current.loggedIn).toBe(true);
    expect(result.current.loginError).toBe(null);
    expect(mockApi.repeaterLogin).toHaveBeenCalledWith(REPEATER_KEY, 'secret');
  });

  it('login sets error on failure', async () => {
    mockApi.repeaterLogin.mockRejectedValueOnce(new Error('Auth failed'));

    const { result } = renderHook(() => useRepeaterDashboard(repeaterConversation));

    await act(async () => {
      await result.current.login('bad');
    });

    expect(result.current.loggedIn).toBe(false);
    expect(result.current.loginError).toBe('Auth failed');
  });

  it('loginAsGuest calls login with empty password', async () => {
    mockApi.repeaterLogin.mockResolvedValueOnce({ status: 'ok' });

    const { result } = renderHook(() => useRepeaterDashboard(repeaterConversation));

    await act(async () => {
      await result.current.loginAsGuest();
    });

    expect(mockApi.repeaterLogin).toHaveBeenCalledWith(REPEATER_KEY, '');
    expect(result.current.loggedIn).toBe(true);
  });

  it('refreshPane stores data on success', async () => {
    const statusData = {
      battery_volts: 4.2,
      tx_queue_len: 0,
      noise_floor_dbm: -120,
      last_rssi_dbm: -85,
      last_snr_db: 7.5,
      packets_received: 100,
      packets_sent: 50,
      airtime_seconds: 600,
      rx_airtime_seconds: 1200,
      uptime_seconds: 86400,
      sent_flood: 10,
      sent_direct: 40,
      recv_flood: 30,
      recv_direct: 70,
      flood_dups: 1,
      direct_dups: 0,
      full_events: 0,
    };
    mockApi.repeaterStatus.mockResolvedValueOnce(statusData);

    const { result } = renderHook(() => useRepeaterDashboard(repeaterConversation));

    await act(async () => {
      await result.current.refreshPane('status');
    });

    expect(result.current.paneData.status).toEqual(statusData);
    expect(result.current.paneStates.status.loading).toBe(false);
    expect(result.current.paneStates.status.error).toBe(null);
    expect(result.current.paneStates.status.fetched_at).toEqual(expect.any(Number));
  });

  it('refreshPane still issues requests under StrictMode remount probing', async () => {
    const statusData = { battery_volts: 4.2 };
    mockApi.repeaterStatus.mockResolvedValueOnce(statusData);

    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(StrictMode, null, children);

    const { result } = renderHook(() => useRepeaterDashboard(repeaterConversation), { wrapper });

    await act(async () => {
      await result.current.refreshPane('status');
    });

    expect(mockApi.repeaterStatus).toHaveBeenCalledTimes(1);
    expect(result.current.paneData.status).toEqual(statusData);
  });

  it('refreshPane retries up to 3 times', async () => {
    mockApi.repeaterStatus.mockRejectedValueOnce(new Error('fail1'));
    mockApi.repeaterStatus.mockRejectedValueOnce(new Error('fail2'));
    mockApi.repeaterStatus.mockRejectedValueOnce(new Error('fail3'));

    const { result } = renderHook(() => useRepeaterDashboard(repeaterConversation));

    await act(async () => {
      await result.current.refreshPane('status');
    });

    expect(mockApi.repeaterStatus).toHaveBeenCalledTimes(3);
    expect(result.current.paneStates.status.error).toBe('fail3');
    expect(result.current.paneData.status).toBe(null);
  });

  it('refreshPane succeeds on second attempt', async () => {
    const statusData = { battery_volts: 3.7 };
    mockApi.repeaterStatus.mockRejectedValueOnce(new Error('fail1'));
    mockApi.repeaterStatus.mockResolvedValueOnce(statusData);

    const { result } = renderHook(() => useRepeaterDashboard(repeaterConversation));

    await act(async () => {
      await result.current.refreshPane('status');
    });

    expect(mockApi.repeaterStatus).toHaveBeenCalledTimes(2);
    expect(result.current.paneData.status).toEqual(statusData);
    expect(result.current.paneStates.status.error).toBe(null);
  });

  it('sendConsoleCommand adds entries to console history', async () => {
    mockApi.sendRepeaterCommand.mockResolvedValueOnce({
      command: 'ver',
      response: 'v2.1.0',
      sender_timestamp: 1000,
    });

    const { result } = renderHook(() => useRepeaterDashboard(repeaterConversation));

    await act(async () => {
      await result.current.sendConsoleCommand('ver');
    });

    expect(result.current.consoleHistory).toHaveLength(2);
    expect(result.current.consoleHistory[0].outgoing).toBe(true);
    expect(result.current.consoleHistory[0].command).toBe('ver');
    expect(result.current.consoleHistory[1].outgoing).toBe(false);
    expect(result.current.consoleHistory[1].response).toBe('v2.1.0');
  });

  it('sendConsoleCommand adds error entry on failure', async () => {
    mockApi.sendRepeaterCommand.mockRejectedValueOnce(new Error('Network error'));

    const { result } = renderHook(() => useRepeaterDashboard(repeaterConversation));

    await act(async () => {
      await result.current.sendConsoleCommand('ver');
    });

    expect(result.current.consoleHistory).toHaveLength(2);
    expect(result.current.consoleHistory[0].outgoing).toBe(true);
    expect(result.current.consoleHistory[0].command).toBe('ver');
    expect(result.current.consoleHistory[1].outgoing).toBe(false);
    expect(result.current.consoleHistory[1].response).toBe('Error: Network error');
    expect(result.current.consoleLoading).toBe(false);
  });

  it('sendZeroHopAdvert sends "advert.zerohop" command', async () => {
    mockApi.sendRepeaterCommand.mockResolvedValueOnce({
      command: 'advert.zerohop',
      response: 'ok',
      sender_timestamp: 1000,
    });

    const { result } = renderHook(() => useRepeaterDashboard(repeaterConversation));

    await act(async () => {
      await result.current.sendZeroHopAdvert();
    });

    expect(mockApi.sendRepeaterCommand).toHaveBeenCalledWith(REPEATER_KEY, 'advert.zerohop');
  });

  it('sendFloodAdvert sends "advert" command', async () => {
    mockApi.sendRepeaterCommand.mockResolvedValueOnce({
      command: 'advert',
      response: 'ok',
      sender_timestamp: 1000,
    });

    const { result } = renderHook(() => useRepeaterDashboard(repeaterConversation));

    await act(async () => {
      await result.current.sendFloodAdvert();
    });

    expect(mockApi.sendRepeaterCommand).toHaveBeenCalledWith(REPEATER_KEY, 'advert');
  });

  it('rebootRepeater sends "reboot" command', async () => {
    mockApi.sendRepeaterCommand.mockResolvedValueOnce({
      command: 'reboot',
      response: 'ok',
      sender_timestamp: 1000,
    });

    const { result } = renderHook(() => useRepeaterDashboard(repeaterConversation));

    await act(async () => {
      await result.current.rebootRepeater();
    });

    expect(mockApi.sendRepeaterCommand).toHaveBeenCalledWith(REPEATER_KEY, 'reboot');
  });

  it('syncClock sends "time <epoch>" command', async () => {
    const dateNowSpy = vi.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000);
    mockApi.sendRepeaterCommand.mockResolvedValueOnce({
      command: 'time 1700000000',
      response: 'ok',
      sender_timestamp: 1000,
    });

    const { result } = renderHook(() => useRepeaterDashboard(repeaterConversation));

    await act(async () => {
      await result.current.syncClock();
    });

    expect(mockApi.sendRepeaterCommand).toHaveBeenCalledWith(REPEATER_KEY, 'time 1700000000');
    dateNowSpy.mockRestore();
  });

  it('loadAll calls refreshPane for all panes serially', async () => {
    mockApi.repeaterStatus.mockResolvedValueOnce({ battery_volts: 4.0 });
    mockApi.repeaterNodeInfo.mockResolvedValueOnce({
      name: null,
      lat: null,
      lon: null,
      clock_utc: null,
    });
    mockApi.repeaterRadioSettings.mockResolvedValueOnce({
      firmware_version: 'v1.0',
      radio: null,
      tx_power: null,
      airtime_factor: null,
      repeat_enabled: null,
      flood_max: null,
    });
    mockApi.repeaterNeighbors.mockResolvedValueOnce({ neighbors: [] });
    mockApi.repeaterAcl.mockResolvedValueOnce({ acl: [] });
    mockApi.repeaterAdvertIntervals.mockResolvedValueOnce({
      advert_interval: null,
      flood_advert_interval: null,
    });
    mockApi.repeaterOwnerInfo.mockResolvedValueOnce({
      owner_info: null,
      guest_password: null,
    });
    mockApi.repeaterLppTelemetry.mockResolvedValueOnce({ sensors: [] });

    const { result } = renderHook(() => useRepeaterDashboard(repeaterConversation));

    await act(async () => {
      await result.current.loadAll();
    });

    expect(mockApi.repeaterStatus).toHaveBeenCalledTimes(1);
    expect(mockApi.repeaterNodeInfo).toHaveBeenCalledTimes(1);
    expect(mockApi.repeaterNeighbors).toHaveBeenCalledTimes(1);
    expect(mockApi.repeaterAcl).toHaveBeenCalledTimes(1);
    expect(mockApi.repeaterRadioSettings).toHaveBeenCalledTimes(1);
    expect(mockApi.repeaterAdvertIntervals).toHaveBeenCalledTimes(1);
    expect(mockApi.repeaterOwnerInfo).toHaveBeenCalledTimes(1);
    expect(mockApi.repeaterLppTelemetry).toHaveBeenCalledTimes(1);
  });

  it('refreshing neighbors fetches node info first', async () => {
    mockApi.repeaterNodeInfo.mockResolvedValueOnce({
      name: 'Repeater',
      lat: '-31.9523',
      lon: '115.8613',
      clock_utc: null,
    });
    mockApi.repeaterNeighbors.mockResolvedValueOnce({ neighbors: [] });

    const { result } = renderHook(() => useRepeaterDashboard(repeaterConversation));

    await act(async () => {
      await result.current.refreshPane('neighbors');
    });

    expect(mockApi.repeaterNodeInfo).toHaveBeenCalledTimes(1);
    expect(mockApi.repeaterNeighbors).toHaveBeenCalledTimes(1);
    expect(mockApi.repeaterNodeInfo.mock.invocationCallOrder[0]).toBeLessThan(
      mockApi.repeaterNeighbors.mock.invocationCallOrder[0]
    );
    expect(result.current.paneData.nodeInfo?.lat).toBe('-31.9523');
    expect(result.current.paneData.neighbors).toEqual({ neighbors: [] });
  });

  it('refreshing neighbors reuses already-fetched node info', async () => {
    mockApi.repeaterNodeInfo.mockResolvedValueOnce({
      name: 'Repeater',
      lat: '-31.9523',
      lon: '115.8613',
      clock_utc: null,
    });
    mockApi.repeaterNeighbors.mockResolvedValueOnce({ neighbors: [] });
    mockApi.repeaterNeighbors.mockResolvedValueOnce({ neighbors: [] });

    const { result } = renderHook(() => useRepeaterDashboard(repeaterConversation));

    await act(async () => {
      await result.current.refreshPane('neighbors');
    });
    await act(async () => {
      await result.current.refreshPane('neighbors');
    });

    expect(mockApi.repeaterNodeInfo).toHaveBeenCalledTimes(1);
    expect(mockApi.repeaterNeighbors).toHaveBeenCalledTimes(2);
  });

  it('restores dashboard state when navigating away and back to the same repeater', async () => {
    const statusData = { battery_volts: 4.2 };
    mockApi.repeaterLogin.mockResolvedValueOnce({ status: 'ok' });
    mockApi.repeaterStatus.mockResolvedValueOnce(statusData);
    mockApi.sendRepeaterCommand.mockResolvedValueOnce({
      command: 'ver',
      response: 'v2.1.0',
      sender_timestamp: 1000,
    });

    const firstMount = renderHook(() => useRepeaterDashboard(repeaterConversation));

    await act(async () => {
      await firstMount.result.current.login('secret');
      await firstMount.result.current.refreshPane('status');
      await firstMount.result.current.sendConsoleCommand('ver');
    });

    expect(firstMount.result.current.loggedIn).toBe(true);
    expect(firstMount.result.current.paneData.status).toEqual(statusData);
    expect(firstMount.result.current.consoleHistory).toHaveLength(2);

    firstMount.unmount();

    const secondMount = renderHook(() => useRepeaterDashboard(repeaterConversation));

    expect(secondMount.result.current.loggedIn).toBe(true);
    expect(secondMount.result.current.loginError).toBe(null);
    expect(secondMount.result.current.paneData.status).toEqual(statusData);
    expect(secondMount.result.current.paneStates.status.loading).toBe(false);
    expect(secondMount.result.current.consoleHistory).toHaveLength(2);
    expect(secondMount.result.current.consoleHistory[1].response).toBe('v2.1.0');
  });
});
