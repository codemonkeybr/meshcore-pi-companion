import { describe, it, expect, beforeEach } from 'vitest';
import { getLocalLabel, setLocalLabel, getContrastTextColor } from '../utils/localLabel';

describe('localLabel utilities', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  describe('getLocalLabel', () => {
    it('returns default when nothing stored', () => {
      const label = getLocalLabel();
      expect(label.text).toBe('');
      expect(label.color).toBe('#062d60');
    });

    it('returns stored label', () => {
      localStorage.setItem(
        'remoteterm-local-label',
        JSON.stringify({ text: 'Dev', color: '#ff0000' })
      );
      const label = getLocalLabel();
      expect(label.text).toBe('Dev');
      expect(label.color).toBe('#ff0000');
    });

    it('handles corrupted JSON gracefully', () => {
      localStorage.setItem('remoteterm-local-label', '{bad json');
      const label = getLocalLabel();
      expect(label.text).toBe('');
      expect(label.color).toBe('#062d60');
    });

    it('handles partial stored data', () => {
      localStorage.setItem('remoteterm-local-label', JSON.stringify({ text: 'Hi' }));
      const label = getLocalLabel();
      expect(label.text).toBe('Hi');
      expect(label.color).toBe('#062d60'); // falls back to default color
    });

    it('handles non-string values in stored data', () => {
      localStorage.setItem('remoteterm-local-label', JSON.stringify({ text: 123, color: true }));
      const label = getLocalLabel();
      expect(label.text).toBe(''); // non-string falls back
      expect(label.color).toBe('#062d60');
    });
  });

  describe('setLocalLabel', () => {
    it('stores label to localStorage', () => {
      setLocalLabel('Test', '#00ff00');
      const raw = localStorage.getItem('remoteterm-local-label');
      expect(raw).not.toBeNull();
      const parsed = JSON.parse(raw!);
      expect(parsed.text).toBe('Test');
      expect(parsed.color).toBe('#00ff00');
    });
  });

  describe('getContrastTextColor', () => {
    it('returns white for dark colors', () => {
      expect(getContrastTextColor('#000000')).toBe('white');
      expect(getContrastTextColor('#062d60')).toBe('white');
      expect(getContrastTextColor('#333333')).toBe('white');
    });

    it('returns black for light colors', () => {
      expect(getContrastTextColor('#ffffff')).toBe('black');
      expect(getContrastTextColor('#ffff00')).toBe('black');
      expect(getContrastTextColor('#00ff00')).toBe('black');
    });

    it('handles hex with # prefix', () => {
      expect(getContrastTextColor('#000000')).toBe('white');
    });

    it('handles hex without # prefix', () => {
      expect(getContrastTextColor('000000')).toBe('white');
      expect(getContrastTextColor('ffffff')).toBe('black');
    });
  });
});
