'use client';

import { Calendar, TrendingUp, TrendingDown, Clock, DollarSign, Target, CheckCircle, XCircle } from 'lucide-react';

interface EarningsEvent {
  date: string;
  actualEPS?: number;
  estimatedEPS?: number;
  actualRevenue?: number;
  estimatedRevenue?: number;
  epsSurprise?: number;
  epsSurprisePercent?: number;
  revenueSurprise?: number;
  revenueSurprisePercent?: number;
  priceMovePercent?: number;
  isUpcoming?: boolean;
  timing?: 'BMO' | 'AMC' | 'UNKNOWN';
}

interface EarningsCalendarProps {
  symbol: string;
  data: {
    nextEarningsDate?: string;
    nextEarningsTime?: 'BMO' | 'AMC' | 'UNKNOWN';
    estimatedEPS?: number;
    estimatedRevenue?: number;
    historicalEarnings: EarningsEvent[];
    stats: {
      avgMove: number;
      avgAbsMove: number;
      beatRate: number;
      avgBeat: number;
      revenueBeatRate?: number;
      avgRevenueBeat?: number;
    };
  };
}

export default function EarningsCalendar({ symbol, data }: EarningsCalendarProps) {
  // Prepare earnings events (upcoming + historical)
  const upcomingEarning: EarningsEvent | null = data.nextEarningsDate ? {
    date: data.nextEarningsDate,
    estimatedEPS: data.estimatedEPS,
    estimatedRevenue: data.estimatedRevenue,
    isUpcoming: true,
    timing: data.nextEarningsTime
  } : null;

  const allEarnings = [
    ...(upcomingEarning ? [upcomingEarning] : []),
    ...data.historicalEarnings.slice(0, 3) // Show last 3 historical earnings
  ];

  const formatCurrency = (value: number | undefined, isRevenue = false) => {
    if (value === undefined || value === null) return 'N/A';
    if (isRevenue) {
      return `$${(value / 1000000000).toFixed(2)}B`; // Convert to billions
    }
    return `$${value.toFixed(2)}`;
  };

  const formatPercent = (value: number | undefined) => {
    if (value === undefined || value === null) return 'N/A';
    const sign = value >= 0 ? '+' : '';
    return `${sign}${value.toFixed(1)}%`;
  };

  const getTimingDisplay = (timing?: 'BMO' | 'AMC' | 'UNKNOWN') => {
    switch (timing) {
      case 'BMO': return 'Before Market Open';
      case 'AMC': return 'After Market Close';
      default: return 'Time TBD';
    }
  };

  const getSurpriseIcon = (surprise: number | undefined) => {
    if (surprise === undefined || surprise === null) return null;
    return surprise > 0 ? 
      <CheckCircle className="h-4 w-4 text-green-600" /> : 
      <XCircle className="h-4 w-4 text-red-600" />;
  };

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
      <div className="flex items-center gap-2 mb-6">
        <Calendar className="h-5 w-5 text-blue-500" />
        <h2 className="text-lg font-semibold">Earnings Calendar</h2>
      </div>

      {/* Earnings Events */}
      <div className="space-y-4">
        {allEarnings.map((earning, index) => (
          <div 
            key={index}
            className={`p-4 rounded-lg border ${
              earning.isUpcoming 
                ? 'bg-blue-50 border-blue-200' 
                : 'bg-gray-50 border-gray-200'
            }`}
          >
            {/* Header */}
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <span className={`font-semibold ${
                  earning.isUpcoming ? 'text-blue-700' : 'text-gray-700'
                }`}>
                  {earning.isUpcoming ? 'Next Earnings' : 'Past Earnings'}
                </span>
                {earning.isUpcoming && (
                  <span className="px-2 py-1 bg-blue-100 text-blue-700 text-xs rounded-full font-medium">
                    UPCOMING
                  </span>
                )}
              </div>
              <div className="text-right">
                <div className="font-bold text-gray-900">
                  {new Date(earning.date).toLocaleDateString('en-US', {
                    month: 'short',
                    day: 'numeric',
                    year: 'numeric'
                  })}
                </div>
                {earning.timing && (
                  <div className="text-xs text-gray-500 flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {getTimingDisplay(earning.timing)}
                  </div>
                )}
              </div>
            </div>

            {/* EPS and Revenue Data */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* EPS Section */}
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-sm font-medium text-gray-600">
                  <Target className="h-4 w-4" />
                  Earnings Per Share
                </div>
                
                <div className="space-y-1">
                  {earning.estimatedEPS !== undefined && (
                    <div className="flex justify-between text-sm">
                      <span className="text-gray-600">Estimate:</span>
                      <span className="font-medium">{formatCurrency(earning.estimatedEPS)}</span>
                    </div>
                  )}
                  
                  {earning.actualEPS !== undefined && (
                    <div className="flex justify-between text-sm">
                      <span className="text-gray-600">Actual:</span>
                      <span className="font-bold">{formatCurrency(earning.actualEPS)}</span>
                    </div>
                  )}
                  
                  {earning.epsSurprise !== undefined && (
                    <div className="flex justify-between items-center text-sm">
                      <span className="text-gray-600">Surprise:</span>
                      <div className="flex items-center gap-1">
                        {getSurpriseIcon(earning.epsSurprise)}
                        <span className={`font-bold ${
                          earning.epsSurprise >= 0 ? 'text-green-600' : 'text-red-600'
                        }`}>
                          {formatCurrency(earning.epsSurprise)} ({formatPercent(earning.epsSurprisePercent)})
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* Revenue Section */}
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-sm font-medium text-gray-600">
                  <DollarSign className="h-4 w-4" />
                  Revenue
                </div>
                
                <div className="space-y-1">
                  {earning.estimatedRevenue !== undefined && (
                    <div className="flex justify-between text-sm">
                      <span className="text-gray-600">Estimate:</span>
                      <span className="font-medium">{formatCurrency(earning.estimatedRevenue, true)}</span>
                    </div>
                  )}
                  
                  {earning.actualRevenue !== undefined && (
                    <div className="flex justify-between text-sm">
                      <span className="text-gray-600">Actual:</span>
                      <span className="font-bold">{formatCurrency(earning.actualRevenue, true)}</span>
                    </div>
                  )}
                  
                  {earning.revenueSurprise !== undefined && (
                    <div className="flex justify-between items-center text-sm">
                      <span className="text-gray-600">Surprise:</span>
                      <div className="flex items-center gap-1">
                        {getSurpriseIcon(earning.revenueSurprise)}
                        <span className={`font-bold ${
                          earning.revenueSurprise >= 0 ? 'text-green-600' : 'text-red-600'
                        }`}>
                          {formatPercent(earning.revenueSurprisePercent)}
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* Price Movement (for historical earnings) */}
            {!earning.isUpcoming && earning.priceMovePercent !== undefined && (
              <div className="mt-3 pt-3 border-t border-gray-200">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-gray-600">Stock Price Reaction:</span>
                  <div className={`flex items-center gap-1 font-bold ${
                    earning.priceMovePercent >= 0 ? 'text-green-600' : 'text-red-600'
                  }`}>
                    {earning.priceMovePercent >= 0 ? 
                      <TrendingUp className="h-4 w-4" /> : 
                      <TrendingDown className="h-4 w-4" />
                    }
                    {formatPercent(earning.priceMovePercent)}
                  </div>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Summary Statistics */}
      {data.stats && (
        <div className="mt-6 pt-4 border-t border-gray-200">
          <h3 className="text-sm font-semibold text-gray-700 mb-3">Earnings Performance Summary</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div className="text-center p-2 bg-gray-50 rounded">
              <div className="font-bold text-gray-900">{data.stats.avgMove.toFixed(1)}%</div>
              <div className="text-gray-600">Avg Move</div>
            </div>
            <div className="text-center p-2 bg-gray-50 rounded">
              <div className="font-bold text-gray-900">{(data.stats.beatRate * 100).toFixed(0)}%</div>
              <div className="text-gray-600">EPS Beat Rate</div>
            </div>
            {data.stats.revenueBeatRate !== undefined && (
              <div className="text-center p-2 bg-gray-50 rounded">
                <div className="font-bold text-gray-900">{(data.stats.revenueBeatRate * 100).toFixed(0)}%</div>
                <div className="text-gray-600">Revenue Beat Rate</div>
              </div>
            )}
            <div className="text-center p-2 bg-gray-50 rounded">
              <div className="font-bold text-gray-900">{formatCurrency(data.stats.avgBeat)}</div>
              <div className="text-gray-600">Avg EPS Beat</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
