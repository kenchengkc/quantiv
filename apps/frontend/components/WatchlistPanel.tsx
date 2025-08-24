'use client';

import React from 'react';
import Link from 'next/link';
import { useWatchlist } from '@/hooks/useWatchlist';
import { WatchlistToggle } from '@/components/WatchlistToggle';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { TrendingUp, TrendingDown, Minus, Trash2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';

export function WatchlistPanel() {
  const { watchlist, clearWatchlist } = useWatchlist();
  
  if (watchlist.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-lg font-semibold">Watchlist</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            No symbols in your watchlist. Add symbols using the star icon.
          </p>
        </CardContent>
      </Card>
    );
  }
  
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-lg font-semibold">
          Watchlist ({watchlist.length})
        </CardTitle>
        {watchlist.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              if (confirm('Clear all symbols from watchlist?')) {
                clearWatchlist();
              }
            }}
            className="text-red-600 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300"
          >
            <Trash2 className="h-4 w-4 mr-1" />
            Clear
          </Button>
        )}
      </CardHeader>
      <CardContent className="p-0">
        <div className="divide-y divide-gray-200 dark:divide-gray-700">
          {watchlist.map((item) => (
            <Link
              key={item.symbol}
              href={`/${item.symbol}`}
              className="flex items-center justify-between p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
            >
              <div className="flex items-center gap-3">
                <WatchlistToggle
                  symbol={item.symbol}
                  name={item.name}
                  price={item.price}
                  change={item.change}
                  changePercent={item.changePercent}
                  size="sm"
                  showTooltip={false}
                />
                <div>
                  <div className="font-semibold text-sm">{item.symbol}</div>
                  {item.name && (
                    <div className="text-xs text-gray-500 dark:text-gray-400 truncate max-w-[150px]">
                      {item.name}
                    </div>
                  )}
                </div>
              </div>
              
              <div className="flex items-center gap-4">
                {item.price !== undefined && (
                  <div className="text-right">
                    <div className="font-medium text-sm">
                      ${item.price.toFixed(2)}
                    </div>
                    {item.change !== undefined && item.changePercent !== undefined && (
                      <div className="flex items-center gap-1 justify-end">
                        {item.change > 0 ? (
                          <TrendingUp className="h-3 w-3 text-green-600 dark:text-green-400" />
                        ) : item.change < 0 ? (
                          <TrendingDown className="h-3 w-3 text-red-600 dark:text-red-400" />
                        ) : (
                          <Minus className="h-3 w-3 text-gray-400" />
                        )}
                        <span
                          className={cn(
                            'text-xs font-medium',
                            item.change > 0
                              ? 'text-green-600 dark:text-green-400'
                              : item.change < 0
                              ? 'text-red-600 dark:text-red-400'
                              : 'text-gray-500 dark:text-gray-400'
                          )}
                        >
                          {item.change > 0 ? '+' : ''}{item.change.toFixed(2)} ({item.changePercent.toFixed(2)}%)
                        </span>
                      </div>
                    )}
                  </div>
                )}
                
                <Badge variant="outline" className="text-xs">
                  View
                </Badge>
              </div>
            </Link>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
