import Link from 'next/link';

export function Footer() {
  return (
    <footer
      style={{
        borderTop: '1px solid var(--line)',
        marginTop: 40,
        padding: '28px 28px',
        background: 'var(--bg)',
      }}
    >
      <div
        style={{
          maxWidth: 1240,
          margin: '0 auto',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 24,
          flexWrap: 'wrap',
        }}
      >
        <Link href="/" aria-label="Quantiv home" style={{ display: 'inline-flex' }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/brand/QuantivBWBanner.png"
            alt="Quantiv"
            style={{ height: 22, width: 'auto', display: 'block', opacity: 0.8 }}
          />
        </Link>
        <div
          style={{
            display: 'flex',
            gap: 22,
            fontSize: 11.5,
            color: 'var(--ink-4)',
            letterSpacing: '0.04em',
          }}
        >
          <Link href="/about" style={{ color: 'inherit' }}>
            About
          </Link>
          <span>© {new Date().getFullYear()} Quantiv</span>
          <span>Educational use only · Not investment advice</span>
        </div>
      </div>
    </footer>
  );
}
