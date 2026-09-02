"use client";

import Image from "next/image";
import Link from "next/link";
import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";

import { MathFormula } from "@/components/MathFormula";
import {
  ABOUT_STATS,
  ABOUT_STORIES,
  METHODOLOGY_SECTIONS,
  type AboutStoryKind,
} from "./aboutContent";

const COUNT_UP_DURATION_MS = 1800;

function useReducedMotion() {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReduced(media.matches);
    sync();
    media.addEventListener?.("change", sync);
    return () => media.removeEventListener?.("change", sync);
  }, []);

  return reduced;
}

function Reveal({
  children,
  delay = 0,
  style,
}: {
  children: ReactNode;
  delay?: number;
  style?: CSSProperties;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    if (typeof IntersectionObserver === "undefined") {
      setShown(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setShown(true);
          observer.disconnect();
        }
      },
      { threshold: 0.08, rootMargin: "0px 0px -32px 0px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={`reveal${shown ? " in" : ""}`}
      style={{ transitionDelay: `${delay}ms`, ...style }}
    >
      {children}
    </div>
  );
}

function CountUp({
  value,
  from,
  suffix,
  decimals,
  delay = 0,
}: {
  value: number;
  from: number;
  suffix: string;
  decimals: number;
  delay?: number;
}) {
  const ref = useRef<HTMLSpanElement | null>(null);
  const [current, setCurrent] = useState(from);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    if (reducedMotion || typeof IntersectionObserver === "undefined") {
      setCurrent(value);
      return;
    }

    let frame: number | null = null;
    let timeout: number | null = null;
    let started = false;
    const observer = new IntersectionObserver(
      (entries) => {
        if (!started && entries.some((entry) => entry.isIntersecting)) {
          started = true;
          observer.disconnect();
          timeout = window.setTimeout(() => {
            const start = performance.now();
            const tick = (time: number) => {
              const progress = Math.min(1, (time - start) / COUNT_UP_DURATION_MS);
              const eased = 1 - Math.pow(1 - progress, 3);
              setCurrent(from + (value - from) * eased);
              if (progress < 1) frame = requestAnimationFrame(tick);
            };
            frame = requestAnimationFrame(tick);
          }, delay);
        }
      },
      { threshold: 0.35 },
    );
    observer.observe(element);

    return () => {
      observer.disconnect();
      if (timeout !== null) window.clearTimeout(timeout);
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [delay, decimals, from, reducedMotion, suffix, value]);

  return (
    <span ref={ref} className="serif tnum">
      {current.toFixed(decimals)}
      {suffix}
    </span>
  );
}

function HeroGlyph() {
  const ref = useRef<HTMLDivElement | null>(null);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    if (reducedMotion || typeof IntersectionObserver === "undefined") {
      element.classList.add("in");
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          element.classList.add("in");
          observer.disconnect();
        }
      },
      { threshold: 0.3 },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [reducedMotion]);

  return (
    <div ref={ref} className="quantiv-hero-q" aria-hidden="true">
      <Image
        src="/brand/QuantivSplashQClosed.webp"
        alt=""
        width={480}
        height={480}
        className="quantiv-hero-q-layer quantiv-hero-q-ring"
        draggable={false}
        priority
      />
      <div className="quantiv-hero-q-slit" />
      <div className="quantiv-hero-q-tail-clip">
        <Image
          src="/brand/QuantivSplashTail.webp"
          alt=""
          width={480}
          height={480}
          className="quantiv-hero-q-layer"
          draggable={false}
          priority
        />
      </div>
    </div>
  );
}

