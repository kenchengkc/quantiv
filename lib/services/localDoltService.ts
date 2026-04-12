/**
 * Stub for localDoltService — the original SQLite-backed service was removed.
 * This stub satisfies type-checking for services that still reference it.
 * All methods return empty/default values.
 */

interface IVStats {
  rank: number;
  percentile: number;
  current: number;
}

interface IVHistoryEntry {
  date: string;
  iv: number;
}

class LocalDoltService {
  async getIVStats(_symbol: string): Promise<IVStats | null> {
    return null;
  }

  async getIVHistory(_symbol: string, _days: number): Promise<IVHistoryEntry[]> {
    return [];
  }

  async getAvailableSymbols(): Promise<string[]> {
    return [];
  }
}

export const localDoltService = new LocalDoltService();
export default localDoltService;
