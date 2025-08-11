'use client';

import React from 'react';
import { Star } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useWatchlist } from '@/hooks/useWatchlist';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

interface WatchlistToggleProps {
  symbol: string;
  name?: string;
  price?: number;
  change?: number;
  changePercent?: number;
  size?: 'sm' | 'md' | 'lg';
  showTooltip?: boolean;
  className?: string;
  onToggle?: (isInWatchlist: boolean) => void;
}

export function WatchlistToggle({
  symbol,
  name,
  price,
  change,
  changePercent,
  size = 'md',
  showTooltip = true,
  className,
  onToggle,
}: WatchlistToggleProps) {
  const { isInWatchlist, toggleWatchlist, isAdding, isRemoving } = useWatchlist();
  const isWatched = isInWatchlist(symbol);
  const isLoading = isAdding || isRemoving;
  
  const sizeClasses = {
    sm: 'h-4 w-4',
    md: 'h-5 w-5',
    lg: 'h-6 w-6',
  };
  
  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    
    toggleWatchlist({
      symbol,
      name,
      price,
      change,
      changePercent,
    });
    
    onToggle?.(!isWatched);
  };
  
  const button = (
    <button
      onClick={handleClick}
      disabled={isLoading}
      className={cn(
        'inline-flex items-center justify-center rounded-md transition-all',
        'hover:bg-gray-100 dark:hover:bg-gray-800',
        'focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        size === 'sm' && 'p-1',
        size === 'md' && 'p-1.5',
        size === 'lg' && 'p-2',
        className
      )}
      aria-label={isWatched ? `Remove ${symbol} from watchlist` : `Add ${symbol} to watchlist`}
    >
      <Star
        className={cn(
          sizeClasses[size],
          'transition-all',
          isWatched ? 'fill-yellow-500 text-yellow-500' : 'text-gray-400 dark:text-gray-600',
          !isLoading && !isWatched && 'hover:text-gray-600 dark:hover:text-gray-400'
        )}
      />
    </button>
  );
  
  if (!showTooltip) {
    return button;
  }
  
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          {button}
        </TooltipTrigger>
        <TooltipContent>
          <p>{isWatched ? 'Remove from watchlist' : 'Add to watchlist'}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
