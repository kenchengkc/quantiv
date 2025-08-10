'use client';

import { TrendingUp, BarChart3 } from 'lucide-react';

interface IVRankSparklineProps {
  data: {
    current: number;
    rank: number;
    percentile: number;
    high52Week: number;
    low52Week: number;
  };
}

export default function IVRankSparkline({ data }: IVRankSparklineProps) {
  const { current, rank, percentile, high52Week, low52Week } = data;
  
  // Generate mock sparkline data (in production, use historical data)
  const sparklineData = Array.from({ length: 52 }, (_, i) => {
    const week = i;
    const trend = Math.sin(week / 8) * 0.3;
    const noise = (Math.random() - 0.5) * 0.2;
    return low52Week + (high52Week - low52Week) * (0.5 + trend + noise);
  });
  sparklineData.push(current);

  const max = Math.max(...sparklineData);
  const min = Math.min(...sparklineData);
  const range = max - min;

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
      <div className="flex items-center gap-2 mb-4">
        <BarChart3 className="h-5 w-5 text-purple-500" />
        <h2 className="text-lg font-semibold">IV Stats</h2>
      </div>

      <div className="grid grid-cols-2 gap-4 mb-4">
        <div>
          <p className="text-xs text-gray-500">IV Rank</p>
          <p className="text-2xl font-bold">{rank.toFixed(0)}</p>
        </div>
        <div>
          <p className="text-xs text-gray-500">IV Percentile</p>
          <p className="text-2xl font-bold">{percentile.toFixed(0)}</p>
        </div>
      </div>

      <div className="h-20 flex items-end gap-0.5">
        {sparklineData.map((value, i) => {
          const height = ((value - min) / range) * 100;
          const isCurrentWeek = i === sparklineData.length - 1;
          return (
            <div
              key={i}
              className={`flex-1 rounded-t transition-colors ${
                isCurrentWeek ? 'bg-purple-500' : 
                value > current ? 'bg-gray-300' : 'bg-purple-200'
              }`}
              style={{ height: `${height}%` }}
            />
          );
        })}
      </div>

      <div className="flex justify-between mt-2 text-xs text-gray-500">
        <span>52W Low: {low52Week.toFixed(1)}%</span>
        <span>Current: {current.toFixed(1)}%</span>
        <span>52W High: {high52Week.toFixed(1)}%</span>
      </div>

      <div className="mt-4 pt-4 border-t border-gray-100">
        <div className={`text-sm font-medium ${rank > 50 ? 'text-orange-600' : 'text-blue-600'}`}>
          {rank > 50 ? (
            <span className="flex items-center gap-1">
              <TrendingUp className="h-3 w-3" />
              Elevated volatility
            </span>
          ) : (
            <span>Normal volatility range</span>
          )}
        </div>
      </div>
    </div>
  );
}
