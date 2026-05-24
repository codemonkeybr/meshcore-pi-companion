// Radio presets for common LoRa configurations
interface RadioPreset {
  name: string;
  freq: number;
  bw: number;
  sf: number;
  cr: number;
}

export const RADIO_PRESETS: RadioPreset[] = [
  { name: 'USA/Canada', freq: 910.525, bw: 62.5, sf: 7, cr: 5 },
  { name: 'Australia', freq: 915.8, bw: 250, sf: 10, cr: 5 },
  { name: 'Australia (narrow)', freq: 916.575, bw: 62.5, sf: 7, cr: 8 },
  { name: 'Australia (Mid)', freq: 915.075, bw: 125, sf: 9, cr: 5 },
  { name: 'Australia SA, WA', freq: 923.125, bw: 62.5, sf: 8, cr: 8 },
  { name: 'Australia QLD', freq: 923.125, bw: 62.5, sf: 8, cr: 5 },
  { name: 'New Zealand', freq: 917.375, bw: 250, sf: 11, cr: 5 },
  { name: 'New Zealand (narrow)', freq: 917.375, bw: 62.5, sf: 7, cr: 5 },
  { name: 'EU/UK (Narrow)', freq: 869.618, bw: 62.5, sf: 8, cr: 8 },
  { name: 'EU/UK (Deprecated)', freq: 869.525, bw: 250, sf: 11, cr: 5 },
  { name: 'Switzerland', freq: 869.618, bw: 62.5, sf: 8, cr: 8 },
  { name: 'Czech Republic (Narrow)', freq: 869.432, bw: 62.5, sf: 7, cr: 5 },
  { name: 'EU 433MHz Long Range', freq: 433.65, bw: 250, sf: 11, cr: 5 },
  { name: 'EU 433MHz (Narrow)', freq: 433.65, bw: 62.5, sf: 8, cr: 8 },
  { name: 'Portugal 433MHz', freq: 433.375, bw: 62.5, sf: 9, cr: 6 },
  { name: 'Portugal 868MHz', freq: 869.618, bw: 62.5, sf: 7, cr: 6 },
  { name: 'Vietnam (Narrow)', freq: 920.25, bw: 62.5, sf: 8, cr: 5 },
  { name: 'Vietnam (Deprecated)', freq: 920.25, bw: 250, sf: 11, cr: 5 },
];
