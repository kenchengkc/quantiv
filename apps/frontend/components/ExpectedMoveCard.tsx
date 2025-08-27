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
  const { summary } = data;

  // Define reasonable maximum percentages for scaling bars
  // These represent what should be considered "large" moves for each timeframe
  const MAX_DAILY_PERCENTAGE = 5;   // 5% daily move is quite large
  const MAX_WEEKLY_PERCENTAGE = 15;  // 15% weekly move is significant
  const MAX_MONTHLY_PERCENTAGE = 25; // 2% monthly move is substantial

  const getMaxPercentage = (label: string): number => {
    if (label.includes('Daily')) return MAX_DAILY_PERCENTAGE;
    if (label.includes('Weekly')) return MAX_WEEKLY_PERCENTAGE;
    if (label.includes('Monthly')) return MAX_MONTHLY_PERCENTAGE;
    return MAX_MONTHLY_PERCENTAGE; // fallback
  };

  const renderRange = (
    label: string,
    range: { move: number; percentage: number; lower: number; upper: number } | null | undefined
  ) => {
    if (!range) return null;

    // Defensive programming: handle missing or invalid percentage
    const percentage = range.percentage ?? 0;
    const lower = range.lower ?? 0;
    const upper = range.upper ?? 0;

    // Debug logging to see what data we're getting
    if (typeof percentage !== 'number') {
      console.warn(`ExpectedMoveCard: Invalid percentage for ${label}:`, range);
    }

    // Calculate bar width as percentage of maximum, capped at 100%
    const maxPercentage = getMaxPercentage(label);
    const barWidth = Math.min((Math.abs(percentage) / maxPercentage) * 100, 100);

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
              className="h-full bg-gradient-to-r from-red-400 via-yellow-400 to-green-400 transition-all duration-300"
              style={{ width: `${barWidth}%` }}
            />
          </div>
          {/* Show scale indicator for context */}
          <div className="mt-1 text-xs text-gray-400 text-right">
            Scale: {maxPercentage}% max
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
