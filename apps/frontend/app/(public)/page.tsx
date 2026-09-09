import type { Metadata } from 'next';
import { Splash } from '@/components/Splash';
import EarningsGrid, { type WeeklyData } from '@/components/EarningsGrid';
import {
  mergeCalendarReference,
  type CalendarReference,
} from '@/lib/calendarReference';
import { readPublicJson } from '@/lib/researchSnapshot.server';

export const metadata: Metadata = {
  title: {
    absolute: 'Earnings Calendar | Quantiv',
  },
};

// This page is a static artifact: the publication release is materialized before
// `next build`, then weekly research plus the independently dated calendar
// reference are rendered into the initial HTML. Calendar reference is optional
// during the transition; once materialized it is authoritative for event identity.
export const dynamic = 'force-static';

function weeklyPublication(): WeeklyData {
  const research = readPublicJson<WeeklyData>('weekly.json');
  if (!research) {
    throw new Error('Frontend publication is missing weekly.json; materialization must complete before build.');
  }
  const calendar = readPublicJson<CalendarReference>('calendar-reference.json');
  return mergeCalendarReference(calendar, research, research.window.start);
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
