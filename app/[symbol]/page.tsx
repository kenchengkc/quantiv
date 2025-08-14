'use client';

import { useParams } from 'next/navigation';
import { useOptions } from '@/hooks/useOptions';
import { useExpectedMove } from '@/hooks/useExpectedMove';
import { useEarnings } from '@/hooks/useEarnings';
import { usePageTimeout } from '@/hooks/usePageTimeout';
import ExpectedMoveCard from '@/components/ExpectedMoveCard';
import IVRankSparkline from '@/components/IVRankSparkline';
import EarningsCalendar from '@/components/EarningsCalendar';
import MiniOptionsChain from '@/components/MiniOptionsChain';
import SymbolSearch from '@/components/SymbolSearch';
import { WatchlistToggle } from '@/components/WatchlistToggle';
import { AlertCircle, Loader2 } from 'lucide-react';

export default function SymbolPage() {
  const params = useParams();
  const symbol = (params.symbol as string)?.toUpperCase();

  // Auto-redirect to homepage after 1 hour to prevent stale data analysis
  usePageTimeout({
    timeoutMinutes: 60,
    redirectPath: '/',
    onTimeout: () => {
      console.log(`[${symbol}] Session expired after 1 hour, redirecting to homepage`);
    }
  });

  const { data: optionsData, isLoading: optionsLoading, error: optionsError } = useOptions({ symbol });
  const { data: emData, isLoading: emLoading, error: emError } = useExpectedMove({ symbol });
  const { data: earningsData, isLoading: earningsLoading, error: earningsError } = useEarnings({ symbol });

  const isLoading = optionsLoading || emLoading || earningsLoading;
  const error = optionsError || emError || earningsError;

  if (!symbol) {
    return (
      <div className="container mx-auto p-6">
        <SymbolSearch />
      </div>
    );
  }

  if (error) {
    return (
      <div className="container mx-auto p-6">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-center gap-3">
          <AlertCircle className="h-5 w-5 text-red-500 flex-shrink-0" />
          <p className="text-red-700">
            Error loading data for {symbol}: {error.message}
          </p>
        </div>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="container mx-auto p-6 flex items-center justify-center min-h-[60vh]">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-blue-500" />
          <p className="text-gray-600">Loading {symbol} data...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="container mx-auto p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div>
            <h1 className="text-3xl font-bold">{symbol}</h1>
            {optionsData?.chain.quote && (
              <div className="flex items-center gap-4 mt-2 text-sm">
                <span className="text-2xl font-semibold">
                  ${optionsData.chain.quote.last.toFixed(2)}
                </span>
                <span className={`font-medium ${optionsData.chain.quote.change >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                  {optionsData.chain.quote.change >= 0 ? '+' : ''}{optionsData.chain.quote.change.toFixed(2)} 
                  ({optionsData.chain.quote.changePercent.toFixed(2)}%)
                </span>
              </div>
            )}
          </div>
          <WatchlistToggle
            symbol={symbol}
            name={optionsData?.chain.quote?.name}
            price={optionsData?.chain.quote?.last}
            change={optionsData?.chain.quote?.change}
            changePercent={optionsData?.chain.quote?.changePercent}
            size="lg"
          />
        </div>
        <SymbolSearch />
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Column */}
        <div className="lg:col-span-2 space-y-6">
          {emData && <ExpectedMoveCard data={emData} />}
          {optionsData && <MiniOptionsChain data={optionsData} />}
        </div>

        {/* Right Column */}
        <div className="space-y-6">
          {optionsData?.ivStats && <IVRankSparkline data={optionsData.ivStats} />}
          {earningsData && <EarningsCalendar data={earningsData} symbol={symbol} />}
        </div>
      </div>
    </div>
  );
}
