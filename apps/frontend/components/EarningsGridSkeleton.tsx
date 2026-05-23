function SkeletonRow({ delayMs }: { delayMs: number }) {
  const bar = (extra: number, h: number, w: string | number, r = 5) => ({
    height: h,
    borderRadius: r,
    background: 'var(--bg-3)',
    width: typeof w === 'number' ? w : w,
    flexShrink: 0 as const,
    animation: 'earnings-grid-pulse 1.1s ease-in-out infinite',
    animationDelay: `${delayMs + extra}ms`,
  });

  return (
    <div
      style={{
        padding: '8px 10px',
        display: 'flex',
        gap: 8,
        alignItems: 'center',
      }}
    >
      <div style={bar(0, 24, 24, 6)} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={bar(20, 10, '68%')} />
        <div style={{ ...bar(40, 8, '42%', 4), marginTop: 6 }} />
      </div>
      <div style={bar(10, 12, 44, 4)} />
    </div>
  );
}

function DayColumnSkeleton({
  dateLabel,
  label,
  isToday,
}: {
  dateLabel: string;
  label: string;
  isToday: boolean;
}) {
  return (
    <div style={{ minWidth: 0 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: 10,
          padding: '0 10px 14px',
          marginBottom: 4,
        }}
      >
        <div
          className="serif"
          style={{
            fontSize: 32,
            lineHeight: 1,
            fontWeight: 800,
            color: isToday ? 'var(--ink)' : 'var(--ink-2)',
            letterSpacing: '-0.03em',
          }}
        >
          {dateLabel}
        </div>
        <div style={{ flex: 1 }}>
          <div
            style={{
              fontSize: 11,
              letterSpacing: '0.14em',
              textTransform: 'uppercase',
              color: isToday ? 'var(--accent)' : 'var(--ink-3)',
              fontWeight: 500,
            }}
          >
            {label}
            {isToday && ' - Today'}
          </div>
          <div
            className="mono tnum"
            style={{ fontSize: 10.5, color: 'var(--ink-4)', marginTop: 2 }}
          >
            Loading...
          </div>
        </div>
      </div>
      {[0, 1, 2, 3, 4].map((i) => (
        <SkeletonRow key={i} delayMs={i * 70} />
      ))}
    </div>
  );
}

export function CalendarGridSkeleton({
  days,
  today,
}: {
  days: Date[];
  today: Date;
}) {
  const dayNames = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'];

  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      className="qv-m-stack qv-calendar-grid"
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(5, minmax(0, 1fr))',
        marginTop: 20,
      }}
    >
      {days.map((d, i) => (
        <div
          key={d.toISOString()}
          className="qv-calendar-day"
          style={{
            borderRight: i < 4 ? '1px solid var(--line)' : 'none',
          }}
        >
          <DayColumnSkeleton
            dateLabel={String(d.getDate())}
            label={dayNames[i]}
            isToday={d.getTime() === today.getTime()}
          />
        </div>
      ))}
    </div>
  );
}

function mondayOf(d: Date) {
  const out = new Date(d);
  const day = out.getDay();
  const delta = day === 0 ? -6 : 1 - day;
  out.setDate(out.getDate() + delta);
  out.setHours(0, 0, 0, 0);
  return out;
}

export function getCurrentCalendarSkeletonDates(offset = 0) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const weekStart = mondayOf(today);
  weekStart.setDate(weekStart.getDate() + 7 * offset);

  const days = Array.from({ length: 5 }, (_, i) => {
    const d = new Date(weekStart);
    d.setDate(weekStart.getDate() + i);
    return d;
  });

  return { days, today };
}

export function EarningsGridFallback({ offset = 0 }: { offset?: number }) {
  const { days, today } = getCurrentCalendarSkeletonDates(offset);

  return (
    <>
      <div style={{ minHeight: 'min(560px, 62vh)' }}>
        <CalendarGridSkeleton days={days} today={today} />
      </div>
      <div
        style={{
          marginTop: 40,
          padding: '16px 0',
          borderTop: '1px solid var(--line)',
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 11,
          color: 'var(--ink-4)',
          minHeight: 36,
        }}
      >
        <span className="mono" style={{ color: 'var(--ink-3)' }}>
          Updating calendar...
        </span>
      </div>
    </>
  );
}
