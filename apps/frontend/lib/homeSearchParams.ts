export type HomeCalendarFilter = 'popular' | 'sp500' | 'movers' | 'all';

const MIN_OFFSET = -1;
const MAX_OFFSET = 2;

export function parseHomeSearchParams(sp: {
  offset?: string;
  filter?: string;
}): { initialOffset: number; initialFilter: HomeCalendarFilter } {
  const v = Number(sp.offset);
  const initialOffset =
    Number.isFinite(v) && v >= MIN_OFFSET && v <= MAX_OFFSET ? v : 0;
  const f = sp.filter;
  const initialFilter: HomeCalendarFilter =
    f === 'popular' || f === 'sp500' || f === 'movers' || f === 'all'
      ? f
      : 'popular';
  return { initialOffset, initialFilter };
}
