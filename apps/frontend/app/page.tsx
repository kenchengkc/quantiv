import { Suspense } from 'react';
import type { Metadata } from 'next';
import EarningsGrid from '@/components/EarningsGrid';
import { EarningsGridFallback } from '@/components/EarningsGridSkeleton';

export const metadata: Metadata = {
  title: {
    absolute: 'Earnings Calendar | Quantiv',
  },
};

// Keep the homepage out of Vercel's static prerender artifact path. A prior
// deployment briefly exposed the root route's .meta headers as document text.
export const dynamic = 'force-dynamic';

type HomeSearchParams = Promise<{
  offset?: string | string[];
}>;

function parseInitialOffset(raw: string | string[] | undefined) {
  const value = Array.isArray(raw) ? raw[0] : raw;
  const parsed = Number(value ?? 0);
  if (!Number.isFinite(parsed)) return 0;
  return Math.min(2, Math.max(-1, Math.trunc(parsed)));
}

export default async function Home({ searchParams }: { searchParams?: HomeSearchParams }) {
  const params = searchParams ? await searchParams : undefined;
  const initialOffset = parseInitialOffset(params?.offset);

  return (
    <div className="qv-m-pad" style={{ maxWidth: 1240, margin: '0 auto', padding: '0 28px 60px' }}>
      {/* Suspense is required: EarningsGrid uses useSearchParams(), which
          forces client-side bailout during prerender in Next 14. */}
      <Suspense fallback={<EarningsGridFallback offset={initialOffset} />}>
        <EarningsGrid />
      </Suspense>
    </div>
  );
}
