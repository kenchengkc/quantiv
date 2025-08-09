import '@testing-library/jest-dom';
import { vi, afterEach } from 'vitest';

// Mock environment variables for tests
process.env.NODE_ENV = 'test';
process.env.REDIS_URL = 'redis://localhost:6379';
process.env.REDIS_TOKEN = 'test-token';

// Mock fetch for API tests
global.fetch = vi.fn();

// Mock Redis for tests
vi.mock('@upstash/redis', () => ({
  Redis: vi.fn().mockImplementation(() => ({
    get: vi.fn(),
    set: vi.fn(),
    incr: vi.fn(),
    expire: vi.fn(),
    zadd: vi.fn(),
    zrange: vi.fn(),
  })),
}));

// Clean up after each test
afterEach(() => {
  vi.clearAllMocks();
});
