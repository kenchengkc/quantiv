'use client';

import { TrendingUp, TrendingDown, Activity } from 'lucide-react';

interface ExpectedMoveCardProps {
  data: {
    symbol: string;
    timestamp: string;
    forecasts: Array<{
      underlying: string;
      quote_ts: string;
      exp_date: string;
      horizon: string;
      em_baseline: number;
      band68_low: number;
      band68_high: number;
      band95_low?: number;
      band95_high?: number;
    }>;
    live_data: {
      symbol: string;
      price: number;
      change: number;
      change_percent: number;
      volume: number;
      timestamp: string;
    };
    metadata: {
      forecast_count: number;
      horizons_requested: string[];
      has_live_data: boolean;
    };
  };
}

export default function ExpectedMoveCard({ data }: ExpectedMoveCardProps) {
  const { forecasts, live_data } = data;
  const currentPrice = live_data.price;

  // Define reasonable maximum percentages for scaling bars
  const MAX_DAILY_PERCENTAGE = 5;   // 5% daily move is quite large
  const MAX_WEEKLY_PERCENTAGE = 15;  // 15% weekly move is significant
  const MAX_TO_EXP_PERCENTAGE = 25; // 25% move to expiration is substantial

  const getMaxPercentage = (horizon: string): number => {
    if (horizon === '1d') return MAX_DAILY_PERCENTAGE;
    if (horizon === '5d') return MAX_WEEKLY_PERCENTAGE;
    if (horizon === 'to_exp') return MAX_TO_EXP_PERCENTAGE;
    return MAX_TO_EXP_PERCENTAGE; // fallback
  };

  const getHorizonLabel = (horizon: string): string => {
    if (horizon === '1d') return 'Daily (1D)';
    if (horizon === '5d') return 'Weekly (5D)';
    if (horizon === 'to_exp') return 'To Expiration';
    return horizon;
  };

  const renderForecast = (forecast: {
    horizon: string;
    em_baseline: number;
    band68_low: number;
    band68_high: number;
  }) => {
    // Calculate price levels from percentage moves
    const lowerPrice = currentPrice * (1 - forecast.band68_low);
    const upperPrice = currentPrice * (1 + forecast.band68_high);
    const movePercentage = forecast.em_baseline * 100; // Convert to percentage

    // Calculate bar width as percentage of maximum, capped at 100%
    const maxPercentage = getMaxPercentage(forecast.horizon);
    const barWidth = Math.min((movePercentage / maxPercentage) * 100, 100);

    return (
      <div key={forecast.horizon} className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium text-gray-600">
            {getHorizonLabel(forecast.horizon)}
          </span>
          <span className="text-sm font-bold">±{movePercentage.toFixed(2)}%</span>
        </div>
        <div className="relative">
          <div className="flex items-center justify-between text-xs">
            <span className="flex items-center gap-1 text-red-600">
              <TrendingDown className="h-3 w-3" />
              ${lowerPrice.toFixed(2)}
            </span>
            <span className="flex items-center gap-1 text-green-600">
              ${upperPrice.toFixed(2)}
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
        <h2 className="text-lg font-semibold">🤖 ML-Powered Expected Move</h2>
      </div>
      
      <div className="space-y-4">
        {forecasts.map(renderForecast)}
      </div>

      <div className="mt-4 pt-4 border-t border-gray-100">
        <p className="text-xs text-gray-500">
          Machine learning powered forecasts using historical options data.
          Represents 68% confidence bands based on trained models.
        </p>
        <div className="mt-2 text-xs text-gray-400">
          Current price: ${currentPrice.toFixed(2)} • {forecasts.length} horizons
        </div>
      </div>
    </div>
  );
}
