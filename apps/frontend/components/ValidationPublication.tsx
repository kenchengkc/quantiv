import {
  optionsSnapshotFreshness,
  publishedForecastStatus,
  researchUpdatePresentation,
  type PublicationControl,
  type PublishedForecast,
} from '@/lib/publicationPresentation';
import styles from './ValidationPublication.module.css';

export function validationDateLabel(
  value: string | null | undefined,
): string {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Unavailable';
  return new Date(value).toLocaleString('en-US', {
    month: 'short',
    day: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'America/New_York',
  });
}

function stateLabel(value: ReturnType<typeof publishedForecastStatus>) {
  if (value === 'passed') return 'Passed';
  if (value === 'failed') return 'Failed';
  if (value === 'advisory') return 'Advisory';
  return 'Unavailable';
}

export function ValidationPublication({
  control,
  forecast,
}: {
  control: PublicationControl;
  forecast: PublishedForecast;
}) {
  const published = publishedForecastStatus(forecast);
  const updates = researchUpdatePresentation(control, forecast);
  const snapshotFreshness = optionsSnapshotFreshness(control.data);
  const publishedTone =
    published === 'passed'
      ? 'up'
      : published === 'failed'
        ? 'down'
        : published === 'advisory'
          ? 'flag'
          : 'ink-3';

  return (
    <section
      aria-label="Research publication and freshness"
      className={styles.summary}
    >
      <article className={styles.card}>
        <div className={styles.cardTop}>
          <span>Last validated release</span>
          <span
            className={styles.dot}
            style={{ background: `var(--${publishedTone})` }}
            aria-hidden
          />
        </div>
        <p
          className={styles.state}
          style={{ color: `var(--${publishedTone})` }}
        >
          {stateLabel(published)}
        </p>
        <p className={styles.explainer}>
          This is the status of the forecast release already retained for
          research.
        </p>
        <p className={styles.timestamp}>
          Last forecast validation: {validationDateLabel(forecast.validated_at)} ET
        </p>
      </article>

      <article className={styles.card}>
        <div className={styles.cardTop}>
          <span>Next research release</span>
          <span
            className={styles.dot}
            style={{ background: `var(--${updates.tone})` }}
            aria-hidden
          />
        </div>
        <p
          className={styles.state}
          style={{ color: `var(--${updates.tone})` }}
        >
          {updates.label}
        </p>
        <p className={styles.explainer}>{updates.detail}</p>
        <div className={styles.meta}>
          <p>
            Assessment captured: {validationDateLabel(control.generated_at)} ET
          </p>
          <p>
            Options evidence assessed: {snapshotFreshness ?? 'Unavailable'}
          </p>
        </div>
      </article>
    </section>
  );
}
