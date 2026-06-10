import type { Metadata } from 'next';
import EarningsGrid from '@/components/EarningsGrid';
import { parseHomeSearchParams } from '@/lib/homeSearchParams';

export const metadata: Metadata = {
  title: {
    absolute: 'Earnings Calendar | Quantiv',
  },
};

// The homepage passes searchParams from the server so EarningsGrid does not
// need useSearchParams() (which forced a Suspense fallback and a skeleton
// flash on every back navigation).
export const dynamic = 'force-dynamic';
export const revalidate = 0;

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ offset?: string; filter?: string }>;
}) {
  const sp = await searchParams;
  const { initialOffset, initialFilter } = parseHomeSearchParams(sp);

  return (
    <div className="qv-m-pad" style={{ maxWidth: 1240, margin: '0 auto', padding: '0 28px 60px' }}>
      <EarningsGrid initialOffset={initialOffset} initialFilter={initialFilter} />
    </div>
  );
}
