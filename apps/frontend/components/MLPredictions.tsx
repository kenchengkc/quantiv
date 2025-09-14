'use client';

import { useState, useEffect } from 'react';
import { SparklesIcon } from '@heroicons/react/24/outline';

interface MLPrediction {
  symbol: string;
  prediction_date: string;
  earnings_date?: string;
  em_baseline: number;
  band68_low: number;
  band68_high: number;
  band95_low: number;
  band95_high: number;
  model_type: string;
  confidence: string;
}

interface MLInfo {
  status: string;
  model_type?: string;
  available_symbols?: number;
  training_date?: string;
  performance?: {
    mae?: number;
    rmse?: number;
    r2?: number;
  };
}

export function MLPredictions() {
  const [predictions, setPredictions] = useState<MLPrediction[]>([]);
  const [mlInfo, setMlInfo] = useState<MLInfo | null>(null);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch ML info and available symbols on mount
  useEffect(() => {
    const fetchMLInfo = async () => {
      try {
        const [infoRes, symbolsRes] = await Promise.all([
          fetch('/api/ml/info'),
          fetch('/api/ml/symbols')
        ]);
        
        if (infoRes.ok) {
          const info = await infoRes.json();
          setMlInfo(info);
        }
        
        if (symbolsRes.ok) {
          const symbolList = await symbolsRes.json();
          setSymbols(symbolList);
          if (symbolList.length > 0) {
            setSelectedSymbol(symbolList[0]);
          }
        }
      } catch (err) {
        console.error('Failed to fetch ML info:', err);
      }
    };

    fetchMLInfo();
  }, []);

  // Fetch prediction for selected symbol
  useEffect(() => {
    if (selectedSymbol) {
      fetchPrediction(selectedSymbol);
    }
  }, [selectedSymbol]);

  const fetchPrediction = async (symbol: string) => {
    setLoading(true);
    setError(null);
    
    try {
      const response = await fetch(`/api/ml/predict/${symbol}`);
      
      if (!response.ok) {
        throw new Error(`Failed to fetch prediction: ${response.statusText}`);
      }
      
      const prediction = await response.json();
      setPredictions([prediction]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch prediction');
      setPredictions([]);
    } finally {
      setLoading(false);
    }
  };

  const formatPercent = (value: number) => `${(value * 100).toFixed(1)}%`;
  const formatDate = (dateStr: string) => {
    try {
      return new Date(dateStr).toLocaleDateString();
    } catch {
      return dateStr;
    }
  };

  if (!mlInfo || mlInfo.status === 'unavailable') {
    return (
      <div className="rounded-lg bg-gray-50 p-6">
        <div className="flex items-center">
          <SparklesIcon className="h-5 w-5 text-gray-400 mr-2" />
          <h3 className="text-lg font-semibold text-gray-900">ML Predictions</h3>
        </div>
        <p className="mt-2 text-sm text-gray-500">
          ML service is not available. Please ensure the backend is running with DuckDB support.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-lg bg-white p-6 shadow-lg">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center">
          <SparklesIcon className="h-5 w-5 text-blue-500 mr-2" />
          <h3 className="text-lg font-semibold text-gray-900">ML Predictions</h3>
        </div>
        <div className="text-xs text-gray-500">
          {mlInfo.model_type} • {mlInfo.available_symbols} symbols
        </div>
      </div>

      {/* Symbol Selector */}
      <div className="mb-4">
        <label htmlFor="symbol-select" className="block text-sm font-medium text-gray-700 mb-2">
          Select Symbol
        </label>
        <select
          id="symbol-select"
          value={selectedSymbol}
          onChange={(e) => setSelectedSymbol(e.target.value)}
          className="block w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
        >
          {symbols.map((symbol) => (
            <option key={symbol} value={symbol}>
              {symbol}
            </option>
          ))}
        </select>
      </div>

      {/* Predictions Display */}
      {loading && (
        <div className="flex items-center justify-center py-8">
          <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-blue-500"></div>
          <span className="ml-2 text-sm text-gray-500">Loading prediction...</span>
        </div>
      )}

      {error && (
        <div className="rounded-md bg-red-50 p-4">
          <p className="text-sm text-red-700">{error}</p>
        </div>
      )}

      {!loading && !error && predictions.length > 0 && (
        <div className="space-y-4">
          {predictions.map((prediction, index) => (
            <div key={index} className="border rounded-lg p-4">
              <div className="flex items-center justify-between mb-3">
                <h4 className="text-lg font-semibold text-gray-900">{prediction.symbol}</h4>
                <div className="flex items-center space-x-2">
                  <span className={`px-2 py-1 rounded-full text-xs font-medium ${
                    prediction.confidence === 'high' ? 'bg-green-100 text-green-800' :
                    prediction.confidence === 'medium' ? 'bg-yellow-100 text-yellow-800' :
                    'bg-gray-100 text-gray-800'
                  }`}>
                    {prediction.confidence} confidence
                  </span>
                  <span className="text-xs text-gray-500">{prediction.model_type}</span>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {/* Expected Move */}
                <div className="text-center">
                  <div className="text-2xl font-bold text-blue-600">
                    {formatPercent(prediction.em_baseline)}
                  </div>
                  <div className="text-sm text-gray-500">Expected Move</div>
                </div>

                {/* 68% Band */}
                <div className="text-center">
                  <div className="text-lg font-semibold text-gray-900">
                    {formatPercent(prediction.band68_low)} - {formatPercent(prediction.band68_high)}
                  </div>
                  <div className="text-sm text-gray-500">68% Band (1σ)</div>
                </div>

                {/* 95% Band */}
                <div className="text-center">
                  <div className="text-lg font-semibold text-gray-900">
                    {formatPercent(prediction.band95_low)} - {formatPercent(prediction.band95_high)}
                  </div>
                  <div className="text-sm text-gray-500">95% Band (2σ)</div>
                </div>
              </div>

              {/* Additional Info */}
              <div className="mt-4 pt-4 border-t border-gray-200">
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <span className="text-gray-500">Prediction Date:</span>
                    <span className="ml-2 font-medium">{formatDate(prediction.prediction_date)}</span>
                  </div>
                  {prediction.earnings_date && (
                    <div>
                      <span className="text-gray-500">Earnings Date:</span>
                      <span className="ml-2 font-medium">{formatDate(prediction.earnings_date)}</span>
                    </div>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Model Performance Info */}
      {mlInfo.performance && (
        <div className="mt-6 pt-4 border-t border-gray-200">
          <h4 className="text-sm font-medium text-gray-900 mb-2">Model Performance</h4>
          <div className="grid grid-cols-3 gap-4 text-sm">
            {mlInfo.performance.mae && (
              <div>
                <span className="text-gray-500">MAE:</span>
                <span className="ml-1 font-medium">{(mlInfo.performance.mae * 100).toFixed(1)}%</span>
              </div>
            )}
            {mlInfo.performance.rmse && (
              <div>
                <span className="text-gray-500">RMSE:</span>
                <span className="ml-1 font-medium">{(mlInfo.performance.rmse * 100).toFixed(1)}%</span>
              </div>
            )}
            {mlInfo.performance.r2 && (
              <div>
                <span className="text-gray-500">R²:</span>
                <span className="ml-1 font-medium">{mlInfo.performance.r2.toFixed(3)}</span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