function MarketVisual({ animated }: { animated: boolean }) {
  return (
    <svg
      viewBox="0 0 360 180"
      role="img"
      aria-label="Animated ATM straddle range centered on spot"
      style={{ width: "100%", height: "auto", display: "block" }}
    >
      <defs>
        <linearGradient id="about-market-band" x1="0" x2="1">
          <stop offset="0%" stopColor="var(--brand-blue-2)" stopOpacity="0.55" />
          <stop offset="100%" stopColor="var(--brand-blue-1)" stopOpacity="0.9" />
        </linearGradient>
      </defs>
      <line x1="28" x2="332" y1="94" y2="94" stroke="var(--line-2)" />
      <rect x="92" y="78" width="176" height="32" rx="16" fill="url(#about-market-band)">
        {animated ? (
          <animate
            attributeName="x"
            values="92;84;92"
            dur="2.8s"
            repeatCount="indefinite"
          />
        ) : null}
        {animated ? (
          <animate
            attributeName="width"
            values="176;192;176"
            dur="2.8s"
            repeatCount="indefinite"
          />
        ) : null}
      </rect>
      <line x1="180" x2="180" y1="52" y2="132" stroke="var(--ink)" strokeWidth="2" />
      <circle cx="180" cy="94" r="6" fill="var(--ink)" />
      <circle cx="92" cy="94" r="5" fill="var(--down)">
        {animated ? (
          <animate attributeName="r" values="4;7;4" dur="2.8s" repeatCount="indefinite" />
        ) : null}
      </circle>
      <circle cx="268" cy="94" r="5" fill="var(--up)">
        {animated ? (
          <animate attributeName="r" values="4;7;4" dur="2.8s" repeatCount="indefinite" />
        ) : null}
      </circle>
      <text x="180" y="38" textAnchor="middle" fill="var(--ink-3)" fontSize="11">
        SPOT
      </text>
      <text x="92" y="142" textAnchor="middle" fill="var(--down)" fontSize="12">
        −6.0%
      </text>
      <text x="268" y="142" textAnchor="middle" fill="var(--up)" fontSize="12">
        +6.0%
      </text>
      <text x="180" y="166" textAnchor="middle" fill="var(--ink-4)" fontSize="10">
        ATM call + put
      </text>
    </svg>
  );
}

function HistoryVisual({ animated }: { animated: boolean }) {
  const events = [
    { x: 46, priced: 22, actual: -14 },
    { x: 84, priced: 24, actual: 31 },
    { x: 122, priced: 20, actual: 11 },
    { x: 160, priced: 26, actual: -34 },
    { x: 198, priced: 23, actual: 28 },
    { x: 236, priced: 27, actual: -19 },
    { x: 274, priced: 25, actual: 36 },
    { x: 312, priced: 28, actual: -23 },
  ];
  const y = (move: number) => 92 - move * 1.55;

  return (
    <svg
      viewBox="0 0 360 180"
      role="img"
      aria-label="Animated historical earnings moves compared with their priced option ranges"
      style={{ width: "100%", height: "auto", display: "block" }}
    >
      <line x1="26" x2="334" y1="92" y2="92" stroke="var(--line-2)" />
      {events.map((event, index) => (
        <g key={event.x}>
          <rect
            x={event.x - 7}
            y={y(event.priced)}
            width="14"
            height={y(-event.priced) - y(event.priced)}
            rx="4"
            fill="color-mix(in oklab, var(--brand-blue-1) 18%, transparent)"
            stroke="color-mix(in oklab, var(--brand-blue-1) 55%, transparent)"
          />
          <circle
            cx={event.x}
            cy={y(event.actual)}
            r="4"
            fill={event.actual >= 0 ? "var(--up)" : "var(--down)"}
          >
            {animated ? (
              <animate
                attributeName="opacity"
                values="0.2;1;1"
                dur="2.4s"
                begin={`${index * 0.18}s`}
                repeatCount="indefinite"
              />
            ) : null}
            {animated ? (
              <animate
                attributeName="r"
                values="1;5;4"
                dur="1.2s"
                begin={`${index * 0.18}s`}
                repeatCount="indefinite"
              />
            ) : null}
          </circle>
        </g>
      ))}
      <text x="30" y="24" fill="var(--ink-4)" fontSize="10">
        priced range
      </text>
      <circle cx="112" cy="20" r="3" fill="var(--up)" />
      <text x="120" y="24" fill="var(--ink-4)" fontSize="10">
        realized move
      </text>
      <text x="180" y="166" textAnchor="middle" fill="var(--ink-4)" fontSize="10">
        each dot = one earnings reaction
      </text>
    </svg>
  );
}

