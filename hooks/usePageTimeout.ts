'use client';

import { useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';

interface UsePageTimeoutOptions {
  timeoutMinutes?: number;
  redirectPath?: string;
  onTimeout?: () => void;
}

/**
 * Hook to automatically redirect user after a specified timeout
 * Useful for ticker pages to prevent stale data analysis
 */
export function usePageTimeout({
  timeoutMinutes = 60, // Default 1 hour
  redirectPath = '/',
  onTimeout
}: UsePageTimeoutOptions = {}) {
  const router = useRouter();
  const timeoutRef = useRef<NodeJS.Timeout | null>(null);
  const startTimeRef = useRef<number>(Date.now());

  useEffect(() => {
    // Clear any existing timeout
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }

    // Set new timeout
    const timeoutMs = timeoutMinutes * 60 * 1000;
    
    timeoutRef.current = setTimeout(() => {
      console.log(`[page-timeout] Redirecting to ${redirectPath} after ${timeoutMinutes} minutes`);
      
      // Call custom timeout handler if provided
      if (onTimeout) {
        onTimeout();
      }
      
      // Redirect to specified path
      router.push(redirectPath);
    }, timeoutMs);

    // Cleanup function
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, [timeoutMinutes, redirectPath, onTimeout, router]);

  // Return time remaining for UI display if needed
  const getTimeRemaining = () => {
    const elapsed = Date.now() - startTimeRef.current;
    const remaining = (timeoutMinutes * 60 * 1000) - elapsed;
    return Math.max(0, remaining);
  };

  const getTimeRemainingFormatted = () => {
    const remaining = getTimeRemaining();
    const minutes = Math.floor(remaining / (60 * 1000));
    const seconds = Math.floor((remaining % (60 * 1000)) / 1000);
    return `${minutes}:${seconds.toString().padStart(2, '0')}`;
  };

  return {
    getTimeRemaining,
    getTimeRemainingFormatted
  };
}
