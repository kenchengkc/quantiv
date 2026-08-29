import type { Metadata } from 'next';
import { Splash } from '@/components/Splash';
import EarningsGrid, { type WeeklyData } from '@/components/EarningsGrid';
// Bundled at build time from the daily refresh. Rendered into the initial HTML
// for the default "this week" view so the calendar (the LCP element) paints
// without waiting on the client bundle + a /weeks fetch. The client revalidates
// from the CDN on mount, so anything stale corrects within the first frame.
import weeklyData from '../../public/weekly.json';

export const metadata: Metadata = {
  title: {
    absolute: 'Earnings Calendar | Quantiv',
  },
};

// This page is a static artifact: daily refresh builds and deploys weekly.json,
// while non-default week/filter URL state is applied by the client after the
// first calendar paint. Keeping the initial route static avoids a serverless
// render for every landing and lets Vercel's edge cache serve the first byte.
export const dynamic = 'force-static';

export default function Home() {
  return (
    <>
      <Splash />
      <div className="qv-m-pad" style={{ maxWidth: 1240, margin: '0 auto', padding: '0 28px 60px' }}>
        <EarningsGrid initialData={weeklyData as WeeklyData} />
      </div>
    </>
  );
}
