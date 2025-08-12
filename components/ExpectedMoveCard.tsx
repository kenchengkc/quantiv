'use client';

import { TrendingUp, TrendingDown, Activity } from 'lucide-react';

interface ExpectedMoveCardProps {
  data: {
    symbol: string;
    spotPrice: number;
    summary: {
      daily?: { move: number; percentage: number; lower: number; upper: number } | null;
      weekly?: { move: number; percentage: number; lower: number; upper: number } | null;
      monthly?: { move: number; percentage: number; lower: number; upper: number } | null;
    };
  };
}

export default function ExpectedMoveCard({ data }: ExpectedMoveCardProps) {
  const { spotPrice, summary } = data;

  const renderRange = (
    label: string,
    range: { move: number; percentage: number; lower: number; upper: number } | null | undefined
  ) => {
    if (!range) return null;

    // Defensive programming: handle missing or invalid percentage
    const percentage = range.percentage ?? 0;
    const move = range.move ?? 0;
    const lower = range.lower ?? 0;
    const upper = range.upper ?? 0;

    // Debug logging to see what data we're getting
    if (typeof percentage !== 'number') {
      console.warn(`ExpectedMoveCard: Invalid percentage for ${label}:`, range);
    }

    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium text-gray-600">{label}</span>
          <span className="text-sm font-bold">±{percentage.toFixed(2)}%</span>
        </div>
        <div className="relative">
          <div className="flex items-center justify-between text-xs">
            <span className="flex items-center gap-1 text-red-600">
              <TrendingDown className="h-3 w-3" />
              ${lower.toFixed(2)}
            </span>
            <span className="flex items-center gap-1 text-green-600">
              ${upper.toFixed(2)}
              <TrendingUp className="h-3 w-3" />
            </span>
          </div>
          <div className="mt-2 h-2 bg-gray-100 rounded-full overflow-hidden">
            <div 
              className="h-full bg-gradient-to-r from-red-400 via-yellow-400 to-green-400"
              style={{ width: '100%' }}
            />
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
      <div className="flex items-center gap-2 mb-4">
        <Activity className="h-5 w-5 text-blue-500" />
        <h2 className="text-lg font-semibold">Expected Move</h2>
      </div>
      
      <div className="space-y-4">
        {renderRange('Daily (1D)', summary.daily)}
        {renderRange('Weekly (7D)', summary.weekly)}
        {renderRange('Monthly (30D)', summary.monthly)}
      </div>

      <div className="mt-4 pt-4 border-t border-gray-100">
        <p className="text-xs text-gray-500">
          Calculated using straddle pricing and implied volatility. 
          Represents 1 standard deviation move (~68% probability).
        </p>
      </div>
    </div>
  );
}
