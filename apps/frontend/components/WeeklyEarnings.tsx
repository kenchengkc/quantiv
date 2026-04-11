'use client'

import { useState, useEffect } from 'react'
import { CalendarIcon, ClockIcon, ChartBarIcon } from '@heroicons/react/24/outline'

interface EarningsEvent {
  ticker: string
  earnings_date: string
  timing: string
  fiscal_q: string
  as_of_date: string
  lead_time_days: number
  expiry_date: string
  days_to_expiry: number
  
  // Core EM metrics
  em_straddle_pct: number
  em_iv_pct: number
  em_straddle_abs: number
  
  // Options data
  spot_price: number
  atm_strike: number
  atm_iv: number
  call_iv: number
  put_iv: number
  
  // Market microstructure
  skew_atm: number
  term_slope: number
  total_vega: number
  avg_bid_ask_spread: number
  
  // Metadata
  method: string
  confidence: string
  
  // ML-enhanced forecasts (optional)
  ml_forecast?: {
    em_ml: number
    em_math: number
    correction_factor: number
    p10: number
    p50: number
    p90: number
    combined_confidence: number
    method: string
  }
}

interface WeeklyData {
  metadata: {
    version: string
    generated_at: string
    as_of_date: string
    method: string
  }
  window: {
    start: string
    end: string
  }
  events: EarningsEvent[]
  summary: {
    total_events: number
    avg_em_straddle_pct: number
    avg_em_iv_pct: number
    avg_lead_time_days: number
  }
}

