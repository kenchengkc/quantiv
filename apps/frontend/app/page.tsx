import { Suspense } from 'react';
import type { Metadata } from 'next';
import EarningsGrid from '@/components/EarningsGrid';
import { EarningsGridFallback } from '@/components/EarningsGridSkeleton';

export const metadata: Metadata = {
  title: {
    absolute: 'Earnings Calendar | Quantiv',
  },
};

// The homepage uses a Suspense-wrapped client calendar. On Vercel's
// prerendered shell, Next 15 can emit an RSC metadata prelude before the
// doctype, which flashes as raw text before hydration. Keep this route
// dynamic so the first byte of the document is always real HTML.
export const dynamic = 'force-dynamic';
export const revalidate = 0;

export default function Home() {
  return (
    <div className="qv-m-pad" style={{ maxWidth: 1240, margin: '0 auto', padding: '0 28px 60px' }}>
      {/* Suspense is required: EarningsGrid uses useSearchParams(), which
          forces client-side bailout during prerender in Next 14. */}
      <Suspense fallback={<EarningsGridFallback />}>
        <EarningsGrid />
      </Suspense>
    </div>
  );
}
