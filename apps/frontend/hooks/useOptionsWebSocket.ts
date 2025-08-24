/**
 * React Hook for Real-Time Options WebSocket Data
 * Manages WebSocket subscriptions and provides live minute-by-minute OHLC data
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { ProcessedOptionsAggregate } from '@/lib/services/polygonWebSocketService';

export interface WebSocketSubscription {
  contractTicker: string;
  subscribed: boolean;
  lastUpdate: string;
  messageCount: number;
}

export interface WebSocketConnectionStatus {
  connected: boolean;
  subscriptions: number;
  reconnectAttempts: number;
}

export interface UseOptionsWebSocketReturn {
  // Connection state
  isConnected: boolean;
  connectionStatus: WebSocketConnectionStatus | null;
  subscriptions: WebSocketSubscription[];
  
  // Real-time data
  latestData: ProcessedOptionsAggregate | null;
  historicalData: ProcessedOptionsAggregate[];
  
  // Subscription management
  subscribe: (contractTicker: string) => Promise<boolean>;
  unsubscribe: (contractTicker: string) => Promise<boolean>;
  batchSubscribe: (contractTickers: string[]) => Promise<{ success: number; failed: number }>;
  
  // Data filtering
  getDataForContract: (contractTicker: string) => ProcessedOptionsAggregate[];
  clearHistoricalData: () => void;
  
  // Status
  loading: boolean;
  error: string | null;
}

const MAX_HISTORICAL_RECORDS = 1000; // Limit memory usage

export function useOptionsWebSocket(): UseOptionsWebSocketReturn {
  const [isConnected, setIsConnected] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState<WebSocketConnectionStatus | null>(null);
  const [subscriptions, setSubscriptions] = useState<WebSocketSubscription[]>([]);
  const [latestData, setLatestData] = useState<ProcessedOptionsAggregate | null>(null);
  const [historicalData, setHistoricalData] = useState<ProcessedOptionsAggregate[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  const statusCheckInterval = useRef<NodeJS.Timeout | null>(null);

  /**
   * Check WebSocket status
   */
  const checkStatus = useCallback(async () => {
    try {
      const response = await fetch('/api/websocket?action=status');
      const result = await response.json();
      
      if (result.success && result.data) {
        setConnectionStatus(result.data.connectionStatus);
        setSubscriptions(result.data.subscriptions || []);
        setIsConnected(result.data.connectionStatus?.connected || false);
        setError(null);
      } else {
        setError(result.error || 'Failed to get WebSocket status');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Status check failed');
      setIsConnected(false);
    }
  }, []);

  /**
   * Subscribe to a contract
   */
  const subscribe = useCallback(async (contractTicker: string): Promise<boolean> => {
    if (!contractTicker.startsWith('O:')) {
      setError('Invalid contract ticker format');
      return false;
    }

    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`/api/websocket?action=subscribe&contract=${encodeURIComponent(contractTicker)}`);
      const result = await response.json();
      
      if (result.success && result.data?.subscribed) {
        await checkStatus(); // Refresh status
        return true;
      } else {
        setError(result.error || 'Subscription failed');
        return false;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Subscription failed');
      return false;
    } finally {
      setLoading(false);
    }
  }, [checkStatus]);

  /**
   * Unsubscribe from a contract
   */
  const unsubscribe = useCallback(async (contractTicker: string): Promise<boolean> => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`/api/websocket?action=unsubscribe&contract=${encodeURIComponent(contractTicker)}`);
      const result = await response.json();
      
      if (result.success && result.data?.unsubscribed) {
        await checkStatus(); // Refresh status
        
        // Remove historical data for this contract
        setHistoricalData(prev => prev.filter(data => data.contractTicker !== contractTicker));
        
        // Clear latest data if it's for this contract
        if (latestData?.contractTicker === contractTicker) {
          setLatestData(null);
        }
        
        return true;
      } else {
        setError(result.error || 'Unsubscription failed');
        return false;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unsubscription failed');
      return false;
    } finally {
      setLoading(false);
    }
  }, [checkStatus, latestData]);

  /**
   * Batch subscribe to multiple contracts
   */
  const batchSubscribe = useCallback(async (contractTickers: string[]): Promise<{ success: number; failed: number }> => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch('/api/websocket', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          action: 'subscribe',
          contracts: contractTickers
        })
      });

      const result = await response.json();
      
      if (result.success && result.data) {
        await checkStatus(); // Refresh status
        return {
          success: result.data.successCount || 0,
          failed: result.data.failureCount || 0
        };
      } else {
        setError(result.error || 'Batch subscription failed');
        return { success: 0, failed: contractTickers.length };
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Batch subscription failed');
      return { success: 0, failed: contractTickers.length };
    } finally {
      setLoading(false);
    }
  }, [checkStatus]);

  /**
   * Get historical data for a specific contract
   */
  const getDataForContract = useCallback((contractTicker: string): ProcessedOptionsAggregate[] => {
    return historicalData.filter(data => data.contractTicker === contractTicker);
  }, [historicalData]);

  /**
   * Clear historical data
   */
  const clearHistoricalData = useCallback(() => {
    setHistoricalData([]);
    setLatestData(null);
  }, []);

  // Set up status checking interval
  useEffect(() => {
    checkStatus(); // Initial check
    
    statusCheckInterval.current = setInterval(checkStatus, 10000); // Check every 10 seconds
    
    return () => {
      if (statusCheckInterval.current) {
        clearInterval(statusCheckInterval.current);
      }
    };
  }, [checkStatus]);

  // Simulate real-time data updates (in a real implementation, this would come from Server-Sent Events or WebSocket)
  useEffect(() => {
    // This is a placeholder for real-time data updates
    // In a production environment, you would:
    // 1. Set up Server-Sent Events (SSE) to receive real-time data from the server
    // 2. Or implement a WebSocket connection on the client side
    // 3. Or use polling to fetch latest data periodically
    
    const simulateDataUpdate = () => {
      // This would be replaced with actual real-time data handling
      // For now, we'll just check status periodically
    };

    const dataInterval = setInterval(simulateDataUpdate, 5000);
    
    return () => clearInterval(dataInterval);
  }, []);

  // Handle new data updates (this would be called when real-time data arrives)
  const handleNewData = useCallback((newData: ProcessedOptionsAggregate) => {
    setLatestData(newData);
    
    setHistoricalData(prev => {
      const updated = [...prev, newData];
      
      // Limit historical data to prevent memory issues
      if (updated.length > MAX_HISTORICAL_RECORDS) {
        return updated.slice(-MAX_HISTORICAL_RECORDS);
      }
      
      return updated;
    });
  }, []);

  return {
    // Connection state
    isConnected,
    connectionStatus,
    subscriptions,
    
    // Real-time data
    latestData,
    historicalData,
    
    // Subscription management
    subscribe,
    unsubscribe,
    batchSubscribe,
    
    // Data filtering
    getDataForContract,
    clearHistoricalData,
    
    // Status
    loading,
    error
  };
}
