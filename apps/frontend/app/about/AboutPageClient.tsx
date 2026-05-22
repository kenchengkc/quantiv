'use client';

import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';
import katex from 'katex';

const ABOUT_PAGE_SETTLE_DELAY_MS = 360;
const COUNT_UP_DURATION_MS = 2600;

function prefersReducedMotion() {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

function waitForWindowLoad() {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return Promise.resolve();
  }
  if (document.readyState === 'complete') {
    return Promise.resolve();
  }
  return new Promise<void>((resolve) => {
    window.addEventListener('load', () => resolve(), { once: true });
  });
}

function waitForFonts() {
  if (typeof document === 'undefined' || !('fonts' in document)) {
    return Promise.resolve();
  }
  return document.fonts.ready.then(() => undefined).catch(() => undefined);
}

function waitForImage(img: HTMLImageElement) {
  const decode = () =>
    img.decode?.().then(() => undefined).catch(() => undefined) ?? Promise.resolve();

  if (img.complete) {
    return decode();
  }

  return new Promise<void>((resolve) => {
    const done = () => {
      img.removeEventListener('load', done);
      img.removeEventListener('error', done);
      resolve();
    };
    img.addEventListener('load', done, { once: true });
    img.addEventListener('error', done, { once: true });
  }).then(decode);
}

function waitForImages(container: HTMLElement | null) {
  if (!container) {
    return Promise.resolve();
  }

  const images = Array.from(container.querySelectorAll('img'));
  return Promise.all(images.map(waitForImage)).then(() => undefined);
}

function usePageSettled(
  containerRef: React.RefObject<HTMLElement | null>,
  delayMs = ABOUT_PAGE_SETTLE_DELAY_MS,
) {
  const [settled, setSettled] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let frame: number | null = null;
    let timeout: number | null = null;

    const finish = () => {
      if (cancelled) return;

      const delay = prefersReducedMotion() ? 0 : delayMs;
      frame = window.requestAnimationFrame(() => {
        timeout = window.setTimeout(() => {
          if (!cancelled) setSettled(true);
        }, delay);
      });
    };

    Promise.all([waitForWindowLoad(), waitForFonts()])
      .then(() => waitForImages(containerRef.current))
      .then(finish)
      .catch(finish);

    return () => {
      cancelled = true;
      if (frame !== null) window.cancelAnimationFrame(frame);
      if (timeout !== null) window.clearTimeout(timeout);
    };
  }, [containerRef, delayMs]);

  return settled;
}