function ModelVisual({ animated }: { animated: boolean }) {
  return (
    <svg
      viewBox="0 0 360 180"
      role="img"
      aria-label="Animated LightGBM quantile range with the option straddle as a comparison threshold"
      style={{ width: "100%", height: "auto", display: "block" }}
    >
      <rect x="42" y="70" width="276" height="42" rx="21" fill="var(--bg-3)" />
      <rect x="70" y="70" width="220" height="42" rx="21" fill="color-mix(in oklab, var(--flag) 22%, transparent)">
        {animated ? (
          <animate attributeName="width" values="196;220;196" dur="3s" repeatCount="indefinite" />
        ) : null}
      </rect>
      <rect x="118" y="70" width="124" height="42" rx="21" fill="color-mix(in oklab, var(--flag) 58%, transparent)" />
      <line x1="180" x2="180" y1="55" y2="127" stroke="var(--flag)" strokeWidth="3" />
      <line x1="232" x2="232" y1="48" y2="134" stroke="var(--brand-blue-1)" strokeWidth="2" strokeDasharray="4 4">
        {animated ? (
          <animate attributeName="x1" values="224;240;232" dur="3s" repeatCount="indefinite" />
        ) : null}
        {animated ? (
          <animate attributeName="x2" values="224;240;232" dur="3s" repeatCount="indefinite" />
        ) : null}
      </line>
      <text x="70" y="54" textAnchor="middle" fill="var(--ink-3)" fontSize="10">
        P10
      </text>
      <text x="180" y="44" textAnchor="middle" fill="var(--flag)" fontSize="11" fontWeight="700">
        P50
      </text>
      <text x="290" y="54" textAnchor="middle" fill="var(--ink-3)" fontSize="10">
        P90
      </text>
      <text x="232" y="151" textAnchor="middle" fill="var(--brand-blue-1)" fontSize="10">
        straddle
      </text>
      <text x="180" y="170" textAnchor="middle" fill="var(--ink-4)" fontSize="10">
        range first · probability second
      </text>
    </svg>
  );
}

function StoryVisual({ kind, animated }: { kind: AboutStoryKind; animated: boolean }) {
  if (kind === "market") return <MarketVisual animated={animated} />;
  if (kind === "history") return <HistoryVisual animated={animated} />;
  return <ModelVisual animated={animated} />;
}

function StoryCard({
  kind,
  kicker,
  title,
  caption,
  animated,
}: {
  kind: AboutStoryKind;
  kicker: string;
  title: string;
  caption: string;
  animated: boolean;
}) {
  const tone =
    kind === "market"
      ? "var(--brand-blue-1)"
      : kind === "history"
        ? "var(--up)"
        : "var(--flag)";

  return (
    <article
      style={{
        minWidth: 0,
        borderRadius: 16,
        border: "1px solid var(--line)",
        background: `linear-gradient(180deg, color-mix(in oklab, ${tone} 7%, var(--bg-2)), var(--bg-2) 68%)`,
        overflow: "hidden",
      }}
    >
      <div style={{ padding: "12px 14px 0" }}>
        <StoryVisual kind={kind} animated={animated} />
      </div>
      <div style={{ padding: "4px 20px 20px" }}>
        <div
          style={{
            color: tone,
            fontSize: 9.5,
            fontWeight: 700,
            letterSpacing: "0.17em",
            textTransform: "uppercase",
          }}
        >
          {kicker}
        </div>
        <h3
          className="serif"
          style={{
            margin: "7px 0 0",
            color: "var(--ink)",
            fontSize: 21,
            lineHeight: 1.05,
            letterSpacing: "-0.015em",
          }}
        >
          {title}
        </h3>
        <p
          style={{
            margin: "8px 0 0",
            color: "var(--ink-3)",
            fontSize: 12,
            lineHeight: 1.45,
          }}
        >
          {caption}
        </p>
      </div>
    </article>
  );
}

