// Display-only palette. Catalog order keeps colors stable when columns are hidden.
const palette = ['#3b82f6', '#a855f7', '#f59e0b', '#06b6d4', '#ec4899', '#22c55e', '#f97316', '#6366f1'];
export function stageStyle(raw, index = 0) {
  const value = typeof raw === 'string' ? raw.trim() : '';
  const color = /^#[0-9a-f]{6}$/i.test(value) ? value : palette[Math.abs(index) % palette.length];
  const rgb = [1, 3, 5].map(i => parseInt(color.slice(i, i + 2), 16));
  return {color, background: `rgb(${rgb.map(x => Math.round(255 * .86 + x * .14)).join(', ')})`, ink: `rgb(${rgb.map(x => Math.round(x * .35)).join(', ')})`};
}
