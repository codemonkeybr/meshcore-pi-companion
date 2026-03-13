import {
  BarChart3,
  Database,
  Info,
  MonitorCog,
  RadioTower,
  Share2,
  type LucideIcon,
} from 'lucide-react';

export type SettingsSection = 'radio' | 'local' | 'database' | 'fanout' | 'statistics' | 'about';

export const SETTINGS_SECTION_ORDER: SettingsSection[] = [
  'radio',
  'local',
  'database',
  'fanout',
  'statistics',
  'about',
];

export const SETTINGS_SECTION_LABELS: Record<SettingsSection, string> = {
  radio: 'Radio',
  local: 'Local Configuration',
  database: 'Database & Messaging',
  fanout: 'MQTT & Automation',
  statistics: 'Statistics',
  about: 'About',
};

export const SETTINGS_SECTION_ICONS: Record<SettingsSection, LucideIcon> = {
  radio: RadioTower,
  local: MonitorCog,
  database: Database,
  fanout: Share2,
  statistics: BarChart3,
  about: Info,
};