function PublicationFlow({ animated }: { animated: boolean }) {
  const stages = [
    ["01", "Observe", "point-in-time"],
    ["02", "Reconcile", "quality gate"],
    ["03", "Verify", "model gate"],
    ["04", "Publish", "or stop"],
  ] as const;

  return (
    <div
      style={{
        borderRadius: 18,
        border: "1px solid var(--line)",
        background:
          "radial-gradient(70% 100% at 50% 0%, color-mix(in oklab, var(--brand-blue-1) 10%, transparent), transparent 70%), var(--bg-2)",
        padding: "20px 18px 18px",
      }}
    >
      <svg
        viewBox="0 0 760 120"
        role="img"
        aria-label="Animated validation flow from point-in-time data to publication"
        style={{ width: "100%", height: "auto", display: "block" }}
      >
        <defs>
          <linearGradient id="about-control-line" x1="0" x2="1">
            <stop offset="0%" stopColor="var(--brand-blue-1)" />
            <stop offset="55%" stopColor="var(--flag)" />
            <stop offset="100%" stopColor="var(--up)" />
          </linearGradient>
        </defs>
        <line x1="84" x2="676" y1="55" y2="55" stroke="var(--line-2)" strokeWidth="2" />
        <line x1="84" x2="676" y1="55" y2="55" stroke="url(#about-control-line)" strokeWidth="3" strokeDasharray="592" />
        {stages.map(([step, title, detail], index) => {
          const x = 84 + index * 197.3;
          return (
            <g key={step}>
              <circle cx={x} cy="55" r="24" fill="var(--bg-3)" stroke="var(--line-2)" />
              <circle cx={x} cy="55" r="5" fill={index === 3 ? "var(--up)" : "var(--brand-blue-1)"} />
              <text x={x} y="18" textAnchor="middle" fill="var(--ink-4)" fontSize="9">
                {step}
              </text>
              <text x={x} y="96" textAnchor="middle" fill="var(--ink)" fontSize="11" fontWeight="700">
                {title}
              </text>
              <text x={x} y="111" textAnchor="middle" fill="var(--ink-4)" fontSize="9">
                {detail}
              </text>
            </g>
          );
        })}
        {animated ? (
          <circle r="6" fill="var(--ink)">
            <animateMotion dur="4.2s" repeatCount="indefinite" path="M 84 55 L 676 55" />
            <animate attributeName="opacity" values="0;1;1;0" dur="4.2s" repeatCount="indefinite" />
          </circle>
        ) : null}
      </svg>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          padding: "8px 8px 0",
          flexWrap: "wrap",
        }}
      >
        <span style={{ color: "var(--ink-3)", fontSize: 12 }}>
          Critical control fails → publication stops.
        </span>
        <span
          style={{
            borderRadius: 999,
            border: "1px solid color-mix(in oklab, var(--up) 35%, var(--line))",
            background: "color-mix(in oklab, var(--up) 9%, transparent)",
            color: "var(--up)",
            padding: "4px 9px",
            fontSize: 9.5,
            letterSpacing: "0.09em",
            textTransform: "uppercase",
            whiteSpace: "nowrap",
          }}
        >
          Fail closed
        </span>
      </div>
    </div>
  );
}

function FormulaDisclosure({
  id,
  kicker,
  title,
  tex,
  note,
}: {
  id: string;
  kicker: string;
  title: string;
  tex: string;
  note: string;
}) {
  const [isOpen, setIsOpen] = useState(true);

  return (
    <details
      id={id}
      open={isOpen}
      onToggle={(event) => setIsOpen(event.currentTarget.open)}
      style={{
        borderRadius: 12,
        border: "1px solid var(--line)",
        background: "var(--bg-2)",
        overflow: "hidden",
      }}
    >
      <summary
        style={{
          cursor: "pointer",
          listStyle: "none",
          display: "grid",
          gridTemplateColumns: "120px minmax(0, 1fr) auto",
          alignItems: "center",
          gap: 12,
          padding: "15px 17px",
        }}
      >
        <span
          style={{
            color: "var(--brand-blue-1)",
            fontSize: 9.5,
            fontWeight: 700,
            letterSpacing: "0.13em",
            textTransform: "uppercase",
          }}
        >
          {kicker}
        </span>
        <strong className="serif" style={{ color: "var(--ink)", fontSize: 15 }}>
          {title}
        </strong>
        <span
          data-disclosure-glyph
          aria-hidden="true"
          style={{ color: "var(--ink-4)", fontSize: 18 }}
        >
          {isOpen ? "−" : "+"}
        </span>
      </summary>
      <div
        style={{
          borderTop: "1px solid var(--line)",
          padding: "16px 18px 18px",
          display: "grid",
          gap: 12,
        }}
      >
        <div
          style={{
            borderRadius: 9,
            border: "1px solid color-mix(in oklab, var(--line) 65%, transparent)",
            background: "color-mix(in oklab, var(--bg-3) 55%, transparent)",
            padding: "14px 16px",
            overflowX: "auto",
          }}
        >
          <MathFormula className="qv-tex" displayMode label={`${title} formula`} math={tex} />
        </div>
        <p style={{ margin: 0, color: "var(--ink-3)", fontSize: 12.5, lineHeight: 1.55 }}>
          {note}
        </p>
      </div>
    </details>
  );
}

