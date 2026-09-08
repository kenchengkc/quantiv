import type { Metadata } from 'next';
import { Splash } from '@/components/Splash';
import EarningsGrid, { type WeeklyData } from '@/components/EarningsGrid';
import { readPublicJson } from '@/lib/researchSnapshot.server';

export const metadata: Metadata = {
  title: {
    absolute: 'Earnings Calendar | Quantiv',
  },
};

// This page is a static artifact: the publication release is materialized before
// `next build`, then weekly.json is rendered into the initial HTML for the LCP
// calendar. Reading through the server publication boundary keeps TypeScript
// independent of generated production JSON while preserving the same static page.
export const dynamic = 'force-static';

function weeklyPublication(): WeeklyData {
  const publication = readPublicJson<WeeklyData>('weekly.json');
  if (!publication) {
    throw new Error('Frontend publication is missing weekly.json; materialization must complete before build.');
  }
  return publication;
}

export default function Home() {
  return (
    <>
      <Splash />
      <div className="qv-m-pad" style={{ maxWidth: 1240, margin: '0 auto', padding: '0 28px 60px' }}>
        <EarningsGrid initialData={weeklyPublication()} />
      </div>
    </>
  );
}
