'use client';

import { Calendar, TrendingUp, TrendingDown } from 'lucide-react';

interface EarningsData {
  events: Array<{
    date: string;
    time: 'BMO' | 'AMC' | 'UNKNOWN';
    fiscalQuarter: string;
    historicalMoves?: Array<{
      date: string;
      priceMovePercent: number;
      epsSurprise: number | null;
    }>;
  }>;
  stats: {
    avgMove: number;
    avgAbsMove: number;
    beatRate: number;
    positiveReactionRate: number;
  } | null;
}

interface EarningsPanelProps {
  data: EarningsData;
  symbol: string;
}

export default function EarningsPanel({ data }: EarningsPanelProps) {
  const nextEarnings = data?.events?.[0];
  const historicalMoves = nextEarnings?.historicalMoves?.slice(0, 8) || [];

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
      <div className="flex items-center gap-2 mb-4">
        <Calendar className="h-5 w-5 text-orange-500" />
        <h2 className="text-lg font-semibold">Earnings</h2>
      </div>

      {nextEarnings && (
        <div className="mb-4 p-3 bg-orange-50 rounded-lg">
          <p className="text-sm font-medium">Next Earnings</p>
          <p className="text-lg font-bold">{new Date(nextEarnings.date).toLocaleDateString()}</p>
          <p className="text-xs text-gray-600">{nextEarnings.time} • {nextEarnings.fiscalQuarter}</p>
        </div>
      )}

      {historicalMoves.length > 0 && (
        <div>
          <p className="text-sm font-medium mb-2">Historical Moves</p>
          <div className="space-y-1">
            {historicalMoves.map((move, i) => (
              <div key={i} className="flex items-center justify-between text-xs">
                <span className="text-gray-500">
                  {new Date(move.date).toLocaleDateString('en-US', { month: 'short', year: '2-digit' })}
                </span>
                <div className="flex items-center gap-2">
                  {move.epsSurprise !== null && (
                    <span className={`font-medium ${move.epsSurprise > 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {move.epsSurprise > 0 ? '✓' : '✗'}
                    </span>
                  )}
                  <span className={`font-bold flex items-center gap-0.5 ${
                    move.priceMovePercent >= 0 ? 'text-green-600' : 'text-red-600'
                  }`}>
                    {move.priceMovePercent >= 0 ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
                    {Math.abs(move.priceMovePercent).toFixed(1)}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {data.stats && (
        <div className="mt-4 pt-4 border-t border-gray-100 grid grid-cols-2 gap-2 text-xs">
          <div>
            <span className="text-gray-500">Avg Move</span>
            <p className="font-bold">{(data.stats.avgMove || data.stats.avgAbsMove || 0).toFixed(1)}%</p>
          </div>
          <div>
            <span className="text-gray-500">Beat Rate</span>
            <p className="font-bold">{((data.stats.beatRate || 0) * 100).toFixed(0)}%</p>
          </div>
        </div>
      )}
    </div>
  );
}
