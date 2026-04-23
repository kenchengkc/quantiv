import { Suspense } from 'react';
import EarningsGrid from '@/components/EarningsGrid';

export default function Home() {
  return (
    <div style={{ maxWidth: 1240, margin: '0 auto', padding: '0 28px 60px' }}>
      {/* Suspense is required: EarningsGrid uses useSearchParams(), which
          forces client-side bailout during prerender in Next 14. */}
      <Suspense fallback={null}>
        <EarningsGrid />
      </Suspense>
    </div>
  );
}
