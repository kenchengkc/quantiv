import type { Page } from '@playwright/test';
import type { CalendarReferenceEvent } from '../../lib/calendarReference';

/** Keep the authoritative calendar and retained research on the same test release. */
export async function installCalendarReference(
  page: Page,
  week: { window: { start: string; end: string }; events: CalendarReferenceEvent[] },
) {
  await page.route('**/calendar-reference.json', (route) => route.fulfill({
    json: {
      schema: 'quantiv.calendar-reference.v1',
      release_id: 'e2e-calendar-reference',
      window: week.window,
      // Reference publication contains identity only; metrics must still come
      // from the separately mocked research week through the real merge path.
      events: week.events.map(({ ticker, earnings_date, timing }) => ({
        ticker, earnings_date, timing,
      })),
    },
  }));
}
