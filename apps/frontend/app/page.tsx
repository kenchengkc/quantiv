import type { Metadata } from 'next';
import EarningsGrid, { type WeeklyData } from '@/components/EarningsGrid';
import { parseHomeSearchParams } from '@/lib/homeSearchParams';
// Bundled at build time from the daily refresh. Rendered into the initial HTML
// for the default "this week" view so the calendar (the LCP element) paints
// without waiting on the client bundle + a /weeks fetch. The client revalidates
// from the CDN on mount, so anything stale corrects within the first frame.
import weeklyData from '../public/weekly.json';

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

  // weekly.json only describes "this week" (offset 0); other offsets keep the
  // existing client-fetch path.
  const initialData = initialOffset === 0 ? (weeklyData as WeeklyData) : null;

  return (
    <div className="qv-m-pad" style={{ maxWidth: 1240, margin: '0 auto', padding: '0 28px 60px' }}>
      <EarningsGrid
        initialOffset={initialOffset}
        initialFilter={initialFilter}
        initialData={initialData}
      />
    </div>
  );
}
