export function parseLocalDate(iso: string): Date {
  const [year, month, day] = iso.slice(0, 10).split('-').map(Number);
  return new Date(year, (month ?? 1) - 1, day ?? 1);
}

export function shortDate(iso: string | undefined | null) {
  if (!iso) return '—';
  return parseLocalDate(iso).toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
}

export function axisDate(iso: string | undefined | null) {
  if (!iso) return '—';
  return parseLocalDate(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  });
}

function erf(x: number) {
  const sign = x >= 0 ? 1 : -1;
  const absX = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * absX);
  const y =
    1 -
    (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t +
      0.254829592) *
      t *
      Math.exp(-absX * absX);
  return sign * y;
}

export function normCDF(z: number) {
  return 0.5 * (1 + erf(z / Math.SQRT2));
}

export function formatSvgNumber(value: number) {
  if (!Number.isFinite(value)) return '0';
  return value.toFixed(4).replace(/\.?0+$/, '');
}