// ──────────────────────────────────────────────────────────────────────────
// Reveal-on-scroll wrapper
// ──────────────────────────────────────────────────────────────────────────
function Reveal({
  children,
  delay = 0,
  enabled = true,
  style,
}: {
  children: React.ReactNode;
  delay?: number;
  enabled?: boolean;
  style?: React.CSSProperties;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (!enabled || shown) return;

    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === 'undefined') {
      setShown(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            setShown(true);
            io.disconnect();
            break;
          }
        }
      },
      { threshold: 0.05, rootMargin: '0px 0px -40px 0px' },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [enabled, shown]);
  return (
    <div
      ref={ref}
      className={`reveal${shown ? ' in' : ''}`}
      style={{ transitionDelay: `${delay}ms`, ...style }}
    >
      {children}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Count-up animation for hero stats
// ──────────────────────────────────────────────────────────────────────────
function CountUp({
  value,
  duration = COUNT_UP_DURATION_MS,
  enabled = true,
  suffix = '',
  decimals = 0,
  prefix = '',
}: {
  value: number;
  duration?: number;
  enabled?: boolean;
  suffix?: string;
  decimals?: number;
  prefix?: string;
}) {
  const [current, setCurrent] = useState(0);
  const ref = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    if (!enabled) {
      setCurrent(0);
      return;
    }

    const el = ref.current;
    if (!el) return;
    if (prefersReducedMotion() || typeof IntersectionObserver === 'undefined') {
      setCurrent(value);
      return;
    }

    let started = false;
    let frame: number | null = null;

    const startAnimation = () => {
      const start = performance.now();
      const tick = (t: number) => {
        const elapsed = t - start;
        const p = Math.min(1, elapsed / duration);
        const eased = 1 - Math.pow(1 - p, 3);
        setCurrent(value * eased);
        if (p < 1) {
          frame = requestAnimationFrame(tick);
        }
      };
      frame = requestAnimationFrame(tick);
    };

    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting && !started) {
            started = true;
            startAnimation();
            io.disconnect();
            break;
          }
        }
      },
      { threshold: 0.4 },
    );
    io.observe(el);
    return () => {
      io.disconnect();
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [enabled, value, duration]);
  return (
    <span ref={ref} className="serif tnum">
      {prefix}
      {current.toFixed(decimals)}
      {suffix}
    </span>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Hero glyph — same reveal sequence as the homepage Splash (closed ring →
// slit opens → tail clips in left-to-right). Plays once when the hero
// scrolls into view. CSS lives in globals.css under .quantiv-hero-q*.
// ──────────────────────────────────────────────────────────────────────────
function HeroGlyph({ enabled = true }: { enabled?: boolean }) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!enabled) return;

    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === 'undefined') {
      el.classList.add('in');
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            el.classList.add('in');
            io.disconnect();
            break;
          }
        }
      },
      { threshold: 0.35 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [enabled]);
  return (
    <div ref={ref} className="quantiv-hero-q" aria-hidden="true">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/brand/QuantivSplashQClosed.png"
        alt=""
        className="quantiv-hero-q-layer quantiv-hero-q-ring"
        draggable={false}
      />
      <div className="quantiv-hero-q-slit" />
      <div className="quantiv-hero-q-tail-clip">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/brand/QuantivSplashTail.png"
          alt=""
          className="quantiv-hero-q-layer"
          draggable={false}
        />
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Mini visualization · straddle bar (Pricing lens)
// ──────────────────────────────────────────────────────────────────────────
function MiniStraddleBar() {
  return (
    <svg viewBox="0 0 200 64" style={{ width: '100%', height: 64, display: 'block' }} aria-hidden="true">
      <defs>
        <linearGradient id="mini-stradd" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0%" stopColor="var(--brand-blue-2)" />
          <stop offset="100%" stopColor="var(--brand-blue-1)" />
        </linearGradient>
        <linearGradient id="mini-density" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="var(--brand-blue-1)" stopOpacity="0.45" />
          <stop offset="100%" stopColor="var(--brand-blue-1)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d="M 0 50 Q 100 -5 200 50 L 200 50 L 0 50 Z" fill="url(#mini-density)" />
      <path d="M 0 50 Q 100 -5 200 50" fill="none" stroke="var(--brand-blue-1)" strokeOpacity="0.6" strokeWidth="0.8" />
      <line x1="0" x2="200" y1="50" y2="50" stroke="var(--line)" strokeWidth="1" />
      <rect x="50" y="45" width="100" height="10" rx="5" fill="url(#mini-stradd)">
        <animate attributeName="opacity" values="0.4;1;0.4" dur="2400ms" repeatCount="indefinite" />
      </rect>
      <line x1="100" x2="100" y1="40" y2="56" stroke="var(--ink)" strokeWidth="1.5" />
      <text x="100" y="64" textAnchor="middle" fontSize="8" fill="var(--ink-2)" letterSpacing="0.1em" fontFamily="ui-monospace, monospace">SPOT</text>
      <text x="50" y="40" textAnchor="middle" fontSize="9" fill="var(--down)" fontFamily="ui-monospace, monospace" fontWeight="700">−6%</text>
      <text x="150" y="40" textAnchor="middle" fontSize="9" fill="var(--up)" fontFamily="ui-monospace, monospace" fontWeight="700">+6%</text>
    </svg>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Mini visualization · realized history bars (History lens)
// ──────────────────────────────────────────────────────────────────────────
function MiniHistory() {
  const rows = useMemo(
    () => [
      { i: 0.052, a: 0.031 },
      { i: 0.055, a: -0.024 },
      { i: 0.058, a: 0.068 },
      { i: 0.062, a: -0.041 },
      { i: 0.060, a: 0.023 },
      { i: 0.061, a: -0.037 },
      { i: 0.063, a: 0.044 },
      { i: 0.065, a: -0.052 },
    ],
    [],
  );
  const W = 200;
  const H = 64;
  const P = 8;
  const colW = (W - P * 2) / rows.length;
  const max = 0.085;
  const y = (v: number) => H / 2 - (v / max) * (H / 2 - 8);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 64, display: 'block' }} aria-hidden="true">
      <line x1={P} x2={W - P} y1={H / 2} y2={H / 2} stroke="var(--line)" />
      {rows.map((r, i) => {
        const cx = P + colW * i + colW / 2;
        const beat = Math.abs(r.a) > r.i;
        return (
          <g key={i}>
            <rect
              x={cx - 5}
              y={y(r.i)}
              width="10"
              height={y(-r.i) - y(r.i)}
              fill="color-mix(in oklab, var(--brand-blue-1) 22%, transparent)"
              rx="1"
            >
              <animate attributeName="opacity" from="0" to="1" dur="600ms" begin={`${i * 80}ms`} fill="freeze" />
            </rect>
            <line x1={cx - 5} x2={cx + 5} y1={y(r.i)} y2={y(r.i)} stroke="var(--brand-blue-1)" strokeWidth="0.8" />
            <line x1={cx - 5} x2={cx + 5} y1={y(-r.i)} y2={y(-r.i)} stroke="var(--brand-blue-1)" strokeWidth="0.8" />
            {beat && (
              <circle
                cx={cx}
                cy={y(r.a)}
                r="4.5"
                fill="none"
                stroke={r.a >= 0 ? 'var(--up)' : 'var(--down)'}
                strokeWidth="0.9"
                opacity="0.45"
              />
            )}
            <circle cx={cx} cy={y(r.a)} r="2.6" fill={r.a >= 0 ? 'var(--up)' : 'var(--down)'}>
              <animate attributeName="r" from="0" to="2.6" dur="320ms" begin={`${400 + i * 80}ms`} fill="freeze" />
            </circle>
          </g>
        );
      })}
    </svg>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Mini visualization · ML quantile band (Model lens)
// ──────────────────────────────────────────────────────────────────────────
function MiniQuantile() {
  return (
    <svg viewBox="0 0 200 64" style={{ width: '100%', height: 64, display: 'block' }} aria-hidden="true">
      <defs>
        <linearGradient id="mini-q-out" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0%" stopColor="var(--flag)" stopOpacity="0.15" />
          <stop offset="100%" stopColor="var(--flag)" stopOpacity="0.30" />
        </linearGradient>
        <linearGradient id="mini-q-in" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0%" stopColor="var(--flag)" stopOpacity="0.55" />
          <stop offset="100%" stopColor="var(--flag)" stopOpacity="0.75" />
        </linearGradient>
      </defs>
      <rect x="14" y="28" width="172" height="14" rx="7" fill="color-mix(in oklab, var(--bg-3) 80%, transparent)" />
      <rect x="34" y="28" width="132" height="14" rx="7" fill="url(#mini-q-out)">
        <animate attributeName="width" from="0" to="132" dur="800ms" fill="freeze" />
      </rect>
      <rect x="62" y="28" width="76" height="14" rx="7" fill="url(#mini-q-in)">
        <animate attributeName="width" from="0" to="76" dur="800ms" begin="200ms" fill="freeze" />
      </rect>
      <line
        x1="100"
        x2="100"
        y1="22"
        y2="48"
        stroke="var(--flag)"
        strokeWidth="2"
        style={{ filter: 'drop-shadow(0 0 4px var(--flag))' }}
      />
      <text x="34" y="20" textAnchor="middle" fontSize="8.5" fill="var(--ink-3)" fontFamily="ui-monospace, monospace">P10</text>
      <text x="100" y="20" textAnchor="middle" fontSize="9" fill="var(--flag)" fontFamily="ui-monospace, monospace" fontWeight="700">P50</text>
      <text x="166" y="20" textAnchor="middle" fontSize="8.5" fill="var(--ink-3)" fontFamily="ui-monospace, monospace">P90</text>
      <text x="34" y="60" textAnchor="middle" fontSize="8" fill="var(--ink-4)" fontFamily="ui-monospace, monospace">2.1%</text>
      <text x="100" y="60" textAnchor="middle" fontSize="8" fill="var(--ink-4)" fontFamily="ui-monospace, monospace">6.3%</text>
      <text x="166" y="60" textAnchor="middle" fontSize="8" fill="var(--ink-4)" fontFamily="ui-monospace, monospace">9.9%</text>
    </svg>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Pipeline diagram — animated line + 4 step cards
// ──────────────────────────────────────────────────────────────────────────
type Step = {
  kicker: string;
  title: string;
  body: string;
  tone: string;
  icon: 'chain' | 'math' | 'history' | 'score';
};

function StepIcon({ kind, color }: { kind: Step['icon']; color: string }) {
  const ICON_SIZE = 26;
  if (kind === 'chain') {
    return (
      <svg width={ICON_SIZE} height={ICON_SIZE} viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <rect x="3" y="4" width="18" height="3" rx="1" stroke={color} strokeWidth="1.6" />
        <rect x="3" y="10.5" width="18" height="3" rx="1" stroke={color} strokeWidth="1.6" />
        <rect x="3" y="17" width="18" height="3" rx="1" stroke={color} strokeWidth="1.6" />
      </svg>
    );
  }
  if (kind === 'math') {
    return (
      <svg width={ICON_SIZE} height={ICON_SIZE} viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M5 12c2-7 5-7 7 0s5 7 7 0" stroke={color} strokeWidth="1.6" strokeLinecap="round" />
        <circle cx="5" cy="12" r="1.4" fill={color} />
        <circle cx="19" cy="12" r="1.4" fill={color} />
      </svg>
    );
  }
  if (kind === 'history') {
    return (
      <svg width={ICON_SIZE} height={ICON_SIZE} viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <rect x="4" y="13" width="3" height="7" rx="0.5" fill={color} opacity="0.5" />
        <rect x="9" y="9" width="3" height="11" rx="0.5" fill={color} opacity="0.7" />
        <rect x="14" y="6" width="3" height="14" rx="0.5" fill={color} opacity="0.85" />
        <rect x="19" y="3" width="3" height="17" rx="0.5" fill={color} />
      </svg>
    );
  }
  return (
    <svg width={ICON_SIZE} height={ICON_SIZE} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="8" stroke={color} strokeWidth="1.6" />
      <path d="M12 8v4l3 2" stroke={color} strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function Pipeline() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === 'undefined') {
      setShown(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            setShown(true);
            io.disconnect();
            break;
          }
        }
      },
      { threshold: 0.3 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const steps: Step[] = [
    {
      kicker: '01',
      title: 'Chain',
      // Real pipeline: Polygon/Finnhub option chains land in DuckDB-backed
      // parquet via scripts/sync_dolthub.py and the hourly daily_score job.
      body:
        'Hourly OPRA chain snapshots covering every listed expiry and every strike. Landed into a DuckDB-backed parquet warehouse.',
      tone: 'var(--brand-blue-1)',
      icon: 'chain',
    },
    {
      kicker: '02',
      title: 'Math',
      body:
        'Black–Scholes with dividend yield. Brent solver for IV to 1e-6. Term IV, ATM skew, vega, straddle EM and 80% bands on the print expiry.',
      tone: 'var(--accent-hi)',
      icon: 'math',
    },
    {
      kicker: '03',
      title: 'History',
      body:
        'Realized close-to-close moves bracketed by Finnhub-grade earnings timing (BMO/AMC). Twelve quarters per name; EPS / revenue overlay where available.',
      tone: 'var(--up)',
      icon: 'history',
    },
    {
      kicker: '04',
      title: 'Score',
      body:
        'Rich-vs-hist edge, IV rank vs trailing 52w, and the LightGBM ensemble’s edge over options. Names ranked so the interesting ones rise to the top of the screener.',
      tone: 'var(--flag)',
      icon: 'score',
    },
  ];

  return (
    <div ref={ref}>
      <svg
        viewBox="0 0 760 100"
        style={{ width: '100%', height: 100, display: 'block', marginBottom: 14 }}
        aria-hidden="true"
      >
        <line x1="80" x2="680" y1="50" y2="50" stroke="var(--line)" strokeWidth="1" strokeDasharray="3 5" />
        <line
          x1="80"
          x2="680"
          y1="50"
          y2="50"
          stroke="url(#pipeline-grad)"
          strokeWidth="2.5"
          strokeDasharray="600"
          strokeDashoffset={shown ? 0 : 600}
          style={{ transition: 'stroke-dashoffset 1800ms cubic-bezier(.4,0,.2,1) 200ms' }}
        />
        <defs>
          <linearGradient id="pipeline-grad" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" stopColor="var(--brand-blue-1)" />
            <stop offset="33%" stopColor="var(--accent-hi)" />
            <stop offset="66%" stopColor="var(--up)" />
            <stop offset="100%" stopColor="var(--flag)" />
          </linearGradient>
        </defs>
        {steps.map((s, i) => {
          const cx = 80 + i * 200;
          return (
            <g key={s.kicker}>
              <circle cx={cx} cy="50" r="22" fill="var(--bg-2)" stroke="var(--line)" strokeWidth="1" />
              <circle
                cx={cx}
                cy="50"
                r="22"
                fill="none"
                stroke={s.tone}
                strokeWidth="1.6"
                strokeDasharray="138"
                strokeDashoffset={shown ? 0 : 138}
                style={{
                  transition: `stroke-dashoffset 700ms cubic-bezier(.4,0,.2,1) ${300 + i * 200}ms`,
                }}
              />
              <text
                x={cx}
                y="55"
                textAnchor="middle"
                fontSize="12"
                fontFamily="Mulish, Nunito Sans, sans-serif"
                fontWeight="800"
                letterSpacing="0.04em"
                fill={s.tone}
              >
                {s.kicker}
              </text>
            </g>
          );
        })}
        {shown && (
          <circle r="4" fill="var(--ink)" opacity="0.8">
            <animateMotion dur="4500ms" repeatCount="indefinite" path="M 80 50 L 680 50" />
            <animate attributeName="opacity" values="0;1;0" dur="4500ms" repeatCount="indefinite" />
          </circle>
        )}
      </svg>

      <div
        className="qv-m-2col"
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
          gap: 10,
          marginTop: 4,
        }}
      >
        {steps.map((s, i) => (
          <div
            key={s.kicker}
            style={{
              padding: '16px 16px 18px',
              background: `linear-gradient(180deg,
                color-mix(in oklab, ${s.tone} 8%, var(--bg-2)),
                var(--bg-2))`,
              borderRadius: 12,
              border: '1px solid var(--line)',
              transition:
                'transform 600ms cubic-bezier(.2,.8,.3,1) ' +
                (200 + i * 180) +
                'ms, opacity 600ms ease ' +
                (200 + i * 180) +
                'ms',
              transform: shown ? 'translateY(0)' : 'translateY(10px)',
              opacity: shown ? 1 : 0,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <StepIcon kind={s.icon} color={s.tone} />
              <span
                style={{
                  fontSize: 10,
                  letterSpacing: '0.18em',
                  textTransform: 'uppercase',
                  color: s.tone,
                  fontWeight: 700,
                }}
              >
                {s.kicker}
              </span>
            </div>
            <h3
              className="serif"
              style={{
                margin: 0,
                fontSize: 18,
                fontWeight: 700,
                color: 'var(--ink)',
                letterSpacing: '-0.01em',
              }}
            >
              {s.title}
            </h3>
            <p style={{ margin: '8px 0 0', fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.5 }}>
              {s.body}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Tex · KaTeX-rendered LaTeX. Render to HTML string once per formula and
// drop into a span. Display mode for block equations, inline otherwise.
// ──────────────────────────────────────────────────────────────────────────
function Tex({ math, displayMode = true }: { math: string; displayMode?: boolean }) {
  const html = useMemo(
    () =>
      katex.renderToString(math, {
        displayMode,
        throwOnError: false,
        output: 'html',
      }),
    [math, displayMode],
  );
  return (
    <span
      className="qv-tex"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Methodology · formula card row
// ──────────────────────────────────────────────────────────────────────────
function FormulaCard({
  kicker,
  title,
  body,
  tex,
}: {
  kicker: string;
  title: string;
  body: string;
  tex: string;
}) {
  return (
    <div
      style={{
        padding: '20px 22px',
        borderRadius: 14,
        border: '1px solid var(--line)',
        background: 'var(--bg-2)',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}
    >
      <div
        style={{
          fontSize: 10,
          letterSpacing: '0.18em',
          textTransform: 'uppercase',
          color: 'var(--ink-3)',
          fontWeight: 700,
        }}
      >
        {kicker}
      </div>
      <h3
        className="serif"
        style={{
          margin: 0,
          fontSize: 18,
          fontWeight: 700,
          color: 'var(--ink)',
          letterSpacing: '-0.01em',
        }}
      >
        {title}
      </h3>
      <div
        style={{
          padding: '14px 16px',
          borderRadius: 8,
          background: 'color-mix(in oklab, var(--bg-3) 50%, transparent)',
          border: '1px solid color-mix(in oklab, var(--line) 60%, transparent)',
          color: 'var(--ink)',
          overflowX: 'auto',
        }}
      >
        <Tex math={tex} />
      </div>
      <p style={{ margin: 0, fontSize: 12.5, color: 'var(--ink-3)', lineHeight: 1.55 }}>{body}</p>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Main About
// ──────────────────────────────────────────────────────────────────────────
export default function AboutPageClient() {
  const pageRef = useRef<HTMLDivElement | null>(null);
  const pageSettled = usePageSettled(pageRef);

  // Anchored to actual repo facts:
  //  · names tracked: ≈ S&P 500 + active Russell + popular names
  //  · chains snapped per week: ~500 names × 4 expiries × 5 trading days
  //    × ~6 hourly snapshots during market hours.
  //  · history depth: realized close-to-close moves go back further at the
  //    universe level than the 12 quarters we persist per name.
  //  · refresh: chain-to-UI latency target, capped at the cron interval.
  const stats = [
    { v: 1424, suf: '', dec: 0, kicker: 'Names', label: 'tracked across our universe' },
    { v: 12.4, suf: 'K', dec: 1, kicker: 'Chains', label: 'snapped per week' },
    { v: 8, suf: ' yrs', dec: 0, kicker: 'History', label: 'of realized data' },
    { v: 60, suf: ' min', dec: 0, kicker: 'Refresh', label: 'chain to UI latency' },
  ];

  return (
    <div
      ref={pageRef}
      className="qv-m-pad"
      style={{ maxWidth: 980, margin: '0 auto', padding: '0 28px 80px' }}
    >
      {/* Hero */}
      <Reveal enabled={pageSettled}>
        <div
          className="qv-m-stack qv-about-hero"
          style={{
            padding: '44px 0 32px',
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 1.4fr) minmax(0, 1fr)',
            gap: 32,
            alignItems: 'center',
          }}
        >
          <div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                fontSize: 10.5,
                letterSpacing: '0.2em',
                textTransform: 'uppercase',
                color: 'var(--ink-3)',
                marginBottom: 18,
                fontWeight: 600,
              }}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/brand/QuantivIcon.png"
                alt=""
                width={18}
                height={18}
                style={{
                  display: 'inline-block',
                  objectFit: 'contain',
                  mixBlendMode: 'screen',
                }}
              />
              <span>About</span>
              <span style={{ color: 'var(--ink-4)' }}>·</span>
              <span>Quantiv</span>
            </div>
            <h1
              className="serif qv-m-h-hero"
              style={{
                margin: 0,
                fontSize: 76,
                fontWeight: 800,
                letterSpacing: '-0.04em',
                lineHeight: 0.9,
                color: 'var(--ink)',
                textTransform: 'uppercase',
              }}
            >
              What options
              <br />
              <span
                style={{
                  background:
                    'linear-gradient(135deg, var(--brand-blue-1), var(--accent-hi))',
                  WebkitBackgroundClip: 'text',
                  WebkitTextFillColor: 'transparent',
                  backgroundClip: 'text',
                }}
              >
                are saying.
              </span>
            </h1>
            <p
              style={{
                marginTop: 24,
                fontSize: 18,
                color: 'var(--ink-2)',
                lineHeight: 1.55,
                letterSpacing: '-0.005em',
                maxWidth: 520,
              }}
            >
              Quantiv reads the options chain like a tape. For every print we
              measure what the market is paying for movement, what the stock
              has actually delivered across the last twelve quarters, and where
              today&apos;s premium sits inside its own 52-week history.
            </p>
          </div>
          <div style={{ justifySelf: 'end' }}>
            <HeroGlyph enabled={pageSettled} />
          </div>
        </div>
      </Reveal>

      {/* Live stats row */}
      <Reveal enabled={pageSettled} delay={60}>
        <div
          className="qv-m-2col qv-about-stats"
          style={{
            borderTop: '1px solid var(--line)',
            borderBottom: '1px solid var(--line)',
            display: 'grid',
            gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
            padding: '20px 0',
          }}
        >
          {stats.map((s, i) => (
            <div
              key={s.kicker}
              style={{
                padding: '0 22px',
                borderLeft: i === 0 ? 'none' : '1px solid var(--line)',
              }}
            >
              <div
                style={{
                  fontSize: 9.5,
                  letterSpacing: '0.2em',
                  textTransform: 'uppercase',
                  color: 'var(--ink-3)',
                  fontWeight: 600,
                  marginBottom: 8,
                }}
              >
                {s.kicker}
              </div>
              <div
                style={{
                  fontSize: 38,
                  fontWeight: 800,
                  letterSpacing: '-0.03em',
                  color: 'var(--ink)',
                  lineHeight: 1,
                }}
              >
                <CountUp
                  enabled={pageSettled}
                  value={s.v}
                  suffix={s.suf}
                  decimals={s.dec}
                  duration={COUNT_UP_DURATION_MS + i * 240}
                />
              </div>
              <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 6 }}>{s.label}</div>
            </div>
          ))}
        </div>
      </Reveal>

      {/* What we measure · three lenses with mini visualizations */}
      <Reveal enabled={pageSettled} delay={100}>
        <div style={{ padding: '44px 0 16px' }}>
          <div
            style={{
              fontSize: 10,
              letterSpacing: '0.18em',
              textTransform: 'uppercase',
              color: 'var(--ink-3)',
              fontWeight: 600,
              marginBottom: 10,
            }}
          >
            What we measure
          </div>
          <h2
            className="serif"
            style={{
              margin: 0,
              fontSize: 36,
              fontWeight: 800,
              letterSpacing: '-0.025em',
              lineHeight: 1,
              color: 'var(--ink)',
            }}
          >
            Three lenses on every print.
          </h2>
        </div>
        <div
          className="qv-m-stack"
          style={{
            marginTop: 16,
            display: 'grid',
            gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
            gap: 14,
          }}
        >
          {(
            [
              {
                kicker: 'Pricing',
                title: 'Implied move',
                body:
                  'The print-expiry ATM straddle prices a ±1σ move. We surface both straddle-implied EM and the IV-implied EM (S₀·σ_ATM·√T) side-by-side. When they diverge, the gap is the skew premium dealers are charging.',
                tone: 'var(--brand-blue-1)',
                viz: <MiniStraddleBar />,
              },
              {
                kicker: 'History',
                title: 'Realized track record',
                body:
                  'Close-to-close moves over the last twelve quarters, bracketed by BMO/AMC timing. Hist edge = (straddle EM − 4Q realized avg) / 4Q realized avg. Positive when options are pricing it richer than the stock has delivered.',
                tone: 'var(--up)',
                viz: <MiniHistory />,
              },
              {
                kicker: 'Model',
                title: 'ML forecast',
                body:
                  'A LightGBM ensemble trained walk-forward on chain features (term IV, skew, vega, DTE) and realized history. Outputs P10–P90 quantiles of |move|. Tight 80% bands = the model is confident; wide bands = priced uncertainty.',
                tone: 'var(--flag)',
                viz: <MiniQuantile />,
              },
            ] as const
          ).map((c) => (
            <div
              key={c.title}
              style={{
                borderRadius: 14,
                border: '1px solid var(--line)',
                background: `linear-gradient(180deg,
                  color-mix(in oklab, ${c.tone} 7%, var(--bg-2)) 0%,
                  var(--bg-2) 70%)`,
                padding: '20px 22px 22px',
                display: 'flex',
                flexDirection: 'column',
                gap: 12,
              }}
            >
              <div
                style={{
                  fontSize: 10,
                  letterSpacing: '0.18em',
                  textTransform: 'uppercase',
                  color: c.tone,
                  fontWeight: 700,
                }}
              >
                {c.kicker}
              </div>
              <div
                style={{
                  background: 'color-mix(in oklab, var(--bg-3) 30%, transparent)',
                  borderRadius: 10,
                  padding: '10px 12px 8px',
                  border: '1px solid color-mix(in oklab, var(--line) 60%, transparent)',
                }}
              >
                {c.viz}
              </div>
              <div>
                <h3
                  className="serif"
                  style={{
                    margin: 0,
                    fontSize: 19,
                    fontWeight: 700,
                    letterSpacing: '-0.01em',
                    color: 'var(--ink)',
                    lineHeight: 1.1,
                  }}
                >
                  {c.title}
                </h3>
                <div style={{ marginTop: 6, fontSize: 12.5, color: 'var(--ink-3)', lineHeight: 1.5 }}>
                  {c.body}
                </div>
              </div>
            </div>
          ))}
        </div>
      </Reveal>

      {/* Models & math · formulas + plain-English notes */}
      <Reveal enabled={pageSettled} delay={120}>
        <div
          style={{
            padding: '44px 0 8px',
            borderTop: '1px solid var(--line)',
            marginTop: 40,
          }}
        >
          <div
            style={{
              fontSize: 10,
              letterSpacing: '0.18em',
              textTransform: 'uppercase',
              color: 'var(--ink-3)',
              fontWeight: 600,
              marginBottom: 10,
            }}
          >
            Models &amp; math
          </div>
          <h2
            className="serif"
            style={{
              margin: 0,
              fontSize: 36,
              fontWeight: 800,
              letterSpacing: '-0.025em',
              lineHeight: 1,
              color: 'var(--ink)',
            }}
          >
            The pricing engine, in seven lines.
          </h2>
          <p
            style={{
              margin: '14px 0 0',
              // Widened from 660 → 820 so the right edge sits close to
              // the formula-card row below it, without letting body
              // copy run past ~115 characters per line.
              maxWidth: 820,
              fontSize: 14,
              color: 'var(--ink-3)',
              lineHeight: 1.6,
            }}
          >
            Every chart on the ticker page traces back to one of these formulas.
            We show the math because the assumptions behind it (log-normal
            returns, constant volatility over the horizon) matter for how
            you read the output.
          </p>
        </div>

        <div
          className="qv-m-stack"
          style={{
            marginTop: 18,
            display: 'grid',
            gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
            gap: 14,
          }}
        >
          <FormulaCard
            kicker="Straddle EM"
            title="What dealers are pricing"
            tex={String.raw`\mathrm{EM}_{\text{straddle}} \;=\; \frac{c_{\mathrm{ATM}} \,+\, p_{\mathrm{ATM}}}{S_0}`}
            body="ATM straddle mid divided by spot. The print-expiry straddle prices a ±1σ
              move at expiry. Collect it if you think the stock will move less than
              implied; pay it if you think more."
          />
          <FormulaCard
            kicker="IV-based EM"
            title="Scale IV to the horizon"
            tex={String.raw`\mathrm{EM}_{\mathrm{IV}} \;=\; \sigma_{\mathrm{ATM}} \,\cdot\, \sqrt{\tfrac{\mathrm{DTE}}{365}}`}
            body="ATM IV is annualized. To compare it with the straddle move, we
              scale it down to the print expiry. Front-month IV bakes in earnings
              risk; the next expiry is your &lsquo;quieter&rsquo; reference."
          />
          <FormulaCard
            kicker="Greeks"
            title="Black–Scholes with dividends"
            tex={String.raw`\begin{aligned}
              d_1 &= \tfrac{\ln(S/K) + (r - q + \tfrac{1}{2}\sigma^{2})\,T}{\sigma\sqrt{T}} \\[2pt]
              \Delta_{\text{call}} &= e^{-qT}\,N(d_1) \qquad \Gamma = \tfrac{e^{-qT}\,\varphi(d_1)}{S\,\sigma\sqrt{T}} \\[2pt]
              \nu &= S\,e^{-qT}\,\varphi(d_1)\,\sqrt{T}
              \end{aligned}`}
            body="IV is solved with Brent&apos;s method to 1e-6 tolerance from mid quotes.
              Greeks are surfaced per expiry so you can see how delta-flat your
              position is, how much it&apos;ll move on a 1-vol jump, and how fast
              theta accelerates into print."
          />
          <FormulaCard
            kicker="Density"
            title="Two-sided log-normal probability"
            tex={String.raw`\begin{aligned}
              z_{+} &= \tfrac{\ln(1 + |x|)}{\sigma} \qquad z_{-} = \tfrac{\ln(1 - |x|)}{\sigma} \\[2pt]
              P\!\left(\,\left|\tfrac{S}{S_0} - 1\right| \ge |x|\,\right) &= \bigl(1 - \Phi(z_{+})\bigr) + \Phi(z_{-})
              \end{aligned}`}
            body="The hover probability on the density bar uses the correct asymmetric
              form. Naïve 2·(1 − Φ(z₊)) slightly overstates the tail because the
              downside in simple-return space is fatter than the upside."
          />
          <FormulaCard
            kicker="Hist edge"
            title="Rich versus what actually printed"
            tex={String.raw`\text{hist\_edge} \;=\; \frac{\mathrm{EM}_{\text{straddle}} \,-\, \mu_{4\mathrm{Q},\,|\Delta|}}{\mu_{4\mathrm{Q},\,|\Delta|}}`}
            body="Compares today's implied move to the average |close-to-close| over
              the last four prints. ≥ +20% = options are pricing the print at least a
              fifth richer than recent history. Sample size is small; treat as a
              quick prior, not a signal."
          />
          <FormulaCard
            kicker="Forecast"
            title="LightGBM quantile ensemble"
            tex={String.raw`\hat{y}_{\tau} \;=\; \arg\min_{\hat{y}}\,\sum_{i} \rho_{\tau}\!\bigl(y_{i} - \hat{y}\bigr),\quad \tau \in \{0.10,\,0.25,\,0.50,\,0.75,\,0.90\}`}
            body="Five gradient-boosted models, one per quantile of |move|, trained
              walk-forward across every observed earnings event in the universe with
              no look-ahead. The 80% band P10–P90 is the model&apos;s confidence
              interval, not a guarantee."
          />
        </div>
      </Reveal>

      {/* Pipeline */}
      <Reveal enabled={pageSettled} delay={160}>
        <div
          style={{
            padding: '44px 0 8px',
            borderTop: '1px solid var(--line)',
            marginTop: 40,
          }}
        >
          <div
            style={{
              fontSize: 10,
              letterSpacing: '0.18em',
              textTransform: 'uppercase',
              color: 'var(--ink-3)',
              fontWeight: 600,
              marginBottom: 10,
            }}
          >
            How it works
          </div>
          <h2
            className="serif"
            style={{
              margin: 0,
              fontSize: 36,
              fontWeight: 800,
              letterSpacing: '-0.025em',
              lineHeight: 1,
              color: 'var(--ink)',
            }}
          >
            From chains to decisions, hourly.
          </h2>
        </div>
        <div style={{ marginTop: 18 }}>
          <Pipeline />
        </div>
      </Reveal>

      {/* Closing note */}
      <Reveal enabled={pageSettled} delay={200}>
        <div
          className="qv-m-stack qv-about-quote"
          style={{
            marginTop: 40,
            borderRadius: 18,
            padding: '32px 34px',
            background:
              'radial-gradient(120% 140% at 0% 0%, color-mix(in oklab, var(--brand-blue-1) 16%, transparent) 0%, transparent 55%), radial-gradient(80% 100% at 100% 100%, color-mix(in oklab, var(--accent) 10%, transparent) 0%, transparent 60%), linear-gradient(180deg, color-mix(in oklab, var(--bg-2) 92%, transparent), color-mix(in oklab, var(--bg-3) 70%, transparent))',
            border: '1px solid color-mix(in oklab, var(--brand-blue-1) 22%, var(--line))',
            display: 'grid',
            gridTemplateColumns: 'auto 1fr',
            gap: 28,
            alignItems: 'center',
          }}
        >
          <div
            style={{
              fontFamily: 'Mulish, serif',
              fontSize: 120,
              fontWeight: 800,
              color: 'var(--brand-blue-1)',
              lineHeight: 0.6,
              letterSpacing: '-0.05em',
              opacity: 0.85,
            }}
          >
            &ldquo;
          </div>
          <div>
            <div
              className="serif"
              style={{
                fontSize: 22,
                fontWeight: 600,
                color: 'var(--ink)',
                letterSpacing: '-0.01em',
                lineHeight: 1.35,
              }}
            >
              Quantiv is a research tool, not a recommendation. The same option
              chain can support opposite trades depending on conviction, position,
              and risk tolerance. We surface signal; you bring judgement, and
              read the small print on every formula above.
            </div>
          </div>
        </div>
      </Reveal>

      {/* Footer */}
      <Reveal enabled={pageSettled} delay={240}>
        <div
          style={{
            marginTop: 44,
            paddingTop: 24,
            borderTop: '1px solid var(--line)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: 16,
          }}
        >
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/brand/QuantivIcon.png"
              alt=""
              width={24}
              height={24}
              style={{
                display: 'inline-block',
                objectFit: 'contain',
                mixBlendMode: 'screen',
              }}
            />
            <span
              className="serif"
              style={{
                fontSize: 17,
                fontWeight: 700,
                color: 'var(--ink)',
                letterSpacing: '-0.01em',
              }}
            >
              Quantiv
            </span>
          </div>
          <Link
            href="/"
            onClick={() => {
              // App-Router's default scroll-to-top can land the user
              // mid-page when the source route was scrolled (this CTA
              // lives near the bottom of /about). Queueing a manual
              // scroll for the next tick forces the calendar to land
              // at its title.
              if (typeof window !== 'undefined') {
                setTimeout(() => window.scrollTo({ top: 0, left: 0 }), 0);
              }
            }}
            style={{
              padding: '11px 20px',
              borderRadius: 999,
              border: '1px solid var(--brand-blue-1)',
              fontSize: 13.5,
              color: 'var(--ink)',
              fontWeight: 600,
              letterSpacing: '-0.005em',
              background: 'color-mix(in oklab, var(--brand-blue-1) 18%, transparent)',
              textDecoration: 'none',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              transition: 'background 140ms ease',
            }}
          >
            Open the Earnings Calendar
            <span style={{ fontSize: 14 }}>→</span>
          </Link>
        </div>
      </Reveal>

      {/* Disclaimer */}
      <Reveal enabled={pageSettled} delay={280}>
        <div
          style={{
            marginTop: 22,
            padding: 18,
            border: '1px solid var(--line)',
            borderRadius: 10,
            fontSize: 12.5,
            color: 'var(--ink-3)',
            lineHeight: 1.55,
          }}
        >
          <strong style={{ color: 'var(--ink-2)' }}>Disclaimer.</strong> Quantiv is
          for educational and informational use only. Options trading carries
          substantial risk including loss of principal. Implied volatility,
          model quantiles, and historical realized moves are descriptive
          statistics, not predictions. Past performance does not guarantee
          future results. Nothing on this site is investment advice.
        </div>
      </Reveal>
    </div>
  );
}
