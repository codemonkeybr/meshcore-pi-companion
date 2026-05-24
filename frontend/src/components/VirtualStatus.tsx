import { useEffect, useState } from 'react';
import { api } from '../api';
import type { CompanionStatus, RoomStatus } from '../types';

export function VirtualStatus() {
  const [rooms, setRooms] = useState<RoomStatus[]>([]);
  const [companions, setCompanions] = useState<CompanionStatus[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function fetchStatus() {
    try {
      const [roomsRes, companionsRes] = await Promise.all([
        api.getVirtualRooms(),
        api.getVirtualCompanions(),
      ]);
      setRooms(roomsRes.rooms);
      setCompanions(companionsRes.companions);
      setError(null);
    } catch {
      setError('Failed to load virtual status');
    }
  }

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, 10_000);
    return () => clearInterval(id);
  }, []);

  if (rooms.length === 0 && companions.length === 0 && !error) {
    return (
      <p className="text-sm text-gray-500 italic">
        No virtual rooms or companions configured (SPI mode only).
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {error && <p className="text-sm text-red-500">{error}</p>}

      {rooms.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-2">
            Virtual Rooms
          </h4>
          <div className="space-y-2">
            {rooms.map((room) => (
              <div
                key={room.name}
                className="flex items-center justify-between rounded border border-gray-200 dark:border-gray-700 px-3 py-2 text-sm"
              >
                <div>
                  <span className="font-medium">{room.name}</span>
                  <span className="ml-2 text-xs text-gray-400 font-mono">
                    {room.public_key_prefix}…
                  </span>
                </div>
                <div className="flex items-center gap-3 text-xs text-gray-500">
                  <span>{room.client_count} clients</span>
                  <span>{room.message_count} msgs</span>
                  <span
                    className={`px-1.5 py-0.5 rounded font-medium ${
                      room.running
                        ? 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300'
                        : 'bg-gray-100 text-gray-500'
                    }`}
                  >
                    {room.running ? 'running' : 'stopped'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {companions.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-2">
            TCP Companions
          </h4>
          <div className="space-y-2">
            {companions.map((comp) => (
              <div
                key={comp.name}
                className="flex items-center justify-between rounded border border-gray-200 dark:border-gray-700 px-3 py-2 text-sm"
              >
                <div>
                  <span className="font-medium">{comp.name}</span>
                  <span className="ml-2 text-xs text-gray-400 font-mono">
                    {comp.bind_address}:{comp.tcp_port}
                  </span>
                </div>
                <div className="flex items-center gap-3 text-xs text-gray-500">
                  {comp.connected && comp.client_address && (
                    <span className="font-mono">{comp.client_address}</span>
                  )}
                  <span
                    className={`px-1.5 py-0.5 rounded font-medium ${
                      comp.connected
                        ? 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300'
                        : 'bg-gray-100 text-gray-500'
                    }`}
                  >
                    {comp.connected ? 'connected' : 'waiting'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
