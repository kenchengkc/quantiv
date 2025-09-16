import { Suspense } from 'react'
import { WeeklyEarnings } from '../components/WeeklyEarnings'

export default function Home() {
  return (
    <main className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900">
      <div className="container mx-auto px-4 py-8">
        <div className="text-center mb-12">
          <h1 className="text-4xl md:text-6xl font-bold text-white mb-4">
            Quantiv
          </h1>
          <p className="text-xl text-slate-300 max-w-2xl mx-auto">
            Weekly expected moves for upcoming earnings
          </p>
        </div>

        <Suspense fallback={
          <div className="flex justify-center items-center py-12">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white"></div>
          </div>
        }>
          <WeeklyEarnings />
        </Suspense>
      </div>
    </main>
  )
}
