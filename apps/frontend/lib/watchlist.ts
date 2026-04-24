'use client';

import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useAuth } from '@clerk/nextjs';

// Single source of truth for the user's watchlist — a React Query cache
// shared across every component that calls useWatchlist(). Mutations update
// the cache from the server response, so the ticker-page "add" button and
// the /watchlist tab stay in sync without races.

const KEY = ['watchlist'] as const;

type Payload = { symbols: string[] };

async function fetchWatchlist(): Promise<string[]> {
  const res = await fetch('/api/watchlist', { cache: 'no-store' });
  if (!res.ok) throw new Error(`GET /api/watchlist ${res.status}`);
  const json = (await res.json()) as Payload;
  return json.symbols ?? [];
}

async function postAdd(symbol: string): Promise<string[]> {
  const res = await fetch('/api/watchlist', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ symbol }),
  });
  if (!res.ok) throw new Error(`POST /api/watchlist ${res.status}`);
  const json = (await res.json()) as Payload;
  return json.symbols ?? [];
}

async function deleteRemove(symbol: string): Promise<string[]> {
  const res = await fetch(`/api/watchlist/${encodeURIComponent(symbol)}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(`DELETE /api/watchlist/${symbol} ${res.status}`);
  const json = (await res.json()) as Payload;
  return json.symbols ?? [];
}

async function putReorder(symbols: string[]): Promise<string[]> {
  const res = await fetch('/api/watchlist/reorder', {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ symbols }),
  });
  if (!res.ok) throw new Error(`PUT /api/watchlist/reorder ${res.status}`);
  const json = (await res.json()) as Payload;
  return json.symbols ?? [];
}

export function useWatchlist() {
  const { isSignedIn, isLoaded } = useAuth();
  const qc = useQueryClient();

  const query = useQuery({
    queryKey: KEY,
    queryFn: fetchWatchlist,
    // Only fire once auth state is known AND user is signed in. The API
    // returns 401 for anonymous users and we don't want failed queries.
    enabled: isLoaded && !!isSignedIn,
    staleTime: 30_000,
  });

  const add = useMutation({
    mutationFn: postAdd,
    // Optimistic: flip the button instantly. Server response replaces the
    // cache in onSuccess so ordering/dedup matches the DB.
    onMutate: async (symbol: string) => {
      await qc.cancelQueries({ queryKey: KEY });
      const prev = qc.getQueryData<string[]>(KEY) ?? [];
      if (!prev.includes(symbol)) {
        qc.setQueryData<string[]>(KEY, [...prev, symbol]);
      }
      return { prev };
    },
    onError: (_err, _sym, ctx) => {
      if (ctx?.prev) qc.setQueryData(KEY, ctx.prev);
    },
    onSuccess: (symbols) => qc.setQueryData(KEY, symbols),
  });

  const remove = useMutation({
    mutationFn: deleteRemove,
    onMutate: async (symbol: string) => {
      await qc.cancelQueries({ queryKey: KEY });
      const prev = qc.getQueryData<string[]>(KEY) ?? [];
      qc.setQueryData<string[]>(
        KEY,
        prev.filter((s) => s !== symbol),
      );
      return { prev };
    },
    onError: (_err, _sym, ctx) => {
      if (ctx?.prev) qc.setQueryData(KEY, ctx.prev);
    },
    onSuccess: (symbols) => qc.setQueryData(KEY, symbols),
  });

  const reorder = useMutation({
    mutationFn: putReorder,
    onMutate: async (symbols: string[]) => {
      await qc.cancelQueries({ queryKey: KEY });
      const prev = qc.getQueryData<string[]>(KEY) ?? [];
      qc.setQueryData<string[]>(KEY, symbols);
      return { prev };
    },
    onError: (_err, _syms, ctx) => {
      if (ctx?.prev) qc.setQueryData(KEY, ctx.prev);
    },
    onSuccess: (symbols) => qc.setQueryData(KEY, symbols),
  });

  return {
    symbols: query.data ?? [],
    isLoaded: query.isSuccess || !(isLoaded && isSignedIn),
    add: (symbol: string) => add.mutate(symbol),
    remove: (symbol: string) => remove.mutate(symbol),
    reorder: (symbols: string[]) => reorder.mutate(symbols),
  };
}
