import type { SymbolDetail } from './symbolPageTypes';

type Props = {
  hasModel: boolean;
  hasOptions: boolean;
  historyCount: number;
  history: SymbolDetail['earnings_history'];
  today: string;
  note?: { title: string; detail: string; source_url: string };
};

export default function ResearchAvailability({ hasModel, hasOptions, historyCount, history, today, note }: Props) {
  const past = (history ?? []).filter((row) => row.date <= today);
  const hasEps = past.some((row) => row.eps_actual != null && Number.isFinite(row.eps_actual));
  const messages = [
    ...(!hasModel ? [{ title: 'ML forecast unavailable', detail: 'No eligible model forecast is available for this earnings event.' }] : []),
    ...(!hasOptions ? [{ title: 'Options and Greeks unavailable', detail: 'This snapshot has no option pairs that pass quote-quality checks. Expiration analysis and contract Greeks are unavailable.' }] : []),
    ...(historyCount < 2 ? [{ title: 'Price-reaction history unavailable', detail: 'At least two earnings events with recorded closing prices are needed for the historical chart.' }] : []),
    ...(!hasEps ? [{ title: 'EPS actuals unavailable', detail: 'The retained earnings data has no reported EPS actuals. Estimates are not reported results.' }] : []),
  ];
  const standaloneEps = historyCount < 2 && hasEps;
  if (!messages.length && !note && !standaloneEps) return null;
  return (
    <section className="qv-card" aria-label="Research availability" style={{ marginTop: 18, padding: '20px 22px' }}>
      {note && <div style={{ marginBottom: 18 }}>
        <h2 style={{ fontSize: 15, margin: '0 0 6px' }}>{note.title}</h2>
        <p style={{ color: 'var(--ink-3)', fontSize: 13, lineHeight: 1.6, margin: 0 }}>{note.detail} <a href={note.source_url} target="_blank" rel="noreferrer">Company update</a></p>
      </div>}
      {messages.length > 0 && <>
        <h2 style={{ fontSize: 15, margin: '0 0 12px' }}>Research availability</h2>
        <div style={{ display: 'grid', gap: 12 }}>
          {messages.map(({ title, detail }) => <div key={title}>
            <h3 style={{ fontSize: 13, margin: '0 0 3px' }}>{title}</h3>
            <p style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--ink-3)', margin: 0 }}>{detail}</p>
          </div>)}
        </div>
      </>}
      {standaloneEps && <div style={{ marginTop: 18, overflowX: 'auto' }}>
        <h2 style={{ fontSize: 15 }}>Historical EPS</h2>
        <table style={{ width: '100%', textAlign: 'left', fontSize: 13 }}>
          <thead><tr><th>Reported</th><th>Actual EPS</th><th>Estimated EPS</th></tr></thead>
          <tbody>{past.slice().sort((a, b) => b.date.localeCompare(a.date)).slice(0, 12).map((row) => <tr key={row.date}>
            <td>{row.date}</td><td>{row.eps_actual?.toFixed(2) ?? '—'}</td><td>{row.eps_estimate?.toFixed(2) ?? '—'}</td>
          </tr>)}</tbody>
        </table>
      </div>}
    </section>
  );
}
