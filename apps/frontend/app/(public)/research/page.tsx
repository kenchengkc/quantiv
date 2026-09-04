import type { Metadata } from 'next';
import { Suspense } from 'react';
import ResearchLabClient from './ResearchLabClient';

export const metadata: Metadata = {
  title: 'Research Lab',
  description:
    'Build point-in-time historical earnings cohorts and compare market-implied moves with realized reactions.',
};

function Fallback() {
  return (
    <div className="qv-m-pad" style={{ maxWidth: 1240, margin: '0 auto', padding: '36px 28px 72px' }}>
      <div className="mono" style={{ fontSize: 10, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--brand-blue-1)' }}>
        Point-in-time earnings research
      </div>
      <h1 className="serif qv-m-h1" style={{ margin: '14px 0 0', fontSize: 62, lineHeight: 0.95, letterSpacing: '-0.035em', textTransform: 'uppercase', color: 'var(--ink)', fontWeight: 800 }}>
        Research Lab
      </h1>
      <div style={{ marginTop: 28, height: 220, borderRadius: 14, border: '1px solid var(--line)', background: 'var(--bg-2)', opacity: 0.6 }} />
    </div>
  );
}

export default function ResearchPage() {
  return (
    <Suspense fallback={<Fallback />}>
      <ResearchLabClient />
    </Suspense>
  );
}