export function WeeklyEarnings() {
  const [data, setData] = useState<WeeklyData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const mlEnabled = true
  const [mlLoading, setMlLoading] = useState(false)

  // Fetch ML forecasts for events
  async function fetchMLForecasts(events: EarningsEvent[]) {
    if (!mlEnabled) return events
    
    setMlLoading(true)
    const eventsWithML = await Promise.all(
      events.map(async (event) => {
        // Only fetch for future events
        if (event.lead_time_days < 0) return event
        
        try {
          const response = await fetch(
            `/api/backend/em/ml-forecast?symbol=${event.ticker}&earnings_date=${event.earnings_date}`
          )
          if (response.ok) {
            const mlForecast = await response.json()
            return { ...event, ml_forecast: mlForecast }
          }
        } catch (err) {
          // Silently fail for individual ML forecasts
          console.warn(`ML forecast failed for ${event.ticker}:`, err)
        }
        return event
      })
    )
    setMlLoading(false)
    return eventsWithML
  }

  useEffect(() => {
    async function fetchWeeklyData() {
      try {
        const response = await fetch('/api/weekly')
        if (!response.ok) {
          throw new Error('Failed to fetch weekly data')
        }
        const weeklyData = await response.json()
        
        // Fetch ML forecasts if enabled
        if (mlEnabled && weeklyData.events) {
          weeklyData.events = await fetchMLForecasts(weeklyData.events)
        }
        
        setData(weeklyData)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error')
      } finally {
        setLoading(false)
      }
    }

    fetchWeeklyData()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mlEnabled])

  if (loading) {
    return (
      <div className="flex justify-center items-center py-12">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white"></div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-red-900/20 border border-red-500 rounded-lg p-6 text-center">
        <p className="text-red-400">Error loading weekly data: {error}</p>
      </div>
    )
  }

  if (!data || data.events.length === 0) {
    return (
      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-6 text-center">
        <p className="text-slate-400">No earnings events found for this week</p>
      </div>
    )
  }

  // Group events by date
  const eventsByDate = data.events.reduce((acc, event) => {
    const date = event.earnings_date
    if (!acc[date]) {
      acc[date] = []
    }
    acc[date].push(event)
    return acc
  }, {} as Record<string, EarningsEvent[]>)

  const formatDate = (dateStr: string) => {
    // Parse as local date to avoid timezone conversion issues
    const [year, month, day] = dateStr.split('-').map(Number)
    const date = new Date(year, month - 1, day)
    return date.toLocaleDateString('en-US', { 
      weekday: 'long', 
      month: 'short', 
      day: 'numeric' 
    })
  }

  const formatPercent = (value: number | null) => {
    if (value === null) return 'N/A'
    return `${(value * 100).toFixed(1)}%`
  }

  const formatDollar = (value: number | null) => {
    if (value === null) return 'N/A'
    return `$${value.toFixed(2)}`
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="text-center">
        <h2 className="text-2xl font-bold text-white mb-2">
          Week of {formatDate(data.window.start)} - {formatDate(data.window.end)}
        </h2>
        <p className="text-slate-300">
          {data.summary.total_events} earnings events • Avg Straddle EM: {formatPercent(data.summary.avg_em_straddle_pct)} • Avg IV EM: {formatPercent(data.summary.avg_em_iv_pct)}
        </p>
        <p className="text-sm text-slate-400 mt-1">
          Last updated: {new Date(data.metadata.generated_at).toLocaleString()} • Method: {data.metadata.method}
        </p>
        {mlLoading && (
          <p className="text-xs text-blue-400 mt-2">
            Loading ML forecasts...
          </p>
        )}
        {mlEnabled && !mlLoading && (
          <p className="text-xs text-blue-300 mt-2">
            ✨ ML-enhanced forecasts enabled
          </p>
        )}
      </div>

      {/* Events by day */}
      <div className="space-y-6">
        {Object.entries(eventsByDate).map(([date, events]) => (
          <div key={date} className="bg-slate-800/50 border border-slate-700 rounded-lg p-6">
            <h3 className="text-lg font-semibold text-white mb-4 flex items-center">
              <CalendarIcon className="h-5 w-5 mr-2" />
              {formatDate(date)}
            </h3>
            
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {events.map((event) => (
                <div key={`${event.ticker}-${event.earnings_date}`} 
                     className="bg-slate-900/50 border border-slate-600 rounded-lg p-4">
                  
                  {/* Symbol and timing */}
                  <div className="flex justify-between items-start mb-3">
                    <h4 className="text-xl font-bold text-white">{event.ticker}</h4>
                    <div className="text-xs text-slate-400 flex items-center">
                      <ClockIcon className="h-3 w-3 mr-1" />
                      {event.timing === 'after_market_close' ? 'After Close' : 
                       event.timing === 'before_market_open' ? 'Before Open' :
                       event.timing === 'during_market_hours' ? 'During Hours' : 'TBD'}
                    </div>
                  </div>
                  
                  {/* Lead time badge */}
                  <div className="mb-3">
                    <span className="inline-block bg-blue-900/50 text-blue-300 text-xs px-2 py-1 rounded">
                      T{event.lead_time_days >= 0 ? '+' : ''}{event.lead_time_days} • {event.fiscal_q}
                    </span>
                    {event.lead_time_days < 0 && (
                      <span className="ml-2 inline-block bg-amber-900/50 text-amber-300 text-xs px-2 py-1 rounded">
                        Historical
                      </span>
                    )}
                  </div>

                  {/* Expected Move */}
                  <div className="space-y-2 mb-3">
                    {/* ML Forecast (if available) */}
                    {event.ml_forecast && (
                      <div className="mb-3 p-3 bg-blue-900/20 border border-blue-700/50 rounded">
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-xs text-blue-300 font-semibold">ML-Enhanced</span>
                          <span className="text-xs px-2 py-0.5 bg-blue-700/50 text-blue-200 rounded">
                            {Math.round(event.ml_forecast.combined_confidence * 100)}% conf
                          </span>
                        </div>
                        <div className="flex justify-between items-center">
                          <div className="text-xs text-slate-400">
                            Math: {formatPercent(event.ml_forecast.em_math)}
                          </div>
                          <div className="text-xs text-slate-400">→</div>
                          <div className="text-sm font-bold text-blue-300">
                            ML: {formatPercent(event.ml_forecast.em_ml)}
                          </div>
                        </div>
                        {/* Confidence band visualization */}
                        <div className="mt-2 h-1.5 bg-slate-700 rounded-full relative overflow-hidden">
                          <div 
                            className="absolute h-full bg-blue-500/30 rounded-full"
                            style={{
                              left: `${Math.max(0, (event.ml_forecast.p10 / (event.ml_forecast.p90 * 1.2)) * 100)}%`,
                              width: `${Math.min(100, ((event.ml_forecast.p90 - event.ml_forecast.p10) / (event.ml_forecast.p90 * 1.2)) * 100)}%`
                            }}
                          />
                          <div 
                            className="absolute h-full w-0.5 bg-blue-400"
                            style={{
                              left: `${(event.ml_forecast.p50 / (event.ml_forecast.p90 * 1.2)) * 100}%`
                            }}
                          />
                        </div>
                        <div className="flex justify-between text-xs text-slate-500 mt-1">
                          <span>{formatPercent(event.ml_forecast.p10)}</span>
                          <span>{formatPercent(event.ml_forecast.p90)}</span>
                        </div>
                      </div>
                    )}
                    
                    <div className="flex justify-between">
                      <span className="text-slate-300">Straddle EM:</span>
                      <span className="text-green-400 font-semibold">
                        {formatPercent(event.em_straddle_pct)}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-300">IV EM:</span>
                      <span className="text-blue-400 font-semibold">
                        {formatPercent(event.em_iv_pct)}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-300">Dollar Move:</span>
                      <span className="text-green-400 font-semibold">
                        {formatDollar(event.em_straddle_abs)}
                      </span>
                    </div>
                  </div>

                  {/* Options details */}
                  <div className="text-xs text-slate-400 space-y-1">
                    <div className="flex justify-between">
                      <span>Spot Price:</span>
                      <span>{formatDollar(event.spot_price)} <span className="text-xs text-slate-500">(est.)</span></span>
                    </div>
                    <div className="flex justify-between">
                      <span>ATM Strike:</span>
                      <span>{formatDollar(event.atm_strike)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Expiry:</span>
                      <span>{event.expiry_date ? new Date(event.expiry_date).toLocaleDateString() : 'N/A'}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>ATM IV:</span>
                      <span>{formatPercent(event.atm_iv)}</span>
                    </div>
                  </div>

                  {/* Advanced metrics */}
                  {(event.skew_atm !== undefined || event.total_vega !== undefined) && (
                    <div className="mt-3 pt-3 border-t border-slate-600">
                      <div className="text-xs text-slate-400 space-y-1">
                        <div className="flex justify-between">
                          <span>Skew (ATM):</span>
                          <span>{event.skew_atm ? `${(event.skew_atm * 100).toFixed(1)}vol` : 'N/A'}</span>
                        </div>
                        <div className="flex justify-between">
                          <span>Total Vega:</span>
                          <span>{event.total_vega ? `$${(event.total_vega * 1000).toFixed(0)}` : 'N/A'}</span>
                        </div>
                        <div className="flex justify-between">
                          <span>Confidence:</span>
                          <span className="capitalize">{event.confidence}</span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* How it works */}
      <div className="bg-slate-800/30 border border-slate-700 rounded-lg p-6">
        <h3 className="text-lg font-semibold text-white mb-3 flex items-center">
          <ChartBarIcon className="h-5 w-5 mr-2" />
          How Expected Move is Calculated
        </h3>
        <div className="text-sm text-slate-300 space-y-2">
          <p>
            <strong>Straddle EM:</strong> ATM Call Mid + ATM Put Mid (market pricing)
          </p>
          <p>
            <strong>IV EM:</strong> ATM IV × √(Days to Expiry / 365) (volatility-based)
          </p>
          <p>
            <strong>Spot Price:</strong> Estimated from ATM options with delta ≈ 0.5
          </p>
          <p>
            <strong>Skew:</strong> Put IV - Call IV difference (in volatility points)
          </p>
          <p>
            <strong>Vega:</strong> Total position sensitivity to 1% IV change (×1000)
          </p>
          <p>
            • T+X: Days after earnings (historical data) • T-X: Days before earnings
          </p>
        </div>
      </div>
    </div>
  )
}
