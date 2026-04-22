export default function AboutPage() {
  return (
    <div style={{ maxWidth: 860, margin: '0 auto', padding: '0 28px 60px' }}>
      {/* Hero */}
      <section
        style={{
          padding: '48px 0 32px',
          borderBottom: '1px solid var(--line)',
          textAlign: 'center',
        }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/brand/QuantivColorBanner.png"
          alt="Quantiv"
          style={{ height: 72, width: 'auto', display: 'inline-block' }}
        />
        <div
          style={{
            marginTop: 18,
            fontSize: 11,
            letterSpacing: '0.22em',
            textTransform: 'uppercase',
            color: 'var(--ink-3)',
          }}
        >
          Options intelligence, distilled
        </div>
        <h1
          className="serif"
          style={{
            margin: '10px auto 0',
            maxWidth: 640,
            fontSize: 34,
            fontWeight: 800,
            letterSpacing: '-0.02em',
            lineHeight: 1.15,
          }}
        >
          Know what the market is pricing in — before the report drops.
        </h1>
      </section>

      {/* Mission */}
      <Section eyebrow="Mission">
        <p style={paraStyle}>
          Quantiv computes expected moves, implied-volatility context, and term structure from live
          option chains so retail traders and students can see what the market is already pricing
          into earnings — without stitching together four tabs and a spreadsheet.
        </p>
      </Section>

      {/* Features */}
      <Section eyebrow="What you get">
        <ul style={listStyle}>
          <li><strong>Expected move</strong> — straddle-based and IV-based, side by side.</li>
          <li><strong>Term structure</strong> — every expiry, ATM strike, IV, straddle, and EM in one table.</li>
          <li><strong>Earnings calendar</strong> — grouped by day and by timing (before open / after close).</li>
          <li><strong>Watchlist</strong> — drag to reorder, click to open, one-tap add from any ticker page.</li>
        </ul>
      </Section>

      {/* Methodology */}
      <Section eyebrow="Methodology">
        <p style={paraStyle}>
          Greeks and implied vol come from Black–Scholes with dividend adjustment; IV is solved
          with Brent&apos;s method to a <span className="mono">1e-6</span> tolerance.
        </p>
        <div style={formulaStyle}>
          <div className="mono tnum" style={{ fontSize: 13 }}>
            EM<sub>straddle</sub> ≈ mid(call<sub>ATM</sub> + put<sub>ATM</sub>)
          </div>
          <div className="mono tnum" style={{ fontSize: 13, marginTop: 6 }}>
            EM<sub>iv</sub> ≈ S₀ · σ<sub>ATM</sub> · √T
          </div>
        </div>
      </Section>

      {/* Stack */}
      <Section eyebrow="Stack">
        <p style={paraStyle}>
          Next.js App Router, TypeScript, Vercel Fluid Compute, Upstash Redis for price caching,
          and Polygon for live quotes. Data pipeline runs on DuckDB + parquet.
        </p>
      </Section>

      {/* Disclaimer */}
      <div
        style={{
          marginTop: 28,
          padding: 18,
          border: '1px solid var(--line)',
          borderRadius: 10,
          fontSize: 12.5,
          color: 'var(--ink-3)',
          lineHeight: 1.5,
        }}
      >
        <strong style={{ color: 'var(--ink-2)' }}>Disclaimer.</strong> Quantiv is for educational
        and informational use only. Options trading carries substantial risk. Past performance
        does not guarantee future results. Nothing here is investment advice.
      </div>
    </div>
  );
}

function Section({ eyebrow, children }: { eyebrow: string; children: React.ReactNode }) {
  return (
    <section style={{ padding: '28px 0', borderBottom: '1px solid var(--line)' }}>
      <div
        style={{
          fontSize: 10,
          letterSpacing: '0.18em',
          textTransform: 'uppercase',
          color: 'var(--ink-3)',
          marginBottom: 10,
        }}
      >
        {eyebrow}
      </div>
      {children}
    </section>
  );
}

const paraStyle: React.CSSProperties = {
  fontSize: 15,
  color: 'var(--ink-2)',
  lineHeight: 1.65,
  margin: 0,
};

const listStyle: React.CSSProperties = {
  listStyle: 'none',
  padding: 0,
  margin: 0,
  display: 'grid',
  gap: 10,
  fontSize: 14.5,
  color: 'var(--ink-2)',
  lineHeight: 1.55,
};

const formulaStyle: React.CSSProperties = {
  marginTop: 14,
  padding: '14px 16px',
  background: 'var(--bg-2)',
  border: '1px solid var(--line)',
  borderRadius: 8,
};
