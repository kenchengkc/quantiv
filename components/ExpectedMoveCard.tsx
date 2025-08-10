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

    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium text-gray-600">{label}</span>
          <span className="text-sm font-bold">±{range.percentage.toFixed(2)}%</span>
        </div>
        <div className="relative">
          <div className="flex items-center justify-between text-xs">
            <span className="flex items-center gap-1 text-red-600">
              <TrendingDown className="h-3 w-3" />
              ${range.lower.toFixed(2)}
            </span>
            <span className="flex items-center gap-1 text-green-600">
              ${range.upper.toFixed(2)}
              <TrendingUp className="h-3 w-3" />
            </span>
          </div>
          <div className="mt-2 h-2 bg-gray-200 rounded-full relative overflow-hidden">
            <div 
              className="absolute h-full bg-gradient-to-r from-red-500 via-yellow-500 to-green-500"
              style={{
                left: `${Math.max(0, ((range.lower - spotPrice * 0.8) / (spotPrice * 0.4)) * 100)}%`,
                right: `${Math.max(0, ((spotPrice * 1.2 - range.upper) / (spotPrice * 0.4)) * 100)}%`
              }}
            />
            <div 
              className="absolute w-0.5 h-full bg-gray-900"
              style={{ left: '50%' }}
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
