import {
  BarChart3,
  Database,
  Info,
  MonitorCog,
  Network,
  RadioTower,
  Share2,
  SlidersHorizontal,
  type LucideIcon,
} from 'lucide-react';

export type SettingsSection =
  | 'radio'
  | 'local'
  | 'radio-app'
  | 'database'
  | 'fanout'
  | 'virtual'
  | 'statistics'
  | 'about';

export const SETTINGS_SECTION_ORDER: SettingsSection[] = [
  'radio',
  'local',
  'fanout',
  'virtual',
  'radio-app',
  'database',
  'statistics',
  'about',
];

export const SETTINGS_SECTION_LABELS: Record<SettingsSection, string> = {
  radio: 'Radio',
  local: 'Local Configuration',
  'radio-app': 'Radio-App Management',
  database: 'Database',
  fanout: 'MQTT & Automation',
  virtual: 'Virtual Rooms & Companions',
  statistics: 'Statistics',
  about: 'About',
};

export const SETTINGS_SECTION_ICONS: Record<SettingsSection, LucideIcon> = {
  radio: RadioTower,
  local: MonitorCog,
  'radio-app': SlidersHorizontal,
  database: Database,
  fanout: Share2,
  virtual: Network,
  statistics: BarChart3,
  about: Info,
};
