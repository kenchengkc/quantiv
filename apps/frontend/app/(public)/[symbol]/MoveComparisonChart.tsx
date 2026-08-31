import type { PredictionMode, LivePredictionStatus } from './symbolPageTypes';
import { estimateQuantileExceedance } from '@/lib/forecastQuantiles';

type Quantiles = {
  p10: number;
  p25: number;
  p50: number;
  p75: number;
  p90: number;
};

type MoveRowProps = {
  label: string;
  value: number;
  detail: string;
  spot: number;
  maxMove: number;
  tone: string;
  quantiles?: Quantiles | null;
};

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function priceRange(spot: number, move: number): string {
  return `$${(spot * (1 - move)).toFixed(0)}–$${(spot * (1 + move)).toFixed(0)}`;
}

function position(value: number, maxMove: number): string {
  return `${Math.max(0, Math.min(100, (value / maxMove) * 100))}%`;
}

function comparisonText(
  optionsMove: number | null,
  modelMove: number | null,
  historyMove: number | null,
): string | null {
  const comparisonMove = modelMove ?? historyMove;
  if (optionsMove == null || comparisonMove == null) return null;
  const gap = Math.abs(optionsMove - comparisonMove) * 100;
  const comparisonName = modelMove == null ? 'historical median' : 'model';
  if (gap < 0.2) {
    return `Options and the ${comparisonName} are closely aligned.`;
  }
  return `Options price ${gap.toFixed(1)} percentage points ${
    optionsMove > comparisonMove ? 'more' : 'less'
  } movement than the ${comparisonName}.`;
}

function exceedanceLabel(
  estimate: ReturnType<typeof estimateQuantileExceedance>,
): string | null {
  if (!estimate) return null;
  const probability = Math.round(estimate.probability * 100);
  if (estimate.qualifier === 'at_least') return `≥${probability}%`;
  if (estimate.qualifier === 'at_most') return `≤${probability}%`;
  return `≈${probability}%`;
}

function MoveRow({
  label,
  value,
  detail,
  spot,
  maxMove,
  tone,
  quantiles,
}: MoveRowProps) {
  const hasQuantiles = quantiles != null;
  const ariaLabel = hasQuantiles
    ? `${label}: point forecast ${percent(value)}; median ${percent(quantiles.p50)}; 80 percent range ${percent(quantiles.p10)} to ${percent(quantiles.p90)}`
    : `${label}: ${percent(value)}; price range ${priceRange(spot, value)}`;

  return (
    <div className="qv-move-comparison-row" aria-label={ariaLabel}>
      <div style={{ minWidth: 0 }}>
        <div
          style={{
            color: 'var(--ink-4)',
            fontSize: 9,
            letterSpacing: '0.11em',
            textTransform: 'uppercase',
          }}
        >
          {label}
        </div>
        <div
          className="mono tnum"
          style={{
            marginTop: 3,
            color: tone,
            fontSize: 16,
            fontWeight: 700,
          }}
        >
          ±{percent(value)}
        </div>
        <div
          className="mono tnum"
          style={{ marginTop: 2, color: 'var(--ink-4)', fontSize: 10 }}
        >
          {priceRange(spot, value)}
        </div>
        <div
          style={{
            marginTop: 3,
            color: 'var(--ink-4)',
            fontSize: 9.5,
            lineHeight: 1.3,
          }}
        >
          {detail}
        </div>
      </div>

      <div
        role="img"
        aria-label={ariaLabel}
        style={{
          position: 'relative',
          height: 48,
          borderRadius: 8,
          background:
            'linear-gradient(90deg, color-mix(in oklab, var(--line) 46%, transparent) 1px, transparent 1px)',
          backgroundSize: '25% 100%',
          borderLeft: '1px solid var(--line-2)',
        }}
      >
        <div
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            top: '50%',
            height: 1,
            background: 'var(--line)',
          }}
        />

        {hasQuantiles ? (
          <>
            <div
              aria-hidden="true"
              style={{
                position: 'absolute',
                left: position(quantiles.p10, maxMove),
                width: `calc(${position(quantiles.p90, maxMove)} - ${position(quantiles.p10, maxMove)})`,
                top: 14,
                height: 20,
                borderRadius: 6,
                background:
                  'color-mix(in oklab, var(--brand-blue-1) 18%, transparent)',
              }}
            />
            <div
              aria-hidden="true"
              style={{
                position: 'absolute',
                left: position(quantiles.p25, maxMove),
                width: `calc(${position(quantiles.p75, maxMove)} - ${position(quantiles.p25, maxMove)})`,
                top: 14,
                height: 20,
                borderRadius: 6,
                background:
                  'color-mix(in oklab, var(--brand-blue-1) 38%, transparent)',
              }}
            />
            <div
              aria-hidden="true"
              style={{
                position: 'absolute',
                left: position(quantiles.p50, maxMove),
                top: 9,
                bottom: 9,
                width: 2,
                transform: 'translateX(-1px)',
                background: 'var(--brand-blue-1)',
              }}
            />
            {Math.abs(value - quantiles.p50) >= 0.001 ? (
              <div
                aria-hidden="true"
                style={{
                  position: 'absolute',
                  left: position(value, maxMove),
                  top: 19,
                  width: 10,
                  height: 10,
                  borderRadius: '50%',
                  transform: 'translateX(-5px)',
                  border: '2px solid var(--bg)',
                  background: tone,
                  boxShadow: `0 0 0 1px ${tone}`,
                }}
              />
            ) : null}
          </>
        ) : (
          <>
            <div
              aria-hidden="true"
              style={{
                position: 'absolute',
                left: 0,
                width: position(value, maxMove),
                top: 20,
                height: 8,
                borderRadius: '0 999px 999px 0',
                background: `color-mix(in oklab, ${tone} 64%, transparent)`,
              }}
            />
            <div
              aria-hidden="true"
              style={{
                position: 'absolute',
                left: position(value, maxMove),
                top: 16,
                width: 2,
                height: 16,
                transform: 'translateX(-1px)',
                background: tone,
              }}
            />
          </>
        )}
      </div>
    </div>
  );
}

