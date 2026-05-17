import type { Metadata } from 'next';
import WatchlistPageClient from './WatchlistPageClient';

export const metadata: Metadata = {
  title: 'Watchlist',
};

export default function WatchlistPage() {
  return <WatchlistPageClient />;
}
