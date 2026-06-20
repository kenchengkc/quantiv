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

function formatWindowLabel(days: Date[]) {
  const start = days[0];
  const end = days[4];
  if (!start || !end) return 'Earnings Week';

  const month = (d: Date) => d.toLocaleDateString('en-US', { month: 'long' });
  const sameMonth = start.getMonth() === end.getMonth();
  return sameMonth
    ? `${month(start)} ${start.getDate()} - ${end.getDate()}, ${start.getFullYear()}`
    : `${month(start)} ${start.getDate()} - ${month(end)} ${end.getDate()}, ${start.getFullYear()}`;
}

function EarningsGridHeaderFallback({ days }: { days: Date[] }) {
  const windowLabel = formatWindowLabel(days);

  return (
    <div style={{ padding: '24px 0 20px', borderBottom: '1px solid var(--line)' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-end',
          gap: 24,
          flexWrap: 'wrap',
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 10,
              letterSpacing: '0.18em',
              textTransform: 'uppercase',
              color: 'var(--ink-3)',
              marginBottom: 14,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              flexWrap: 'wrap',
              minHeight: 22,
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/brand/QuantivIcon.webp"
              alt=""
              width={18}
              height={18}
              style={{
                display: 'inline-block',
                objectFit: 'contain',
                mixBlendMode: 'screen',
              }}
            />
            <span>Earnings Week</span>
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 5,
                padding: '2px 8px',
                borderRadius: 999,
                border: '1px solid var(--line)',
                background: 'var(--bg-2)',
                color: 'var(--ink-3)',
                letterSpacing: '0.08em',
                fontSize: 9.5,
                visibility: 'hidden',
              }}
              aria-hidden="true"
            >
              <span style={{
                width: 6, height: 6, borderRadius: 999,
                background: 'var(--ink-4)',
              }} />
              MARKET CLOSED · LAST CLOSE
            </span>
          </div>
          <h1
            className="serif qv-m-h1"
            style={{
              margin: 0,
              fontSize: 56,
              fontWeight: 800,
              letterSpacing: '-0.032em',
              lineHeight: 0.94,
              color: 'var(--ink)',
              textWrap: 'balance',
              textTransform: 'uppercase',
            }}
          >
            {windowLabel}
          </h1>
          <div
            style={{
              marginTop: 14,
              fontSize: 16,
              color: 'var(--ink-2)',
              maxWidth: 660,
              lineHeight: 1.55,
              letterSpacing: '-0.005em',
            }}
          >
            Tracking what options markets expect and what the market actually delivers.
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 2 }} aria-hidden="true">
          {['‹', 'Last week', 'This week', 'Next week', 'In two weeks', '›'].map((label) => (
            <span
              key={label}
              className="chip"
              style={{
                fontSize: label.length === 1 ? 14 : 11,
                width: label.length === 1 ? 32 : undefined,
                padding: label.length === 1 ? 0 : undefined,
                justifyContent: 'center',
                opacity: 0.72,
              }}
            >
              {label}
            </span>
          ))}
        </div>
      </div>

      <div
        style={{
          marginTop: 28,
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          flexWrap: 'wrap',
        }}
      >
        <div
          style={{
            color: 'var(--ink-3)',
            fontSize: 11,
            letterSpacing: '0.14em',
            textTransform: 'uppercase',
          }}
        >
          Show
        </div>
        <div style={{ display: 'flex', gap: 6 }} aria-hidden="true">
          {['Popular', 'S&P 500', 'Big movers', 'All'].map((label) => (
            <span key={label} className="chip" style={{ fontSize: 11, opacity: 0.72 }}>
              {label}
            </span>
          ))}
        </div>
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            color: 'var(--ink-3)',
            fontSize: 11.5,
            fontStyle: 'italic',
          }}
        >
          Ranked by a 70/30 blend of 90-day dollar volume and market cap.
        </span>
        <div style={{ flex: 1 }} />
        <div
          style={{
            height: 34,
            width: 188,
            borderRadius: 999,
            border: '1px solid var(--line-2)',
            background: 'color-mix(in oklab, var(--bg-2) 88%, transparent)',
          }}
          aria-hidden="true"
        />
      </div>
    </div>
  );
}

export function EarningsGridFallback({ offset = 0 }: { offset?: number }) {
  const { days, today } = getCurrentCalendarSkeletonDates(offset);

  return (
    <>
      <EarningsGridHeaderFallback days={days} />
      <div className="qv-calendar-shell">
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