export default function MoveComparisonChart({
  spot,
  optionsMovePct,
  modelMovePct,
  modelQuantiles,
  modelIsSpotUpdated,
  historicalMovePct,
  historyCount,
  ivRank,
  mode,
  onModeChange,
  spotUpdateDisabled,
  spotUpdateStatus,
  modelMeta,
  unavailableReason,
}: {
  spot: number;
  optionsMovePct: number | null;
  modelMovePct: number | null;
  modelQuantiles: Quantiles | null;
  modelIsSpotUpdated: boolean;
  historicalMovePct: number | null;
  historyCount: number;
  ivRank: number | null;
  mode: PredictionMode;
  onModeChange: (mode: PredictionMode) => void;
  spotUpdateDisabled: boolean;
  spotUpdateStatus: LivePredictionStatus;
  modelMeta: string;
  unavailableReason: string | null;
}) {
  const values = [
    optionsMovePct,
    modelMovePct,
    historicalMovePct,
    modelQuantiles?.p90,
  ].filter((value): value is number => value != null && Number.isFinite(value));
  if (values.length < 2 || spot <= 0) return null;

  const maxMove = Math.max(...values, 0.01) * 1.12;
  const comparison = comparisonText(
    optionsMovePct,
    modelMovePct,
    historicalMovePct,
  );
  const exceedance =
    optionsMovePct != null && modelQuantiles
      ? estimateQuantileExceedance(modelQuantiles, optionsMovePct)
      : null;
  const exceedanceValue = exceedanceLabel(exceedance);
  const ticks = [0, 0.25, 0.5, 0.75, 1];

  return (
    <section
      className="qv-card"
      aria-labelledby="move-comparison-title"
      style={{ marginTop: 22 }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          gap: 16,
          flexWrap: 'wrap',
        }}
      >
        <div style={{ minWidth: 0 }}>
          <span className="qv-pill">Earnings move</span>
          <h3
            id="move-comparison-title"
            className="serif"
            style={{
              margin: '10px 0 0',
              fontSize: 21,
              fontWeight: 700,
              letterSpacing: '-0.01em',
            }}
          >
            Market vs model vs history
          </h3>
          <div
            style={{
              marginTop: 4,
              color: 'var(--ink-3)',
              fontSize: 13,
              lineHeight: 1.45,
            }}
          >
            Absolute move magnitude around ${spot.toFixed(2)} spot. Price
            ranges are symmetric references, not directional forecasts.
          </div>
        </div>

        {modelQuantiles ? (
          <div
            role="tablist"
            aria-label="Forecast input mode"
            style={{
              display: 'inline-grid',
              gridTemplateColumns: '1fr 1fr',
              minWidth: 190,
              padding: 3,
              borderRadius: 8,
              border: '1px solid var(--line)',
              background: 'var(--bg-2)',
            }}
          >
            {(['snapshot', 'live'] as PredictionMode[]).map((item) => {
              const active = mode === item;
              const disabled = item === 'live' && spotUpdateDisabled;
              return (
                <button
                  key={item}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  disabled={disabled}
                  onClick={() => onModeChange(item)}
                  title={
                    disabled
                      ? 'No model horizon is available for this snapshot'
                      : undefined
                  }
                  style={{
                    minHeight: 28,
                    border: 'none',
                    borderRadius: 6,
                    background: active ? 'var(--bg-3)' : 'transparent',
                    color: disabled
                      ? 'var(--ink-4)'
                      : active
                        ? 'var(--ink)'
                        : 'var(--ink-3)',
                    fontSize: 11,
                    fontWeight: active ? 700 : 500,
                    cursor: disabled ? 'not-allowed' : 'pointer',
                    fontFamily: 'inherit',
                  }}
                >
                  {item === 'snapshot'
                    ? 'Nightly'
                    : spotUpdateStatus === 'loading'
                      ? 'Updating…'
                      : 'Spot-updated'}
                </button>
              );
            })}
          </div>
        ) : null}
      </div>

      <div style={{ marginTop: 20 }}>
        <div className="qv-move-comparison-axis" aria-hidden="true">
          <span />
          <div style={{ position: 'relative', height: 16 }}>
            {ticks.map((tick) => (
              <span
                key={tick}
                className="mono tnum"
                style={{
                  position: 'absolute',
                  left: `${tick * 100}%`,
                  transform:
                    tick === 0
                      ? 'none'
                      : tick === 1
                        ? 'translateX(-100%)'
                        : 'translateX(-50%)',
                  color: 'var(--ink-4)',
                  fontSize: 9,
                }}
              >
                {percent(maxMove * tick)}
              </span>
            ))}
          </div>
        </div>

        <div style={{ display: 'grid', gap: 10 }}>
          {optionsMovePct != null ? (
            <MoveRow
              label="Options-implied"
              value={optionsMovePct}
              detail="ATM straddle"
              spot={spot}
              maxMove={maxMove}
              tone="var(--flag)"
            />
          ) : null}
          {modelMovePct != null && modelQuantiles ? (
            <MoveRow
              label={modelIsSpotUpdated ? 'Spot-updated model' : 'Nightly model'}
              value={modelMovePct}
              detail={`P10–P90 ${percent(modelQuantiles.p10)}–${percent(modelQuantiles.p90)}`}
              spot={spot}
              maxMove={maxMove}
              tone="var(--brand-blue-1)"
              quantiles={modelQuantiles}
            />
          ) : null}
          {historicalMovePct != null ? (
            <MoveRow
              label="Historical median"
              value={historicalMovePct}
              detail={
                historyCount > 0
                  ? `Last ${historyCount} earnings`
                  : 'Realized earnings moves'
              }
              spot={spot}
              maxMove={maxMove}
              tone="var(--ink-2)"
            />
          ) : null}
        </div>
      </div>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 7,
          marginTop: 16,
          paddingTop: 12,
          borderTop: '1px solid var(--line)',
        }}
      >
        {exceedanceValue ? (
          <span
            className="mono tnum"
            style={{
              padding: '4px 8px',
              borderRadius: 999,
              color: 'var(--brand-blue-1)',
              border:
                '1px solid color-mix(in oklab, var(--brand-blue-1) 30%, var(--line))',
              background:
                'color-mix(in oklab, var(--brand-blue-1) 8%, transparent)',
              fontSize: 10,
              fontWeight: 650,
            }}
          >
            Straddle exceedance {exceedanceValue}
          </span>
        ) : null}
        {comparison ? (
          <strong
            style={{
              color: 'var(--brand-blue-1)',
              fontSize: 11,
              fontWeight: 650,
            }}
          >
            {comparison}
          </strong>
        ) : null}
        {ivRank != null ? (
          <span style={{ color: 'var(--ink-4)', fontSize: 10 }}>
            IV rank {Math.round(ivRank * 100)}%
          </span>
        ) : null}
        {modelQuantiles ? (
          <span style={{ color: 'var(--ink-4)', fontSize: 10 }}>
            Light band P10–P90 · dark band P25–P75 · line P50
          </span>
        ) : null}
        {exceedanceValue ? (
          <span style={{ color: 'var(--ink-4)', fontSize: 10 }}>
            Quantile interpolation · before spreads, fees, and post-event IV
            change
          </span>
        ) : null}
      </div>

      {mode === 'live' ? (
        <div
          style={{
            marginTop: 8,
            color:
              spotUpdateStatus === 'unavailable'
                ? 'var(--flag)'
                : 'var(--ink-4)',
            fontSize: 10,
            lineHeight: 1.4,
          }}
        >
          {spotUpdateStatus === 'unavailable' && unavailableReason
            ? `Spot update unavailable; showing nightly model. ${unavailableReason}`
            : modelMeta}
        </div>
      ) : null}
    </section>
  );
}
