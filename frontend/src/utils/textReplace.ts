const ENABLED_KEY = 'remoteterm-text-replace-enabled';
const MAP_KEY = 'remoteterm-text-replace-map';

const DEFAULT_MAP: Record<string, string> = {
  А: 'A',
  В: 'B',
  Е: 'E',
  Ё: 'E',
  З: '3',
  К: 'K',
  М: 'M',
  Н: 'H',
  О: 'O',
  Р: 'P',
  С: 'C',
  Т: 'T',
  Х: 'X',
  Ь: 'b',
  а: 'a',
  е: 'e',
  ё: 'e',
  о: 'o',
  р: 'p',
  с: 'c',
  у: 'y',
  х: 'x',
};

export const DEFAULT_MAP_JSON = JSON.stringify(DEFAULT_MAP, null, 2);

export function getTextReplaceEnabled(): boolean {
  try {
    return localStorage.getItem(ENABLED_KEY) === 'true';
  } catch {
    return false;
  }
}

export function setTextReplaceEnabled(enabled: boolean): void {
  try {
    if (enabled) {
      localStorage.setItem(ENABLED_KEY, 'true');
    } else {
      localStorage.removeItem(ENABLED_KEY);
    }
  } catch {
    // localStorage may be unavailable
  }
}

export function getTextReplaceMapJson(): string {
  try {
    const raw = localStorage.getItem(MAP_KEY);
    if (raw !== null) return raw;
  } catch {
    // fall through
  }
  return DEFAULT_MAP_JSON;
}

/** Persist the map JSON only if it's valid. Returns null on success or an error string. */
export function setTextReplaceMapJson(json: string): string | null {
  try {
    const parsed = JSON.parse(json);
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed))
      return 'Must be a JSON object.';
    const rawEntries = Object.entries(parsed);
    for (const [k, v] of rawEntries) {
      if (typeof k !== 'string' || typeof v !== 'string')
        return 'All keys and values must be strings.';
    }
    const entries = rawEntries as [string, string][];
    // Check for re-expansion: no key may appear as a substring of any replacement value.
    for (const [needle] of entries) {
      if (needle.length === 0) continue;
      for (const [, replacement] of entries) {
        if (replacement.includes(needle)) {
          return `Key "${needle}" appears inside replacement "${replacement}" and would re-expand on every keystroke.`;
        }
      }
    }
    localStorage.setItem(MAP_KEY, json);
    return null;
  } catch {
    return 'Invalid JSON.';
  }
}

/** Build a sorted-by-length-desc array of [needle, replacement] for efficient matching. */
function buildReplacements(json: string): [string, string][] {
  try {
    const parsed = JSON.parse(json) as Record<string, string>;
    return Object.entries(parsed)
      .filter(([k]) => k.length > 0)
      .sort((a, b) => b[0].length - a[0].length);
  } catch {
    return [];
  }
}

/**
 * Apply text replacements and compute the adjusted cursor position.
 * Returns null if nothing changed.
 */
export function applyTextReplacements(
  text: string,
  cursorPos: number,
  mapJson: string
): { text: string; cursor: number } | null {
  const replacements = buildReplacements(mapJson);
  if (replacements.length === 0) return null;

  let result = '';
  let newCursor = cursorPos;
  let i = 0;

  while (i < text.length) {
    let matched = false;
    for (const [needle, replacement] of replacements) {
      if (text.startsWith(needle, i)) {
        result += replacement;
        // Adjust cursor if this match is before or spans the cursor
        if (i + needle.length <= cursorPos) {
          newCursor += replacement.length - needle.length;
        } else if (i < cursorPos) {
          // Cursor is inside this match — place it after the replacement
          newCursor = result.length;
        }
        i += needle.length;
        matched = true;
        break;
      }
    }
    if (!matched) {
      result += text[i];
      i++;
    }
  }

  if (result === text) return null;
  return { text: result, cursor: newCursor };
}
