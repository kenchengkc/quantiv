'use client';

import { useState } from 'react';
import { ChevronDown } from 'lucide-react';

interface OptionsData {
  chain: {
    expirations: Array<{ date: string; dte: number }>;
    strikes: Record<string, Record<string, {
      strike: number;
      type: 'call' | 'put';
      bid: number | null;
      ask: number | null;
      mark: number;
      volume: number;
      openInterest: number;
      iv: number | null;
      delta: number | null;
      inTheMoney: boolean;
    }>>;
    quote: { last: number };
  };
  atmStrike: number | null;
}

interface MiniOptionsChainProps {
  data: OptionsData;
}

export default function MiniOptionsChain({ data }: MiniOptionsChainProps) {
  const [selectedExpiration, setSelectedExpiration] = useState(data.chain.expirations[0]?.date);
  
  const spotPrice = data.chain.quote.last;
  const strikes = data.chain.strikes[selectedExpiration] || {};
  
  // Get unique strikes sorted numerically
  const uniqueStrikes = [...new Set(Object.values(strikes).map(opt => opt.strike))].sort((a, b) => a - b);
  
  // Filter to show ATM ± 5 strikes
  const atmIndex = uniqueStrikes.findIndex(s => s >= spotPrice);
  const displayStrikes = uniqueStrikes.slice(Math.max(0, atmIndex - 5), atmIndex + 6);

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">Options Chain</h2>
        <div className="relative">
          <select
            value={selectedExpiration}
            onChange={(e) => setSelectedExpiration(e.target.value)}
            className="appearance-none bg-gray-50 border border-gray-300 rounded-lg px-3 py-1.5 pr-8 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {data.chain.expirations.slice(0, 5).map(exp => (
              <option key={exp.date} value={exp.date}>
                {new Date(exp.date).toLocaleDateString()} ({exp.dte}d)
              </option>
            ))}
          </select>
          <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 h-4 w-4 pointer-events-none text-gray-500" />
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200">
              <th colSpan={4} className="text-center py-2 font-medium text-green-600">Calls</th>
              <th className="text-center py-2 font-medium">Strike</th>
              <th colSpan={4} className="text-center py-2 font-medium text-red-600">Puts</th>
            </tr>
            <tr className="text-xs text-gray-500 border-b">
              <th className="text-left py-1">OI</th>
              <th className="text-left py-1">Vol</th>
              <th className="text-left py-1">IV</th>
              <th className="text-left py-1">Bid/Ask</th>
              <th className="text-center py-1"></th>
              <th className="text-right py-1">Bid/Ask</th>
              <th className="text-right py-1">IV</th>
              <th className="text-right py-1">Vol</th>
              <th className="text-right py-1">OI</th>
            </tr>
          </thead>
          <tbody>
            {displayStrikes.map(strike => {
              const call = strikes[`${strike}_call`];
              const put = strikes[`${strike}_put`];
              const isATM = strike === data.atmStrike;
              
              return (
                <tr key={strike} className={`border-b border-gray-100 ${isATM ? 'bg-blue-50' : ''}`}>
                  {/* Calls */}
                  <td className="py-1.5 text-xs">{call?.openInterest || '-'}</td>
                  <td className="py-1.5 text-xs">{call?.volume || '-'}</td>
                  <td className="py-1.5 text-xs">{call?.iv ? `${(call.iv * 100).toFixed(0)}%` : '-'}</td>
                  <td className="py-1.5 text-xs">
                    {call?.bid && call?.ask ? `${call.bid.toFixed(2)}/${call.ask.toFixed(2)}` : '-'}
                  </td>
                  
                  {/* Strike */}
                  <td className={`py-1.5 text-center font-medium ${isATM ? 'text-blue-600' : ''}`}>
                    {strike.toFixed(2)}
                  </td>
                  
                  {/* Puts */}
                  <td className="py-1.5 text-xs text-right">
                    {put?.bid && put?.ask ? `${put.bid.toFixed(2)}/${put.ask.toFixed(2)}` : '-'}
                  </td>
                  <td className="py-1.5 text-xs text-right">{put?.iv ? `${(put.iv * 100).toFixed(0)}%` : '-'}</td>
                  <td className="py-1.5 text-xs text-right">{put?.volume || '-'}</td>
                  <td className="py-1.5 text-xs text-right">{put?.openInterest || '-'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex items-center justify-between text-xs text-gray-500">
        <span>ATM Strike: {data.atmStrike?.toFixed(2) || 'N/A'}</span>
        <span>Spot: ${spotPrice.toFixed(2)}</span>
      </div>
    </div>
  );
}
