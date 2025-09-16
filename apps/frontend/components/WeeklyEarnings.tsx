'use client'

import { useState, useEffect } from 'react'
import { CalendarIcon, ClockIcon, ChartBarIcon } from '@heroicons/react/24/outline'

interface EarningsEvent {
  act_symbol: string
  earnings_date: string
  t1: string
  expiry: string | null
  spot_ref: number | null
  atm_strike: number | null
  mid_call: number | null
  mid_put: number | null
  em_abs: number | null
  em_pct: number | null
  when: string
  call_bid: number | null
  call_ask: number | null
  put_bid: number | null
  put_ask: number | null
}

interface WeeklyData {
  window: {
    start: string
    end: string
    generated_at: string
  }
  events: EarningsEvent[]
  summary: {
    total_events: number
    avg_em_pct: number
  }
}

export function WeeklyEarnings() {
  const [data, setData] = useState<WeeklyData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    async function fetchWeeklyData() {
      try {
        const response = await fetch('/weekly.json')
        if (!response.ok) {
          throw new Error('Failed to fetch weekly data')
        }
        const weeklyData = await response.json()
        setData(weeklyData)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error')
      } finally {
        setLoading(false)
      }
    }

    fetchWeeklyData()
  }, [])

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
    const date = new Date(dateStr)
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
          {data.summary.total_events} earnings events • Avg EM: {formatPercent(data.summary.avg_em_pct)}
        </p>
        <p className="text-sm text-slate-400 mt-1">
          Last updated: {new Date(data.window.generated_at).toLocaleString()}
        </p>
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
                <div key={`${event.act_symbol}-${event.earnings_date}`} 
                     className="bg-slate-900/50 border border-slate-600 rounded-lg p-4">
                  
                  {/* Symbol and timing */}
                  <div className="flex justify-between items-start mb-3">
                    <h4 className="text-xl font-bold text-white">{event.act_symbol}</h4>
                    <div className="text-xs text-slate-400 flex items-center">
                      <ClockIcon className="h-3 w-3 mr-1" />
                      {event.when || 'TBD'}
                    </div>
                  </div>

                  {/* Expected Move */}
                  <div className="space-y-2 mb-3">
                    <div className="flex justify-between">
                      <span className="text-slate-300">Expected Move:</span>
                      <span className="text-green-400 font-semibold">
                        {formatPercent(event.em_pct)}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-300">Dollar Move:</span>
                      <span className="text-green-400 font-semibold">
                        {formatDollar(event.em_abs)}
                      </span>
                    </div>
                  </div>

                  {/* Options details */}
                  <div className="text-xs text-slate-400 space-y-1">
                    <div className="flex justify-between">
                      <span>Spot Ref:</span>
                      <span>{formatDollar(event.spot_ref)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>ATM Strike:</span>
                      <span>{formatDollar(event.atm_strike)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Expiry:</span>
                      <span>{event.expiry ? new Date(event.expiry).toLocaleDateString() : 'N/A'}</span>
                    </div>
                  </div>

                  {/* Straddle components */}
                  {(event.mid_call || event.mid_put) && (
                    <div className="mt-3 pt-3 border-t border-slate-600">
                      <div className="text-xs text-slate-400 space-y-1">
                        <div className="flex justify-between">
                          <span>Call Mid:</span>
                          <span>{formatDollar(event.mid_call)}</span>
                        </div>
                        <div className="flex justify-between">
                          <span>Put Mid:</span>
                          <span>{formatDollar(event.mid_put)}</span>
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
            Expected Move = ATM Call Mid + ATM Put Mid (straddle price)
          </p>
          <p>
            • Uses options expiring after earnings date
          </p>
          <p>
            • Based on implied volatility from T-1 (last trading day before earnings)
          </p>
          <p>
            • Represents market's expectation of stock movement magnitude
          </p>
        </div>
      </div>
    </div>
  )
}