export default function AboutPageClient() {
  const reducedMotion = useReducedMotion();
  const animated = !reducedMotion;

  return (
    <div className="qv-m-pad" style={{ maxWidth: 1040, margin: "0 auto", padding: "0 28px 80px" }}>
      <Reveal>
        <header
          className="qv-m-stack"
          style={{
            minHeight: 510,
            padding: "46px 0 34px",
            display: "grid",
            gridTemplateColumns: "minmax(0, 1.35fr) minmax(320px, .75fr)",
            alignItems: "center",
            gap: 32,
          }}
        >
          <div>
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 9,
                color: "var(--ink-3)",
                fontSize: 10,
                letterSpacing: "0.18em",
                textTransform: "uppercase",
                fontWeight: 700,
              }}
            >
              <Image src="/brand/QuantivIcon.webp" alt="" width={18} height={18} />
              About Quantiv
            </div>
            <h1
              className="serif qv-m-h-hero"
              style={{
                margin: "20px 0 0",
                color: "var(--ink)",
                fontSize: 76,
                fontWeight: 800,
                lineHeight: 0.9,
                letterSpacing: "-0.045em",
                textTransform: "uppercase",
              }}
            >
              See what
              <br />
              <span
                style={{
                  background: "linear-gradient(135deg, var(--brand-blue-1), var(--accent-hi))",
                  WebkitBackgroundClip: "text",
                  WebkitTextFillColor: "transparent",
                  backgroundClip: "text",
                }}
              >
                options imply.
              </span>
            </h1>
            <p
              style={{
                margin: "22px 0 0",
                maxWidth: 610,
                color: "var(--ink-2)",
                fontSize: 17,
                lineHeight: 1.5,
              }}
            >
              Market pricing, realized earnings moves, and model ranges—shown together so the comparison is visible before it is explained.
            </p>
          </div>
          <div style={{ justifySelf: "end" }}>
            <HeroGlyph />
          </div>
        </header>
      </Reveal>

      <Reveal delay={40}>
        <section
          className="qv-m-2col"
          aria-label="Quantiv coverage statistics"
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
            padding: "20px 0",
            borderTop: "1px solid var(--line)",
            borderBottom: "1px solid var(--line)",
          }}
        >
          {ABOUT_STATS.map((stat, index) => (
            <div
              key={stat.kicker}
              style={{
                padding: "0 22px",
                borderLeft: index === 0 ? "none" : "1px solid var(--line)",
              }}
            >
              <div
                style={{
                  color: "var(--ink-4)",
                  fontSize: 9,
                  letterSpacing: "0.17em",
                  textTransform: "uppercase",
                }}
              >
                {stat.kicker}
              </div>
              <div
                style={{
                  marginTop: 7,
                  color: "var(--ink)",
                  fontSize: 36,
                  fontWeight: 800,
                  letterSpacing: "-0.03em",
                  lineHeight: 1,
                }}
              >
                <CountUp
                  value={stat.value}
                  from={stat.from}
                  suffix={stat.suffix}
                  decimals={stat.decimals}
                  delay={index * 90}
                />
              </div>
              <div style={{ marginTop: 5, color: "var(--ink-3)", fontSize: 10.5 }}>
                {stat.label}
              </div>
            </div>
          ))}
        </section>
      </Reveal>

      <Reveal delay={80}>
        <section style={{ padding: "52px 0 8px" }}>
          <div
            style={{
              color: "var(--ink-4)",
              fontSize: 9.5,
              fontWeight: 700,
              letterSpacing: "0.17em",
              textTransform: "uppercase",
            }}
          >
            One loop · three questions
          </div>
          <h2
            className="qv-type-section-title"
            style={{ margin: "10px 0 0", color: "var(--ink)" }}
          >
            See the research move.
          </h2>
        </section>
        <div
          className="qv-m-stack"
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
            gap: 14,
            marginTop: 18,
          }}
        >
          {ABOUT_STORIES.map((story) => (
            <StoryCard key={story.kind} {...story} animated={animated} />
          ))}
        </div>
      </Reveal>

      <Reveal delay={120}>
        <section style={{ padding: "58px 0 8px", marginTop: 36, borderTop: "1px solid var(--line)" }}>
          <div
            style={{
              color: "var(--ink-4)",
              fontSize: 9.5,
              fontWeight: 700,
              letterSpacing: "0.17em",
              textTransform: "uppercase",
            }}
          >
            Publication controls
          </div>
          <div
            className="qv-m-stack"
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "end",
              gap: 18,
              marginTop: 9,
            }}
          >
            <h2
              className="qv-type-section-title"
              style={{ margin: 0, color: "var(--ink)" }}
            >
              Validated before published.
            </h2>
            <span style={{ color: "var(--ink-3)", fontSize: 12 }}>
              End-of-day research · not an execution feed
            </span>
          </div>
        </section>
        <div style={{ marginTop: 18 }}>
          <PublicationFlow animated={animated} />
        </div>
      </Reveal>

      <Reveal delay={160}>
        <section
          id="models-and-math"
          style={{ padding: "56px 0 8px", marginTop: 40, borderTop: "1px solid var(--line)" }}
        >
          <div
            style={{
              color: "var(--ink-4)",
              fontSize: 9.5,
              fontWeight: 700,
              letterSpacing: "0.17em",
              textTransform: "uppercase",
            }}
          >
            Methodology
          </div>
          <div
            className="qv-m-stack"
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "end",
              gap: 18,
              marginTop: 9,
            }}
          >
            <h2
              className="qv-type-section-title"
              style={{ margin: 0, color: "var(--ink)" }}
            >
              Math when you want it.
            </h2>
            <span style={{ color: "var(--ink-3)", fontSize: 12 }}>
              Tap a row to collapse the formula and caveat.
            </span>
          </div>
        </section>
        <div style={{ display: "grid", gap: 8, marginTop: 18 }}>
          {METHODOLOGY_SECTIONS.map((section) => (
            <FormulaDisclosure key={section.id} {...section} />
          ))}
        </div>
      </Reveal>

      <Reveal delay={200}>
        <section
          style={{
            marginTop: 46,
            padding: "28px 30px",
            borderRadius: 18,
            border: "1px solid color-mix(in oklab, var(--brand-blue-1) 22%, var(--line))",
            background:
              "radial-gradient(100% 140% at 0% 0%, color-mix(in oklab, var(--brand-blue-1) 14%, transparent), transparent 58%), var(--bg-2)",
            display: "grid",
            gridTemplateColumns: "auto minmax(0, 1fr)",
            gap: 20,
            alignItems: "center",
          }}
        >
          <div
            aria-hidden="true"
            className="serif"
            style={{ color: "var(--brand-blue-1)", fontSize: 72, lineHeight: 0.7 }}
          >
            ≠
          </div>
          <div>
            <strong className="serif" style={{ color: "var(--ink)", fontSize: 20 }}>
              Research, not a recommendation.
            </strong>
            <div style={{ marginTop: 5, color: "var(--ink-3)", fontSize: 12.5, lineHeight: 1.5 }}>
              Quantiv shows priced movement and model evidence. Direction, position sizing, liquidity, fees, and risk tolerance remain yours.
            </div>
          </div>
        </section>
      </Reveal>

      <Reveal delay={220}>
        <footer
          style={{
            marginTop: 44,
            paddingTop: 24,
            borderTop: "1px solid var(--line)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 16,
            flexWrap: "wrap",
          }}
        >
          <div style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
            <Image src="/brand/QuantivIcon.webp" alt="" width={24} height={24} />
            <span className="serif" style={{ color: "var(--ink)", fontSize: 17, fontWeight: 700 }}>
              Quantiv
            </span>
          </div>
          <Link
            href="/"
            onClick={() => {
              if (typeof window !== "undefined") {
                setTimeout(() => window.scrollTo({ top: 0, left: 0 }), 0);
              }
            }}
            style={{
              padding: "11px 20px",
              borderRadius: 999,
              border: "1px solid var(--brand-blue-1)",
              color: "var(--brand-blue-1)",
              fontSize: 13,
              textDecoration: "none",
              fontWeight: 700,
            }}
          >
            Explore earnings →
          </Link>
        </footer>
      </Reveal>
    </div>
  );
}