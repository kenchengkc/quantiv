import { Suspense } from 'react';
import type { Metadata } from 'next';
import EarningsGrid from '@/components/EarningsGrid';

export const metadata: Metadata = {
  title: {
    absolute: 'Earnings Calendar | Quantiv',
  },
};

// Keep the homepage out of Vercel's static prerender artifact path. A prior
// deployment briefly exposed the root route's .meta headers as document text.
export const dynamic = 'force-dynamic';

export default function Home() {
  return (
    <div className="qv-m-pad" style={{ maxWidth: 1240, margin: '0 auto', padding: '0 28px 60px' }}>
      {/* Suspense is required: EarningsGrid uses useSearchParams(), which
          forces client-side bailout during prerender in Next 14. */}
      <Suspense fallback={null}>
        <EarningsGrid />
      </Suspense>
    </div>
  );
}
