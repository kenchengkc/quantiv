import { Suspense } from 'react';
import EarningsScreener from '@/components/EarningsScreener';

export default function ScreenerPage() {
  return (
    <div style={{ maxWidth: 1240, margin: '0 auto', padding: '0 28px 60px' }}>
      <Suspense fallback={<div style={{ color: 'var(--ink-3)', padding: '40px 0', fontSize: 13 }}>Loading…</div>}>
        <EarningsScreener />
      </Suspense>
    </div>
  );
}
