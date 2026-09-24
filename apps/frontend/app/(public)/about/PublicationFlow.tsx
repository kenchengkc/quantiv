"use client";

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import styles from './PublicationFlow.module.css';

const CHECKPOINTS = [
  { name: 'Observe', caption: 'Record the snapshot', title: 'Point-in-time inputs', description: 'Prices, option quotes and the earnings event belong to a specific snapshot. The forecast can use only information available before its prediction deadline.', rule: 'No future information enters the forecast.', evidence: 'Snapshot manifest', checks: ['Market session recorded', 'Earnings date and session identified', 'Feature cutoff precedes the announcement'] },
  { name: 'Reconcile', caption: 'Validate source inputs', title: 'Option quote validation', description: 'Eligible option quotes need usable bid/ask prices, matching contracts and consistent timestamps. Required evidence that contradicts the snapshot blocks this candidate release.', rule: 'A failed critical check stops publication.', evidence: 'Quote reconciliation', checks: ['Positive, uncrossed bid / ask', 'Call and put share strike and expiry', 'Quote evidence matches the snapshot session'] },
  { name: 'Verify', caption: 'Reconcile forecast values', title: 'Forecast consistency', description: 'Recompute option-derived estimates, validate model inputs and forecast ranges, and verify consistency across the site.', rule: 'Every published value must reconcile.', evidence: 'Forecast consistency', checks: ['Expected-move math reconciles', 'Forecast bands are ordered and finite', 'Calendar and ticker forecasts agree'] },
  { name: 'Publish', caption: 'Publish verified data', title: 'Consistent publication', description: 'Validated files are packaged together and verified before the site switches to the new snapshot. A failed candidate leaves the last validated release in place.', rule: 'The new release is available only after verification.', evidence: 'Published research', checks: ['Release contents verified', 'Calendar and ticker share a snapshot', 'Validation evidence accompanies the release'] },
] as const;

type Scenario = 'valid' | 'stale';

function Mark({ blocked = false }: { blocked?: boolean }) {
  return <svg viewBox="0 0 20 20" width="18" height="18" fill="none" aria-hidden="true">
    {blocked ? <path d="m6 6 8 8M14 6l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /> : <path d="m4 10 4 4 8-8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />}
  </svg>;
}

