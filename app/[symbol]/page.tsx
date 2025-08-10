'use client';

import { useParams } from 'next/navigation';
import { useOptions } from '@/hooks/useOptions';
import { useExpectedMove } from '@/hooks/useExpectedMove';
import { useEarnings } from '@/hooks/useEarnings';
import ExpectedMoveCard from '@/components/ExpectedMoveCard';
import IVRankSparkline from '@/components/IVRankSparkline';
import EarningsPanel from '@/components/EarningsPanel';
import MiniOptionsChain from '@/components/MiniOptionsChain';
import SymbolSearch from '@/components/SymbolSearch';
import { AlertCircle, Loader2 } from 'lucide-react';

export default function SymbolPage() {
  const params = useParams();
  const symbol = (params.symbol as string)?.toUpperCase();

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
          {earningsData && <EarningsPanel data={earningsData} symbol={symbol} />}
        </div>
      </div>
    </div>
  );
}
