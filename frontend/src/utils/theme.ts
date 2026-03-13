export interface Theme {
  id: string;
  name: string;
  /** 6 hex colors for the swatch preview: [bg, card, primary, accent, warm, cool] */
  swatches: [string, string, string, string, string, string];
  /** Hex background color for the PWA theme-color meta tag */
  metaThemeColor: string;
}

export const THEME_CHANGE_EVENT = 'remoteterm-theme-change';

export const THEMES: Theme[] = [
  {
    id: 'original',
    name: 'Original',
    swatches: ['#111419', '#181b21', '#27a05c', '#282c33', '#f59e0b', '#3b82f6'],
    metaThemeColor: '#111419',
  },
  {
    id: 'light',
    name: 'Light',
    swatches: ['#F8F7F4', '#FFFFFF', '#1B7D4E', '#EDEBE7', '#D97706', '#3B82F6'],
    metaThemeColor: '#F8F7F4',
  },
  {
    id: 'ios',
    name: 'iPhone',
    swatches: ['#F2F2F7', '#FFFFFF', '#007AFF', '#E5E5EA', '#FF9F0A', '#34C759'],
    metaThemeColor: '#F2F2F7',
  },
  {
    id: 'paper-grove',
    name: 'Paper Grove',
    swatches: ['#F7F1E4', '#FFF9EE', '#2F9E74', '#E7DEC8', '#E76F51', '#5C7CFA'],
    metaThemeColor: '#F7F1E4',
  },
  {
    id: 'cyberpunk',
    name: 'Cyberpunk',
    swatches: ['#07080A', '#0D1112', '#00FF41', '#141E17', '#FAFF00', '#FF2E6C'],
    metaThemeColor: '#07080A',
  },
  {
    id: 'obsidian-glass',
    name: 'Obsidian Glass',
    swatches: ['#0C0E12', '#151821', '#D4A070', '#1E2230', '#D4924A', '#5B82B4'],
    metaThemeColor: '#0C0E12',
  },
  {
    id: 'solar-flare',
    name: 'Solar Flare',
    swatches: ['#0D0607', '#151012', '#FF0066', '#2D1D22', '#FF8C1A', '#30ACD4'],
    metaThemeColor: '#0D0607',
  },
  {
    id: 'lagoon-pop',
    name: 'Lagoon Pop',
    swatches: ['#081A22', '#0F2630', '#23D7C6', '#173844', '#FF7A66', '#7C83FF'],
    metaThemeColor: '#081A22',
  },
  {
    id: 'candy-dusk',
    name: 'Candy Dusk',
    swatches: ['#140F24', '#201736', '#FF79C9', '#2A2144', '#FFC857', '#8BE9FD'],
    metaThemeColor: '#140F24',
  },
  {
    id: 'high-contrast',
    name: 'High Contrast',
    swatches: ['#000000', '#141414', '#3B9EFF', '#1E1E1E', '#FFB800', '#FF4757'],
    metaThemeColor: '#000000',
  },
  {
    id: 'monochrome',
    name: 'Monochrome',
    swatches: ['#FAFAFA', '#FFFFFF', '#111111', '#EAEAEA', '#8A8A8A', '#4A4A4A'],
    metaThemeColor: '#FAFAFA',
  },
  {
    id: 'windows-95',
    name: 'Windows 95',
    swatches: ['#008080', '#C0C0C0', '#000080', '#DFDFDF', '#FFDE59', '#000000'],
    metaThemeColor: '#008080',
  },
];

const THEME_KEY = 'remoteterm-theme';

export function getSavedTheme(): string {
  try {
    return localStorage.getItem(THEME_KEY) ?? 'original';
  } catch {
    return 'original';
  }
}

export function applyTheme(themeId: string): void {
  try {
    localStorage.setItem(THEME_KEY, themeId);
  } catch {
    // localStorage may be unavailable
  }

  if (themeId === 'original') {
    delete document.documentElement.dataset.theme;
  } else {
    document.documentElement.dataset.theme = themeId;
  }

  // Update PWA theme-color meta tag
  const theme = THEMES.find((t) => t.id === themeId);
  if (theme) {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute('content', theme.metaThemeColor);
    }
  }

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(THEME_CHANGE_EVENT, { detail: themeId }));
  }
}
