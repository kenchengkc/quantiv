import {
  optionsSnapshotFreshness, publishedForecastStatus, researchUpdatePresentation,
  type PublicationControl, type PublishedForecast,
} from '@/lib/publicationPresentation';
import styles from './ValidationPublication.module.css';

export function validationDateLabel(value: string | null | undefined): string {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Unavailable';
  return new Date(value).toLocaleString('en-US', {
    month: 'short', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
    hour12: false, timeZone: 'America/New_York',
  });
}

export function ValidationPublication({ control, forecast }: {
  control: PublicationControl; forecast: PublishedForecast;
}) {
  const published = publishedForecastStatus(forecast);
  const updates = researchUpdatePresentation(control, forecast);
  const snapshotFreshness = optionsSnapshotFreshness(control.data);
  const publishedTone = published === 'passed' ? 'up' : published === 'failed' ? 'down'
    : published === 'degraded' ? 'flag' : 'ink-3';
  return (
    <section aria-label="Research publication and freshness" className={styles.summary}>
      <div>
        <h3>Published forecast checks</h3>
        <p className={styles.state} style={{ color: `var(--${publishedTone})` }}>
          {published === 'passed' ? 'Passed' : published === 'failed' ? 'Failed'
            : published === 'degraded' ? 'Review required' : 'Unavailable'}
        </p>
        <p>Last forecast validation: {validationDateLabel(forecast.validated_at)} ET</p>
      </div>
      <div>
        <h3>New research updates</h3>
        <p className={styles.state} style={{ color: `var(--${updates.tone})` }}>{updates.label}</p>
        <p>{updates.detail}</p>
        <p>Assessment captured: {validationDateLabel(control.generated_at)} ET</p>
        <p>Options evidence assessed: {snapshotFreshness ?? 'Unavailable'}</p>
      </div>
    </section>
  );
}