export default function PublicationFlow({ animated }: { animated: boolean }) {
  const [step, setStep] = useState(0);
  const [scenario, setScenario] = useState<Scenario>('valid');
  const [playing, setPlaying] = useState(true);
  const [restart, setRestart] = useState(0);
  const [visible, setVisible] = useState(false);
  const [pageVisible, setPageVisible] = useState(true);
  const root = useRef<HTMLElement>(null);
  const blocked = scenario === 'stale' && step === 1;
  const finished = step === 3 || blocked;
  const running = animated && playing && visible && pageVisible && !finished;
  const current = CHECKPOINTS[step];

  useEffect(() => {
    const element = root.current;
    if (!element) return;
    if (typeof IntersectionObserver === 'undefined') { setVisible(true); return; }
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { threshold: 0.2 });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const sync = () => setPageVisible(document.visibilityState !== 'hidden');
    sync();
    document.addEventListener('visibilitychange', sync);
    return () => document.removeEventListener('visibilitychange', sync);
  }, []);

  useEffect(() => {
    if (!running) return;
    const timer = window.setTimeout(() => setStep((previous) => Math.min(3, previous + 1)), 4500);
    return () => window.clearTimeout(timer);
  }, [running, step, restart]);

  function reset(next: Scenario = scenario) {
    setRestart((previous) => previous + 1);
    setScenario(next);
    setStep(0);
    setPlaying(true);
  }

  const rows = step === 0 ? [
    ['Earnings event', 'Wednesday · before open', 'Session identified'],
    ['Market snapshot', 'Tuesday · 16:00 ET', 'Timestamp retained'],
    ['Prediction cutoff', 'Before the announcement', 'No look-ahead'],
  ] : step === 1 ? [
    ['Call / put contracts', 'Same strike + expiry', 'Matched'],
    ['Bid / ask', '$2.10 / $2.30', 'Uncrossed'],
    ['Quote session', blocked ? 'Monday ≠ Tuesday' : 'Tuesday = Tuesday', blocked ? 'Mismatch' : 'Matched'],
  ] : step === 2 ? [
    ['Option midpoint', '($2.10 + $2.30) ÷ 2 = $2.20', 'Reconciled'],
    ['Model range', 'P10 2.1% < P50 4.8% < P90 8.3%', 'Ordered'],
    ['Headline forecast', 'Calendar 4.8% = ticker 4.8%', 'Consistent'],
  ] : [
    ['Snapshot', 'One verified release', 'Published'],
    ['Evidence', 'Inputs + checks + release identity', 'Traceable'],
    ['Site update', 'Calendar, screener and ticker pages', 'In sync'],
  ];

  return <section ref={root} className={styles.flow} aria-label="Publication controls walkthrough" data-running={running} data-blocked={blocked}>
    <div className={styles.toolbar}>
      <div><span className={styles.eyebrow}>Publication workflow</span><p className={styles.disclaimer}>Illustrative example · not live status</p></div>
      <div className={styles.controls}>
        <div className={styles.scenarios} role="group" aria-label="Example scenario">
          <button type="button" aria-pressed={scenario === 'valid'} onClick={() => reset('valid')}>Valid snapshot</button>
          <button type="button" aria-pressed={scenario === 'stale'} onClick={() => reset('stale')}>Stale quote</button>
        </div>
        {animated && <button type="button" className={styles.playback} aria-label={finished ? 'Replay walkthrough' : playing ? 'Pause walkthrough' : 'Play walkthrough'} onClick={() => finished ? reset() : setPlaying(!playing)}>
          <span aria-hidden="true">{finished ? '↻' : playing ? 'Ⅱ' : '▷'}</span><span>{finished ? 'Replay' : playing ? 'Pause' : 'Play'}</span>
        </button>}
      </div>
    </div>

    <div className={styles.track} role="group" aria-label="Inspect a checkpoint">
      {CHECKPOINTS.map((checkpoint, index) => <button key={checkpoint.name} type="button"
        className={styles.checkpoint} aria-label={`0${index + 1} ${checkpoint.name}`} aria-pressed={step === index}
        disabled={scenario === 'stale' && index > 1} data-state={index === step ? (blocked ? 'blocked' : 'active') : index < step ? 'passed' : 'waiting'}
        onClick={() => { setStep(index); setPlaying(false); }}>
        <span className={styles.node}>{index < step ? <Mark /> : `0${index + 1}`}</span>
        <span className={styles.checkpointCopy}><strong>{checkpoint.name}</strong><span>{checkpoint.caption}</span></span>
        <span className={styles.connector} aria-hidden="true"><i /></span>
      </button>)}
    </div>

    <div className={styles.scene} key={`${scenario}-${step}`}>
      <div className={styles.explainer}>
        <span className={styles.stepLabel}>Checkpoint 0{step + 1} / 04</span>
        <h3>{blocked ? 'Quote validation failed' : current.title}</h3>
        <p>{blocked ? 'The candidate snapshot specifies Tuesday, but its quote evidence is from Monday. The session check fails, preventing the candidate from replacing the published release.' : current.description}</p>
        <div className={styles.rule}><span aria-hidden="true">↳</span>{current.rule}</div>
      </div>
      <div className={styles.lab}>
        <div className={styles.labHeader}><span>{current.evidence}</span><span className={styles.status}><i />{blocked ? 'Held' : step === 3 ? 'Released' : 'Candidate'}</span></div>
        <div className={styles.evidence}>
          {rows.map(([label, value, status], index) => <div className={styles.evidenceRow} key={label} data-failed={blocked && index === 2} style={{ animationDelay: `${index * 140}ms` }}>
            <span className={styles.rowLabel}>{label}</span><strong>{value}</strong><span className={styles.rowStatus}>{status}<Mark blocked={blocked && index === 2} /></span>
          </div>)}
        </div>
        <div className={styles.destination}>
          <div className={styles.transfer} aria-hidden="true"><span /><span /><span /><i>{blocked ? '×' : '↓'}</i></div>
          {step === 3 ? <div className={styles.surfaces}>
            <div><span>Calendar</span><strong>±4.8<span>%</span></strong><small>Model forecast</small></div>
            <div><span>Ticker page</span><strong>±4.8<span>%</span></strong><small>Same event · same forecast</small></div>
            <span className={styles.releaseSeal}><Mark /> New snapshot published</span>
          </div> : <div className={styles.gateResult}>
            <span className={styles.resultIcon} aria-hidden="true">{blocked ? '×' : '✓'}</span>
            <div><strong>{blocked ? 'Publication blocked' : step === 0 ? 'Evidence locked to this event' : step === 1 ? 'Inputs reconciled' : 'Ready for release verification'}</strong>
            <span>{blocked ? 'Last validated release stays available' : step === 0 ? 'Event, session, and prediction cutoff are recorded together' : step === 1 ? 'Required quote checks passed in this example' : 'A consistent forecast across every research surface'}</span></div>
          </div>}
        </div>
      </div>
    </div>

    <div className={styles.checklist} aria-label="Checks at this checkpoint">
      {current.checks.map((check, index) => <span key={check} data-failed={blocked && index === 2}><Mark blocked={blocked && index === 2} />{check}</span>)}
    </div>
    <footer className={styles.footer}>
      <p><strong>Fail closed.</strong> Passing controls establishes data integrity; forecasts remain uncertain.</p>
      <Link href="/validation">View actual validation evidence <span aria-hidden="true">↗</span></Link>
    </footer>
    <span className={styles.srOnly} role="status">{!playing || finished ? (blocked ? 'Publication blocked. Last validated release stays available.' : `${current.name} checkpoint selected.`) : ''}</span>
  </section>;
}
