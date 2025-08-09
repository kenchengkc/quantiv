/**
 * Zod schemas for API validation and type safety
 * Used across all API routes for request/response validation
 */

import { z } from 'zod';

/**
 * Common validation schemas
 */
export const SymbolSchema = z.string()
  .min(1, 'Symbol is required')
  .max(10, 'Symbol too long')
  .regex(/^[A-Za-z0-9.-]+$/, 'Invalid symbol format')
  .transform(s => s.toUpperCase());

export const ExpirySchema = z.string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, 'Expiry must be in YYYY-MM-DD format')
  .refine(date => {
    const parsed = new Date(date);
    const now = new Date();
    return parsed > now;
  }, 'Expiry must be in the future');

export const DateSchema = z.string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, 'Date must be in YYYY-MM-DD format');

/**
 * Options chain schemas
 */
export const OptionSchema = z.object({
  strike: z.number().positive('Strike must be positive'),
  mid: z.number().nonnegative('Mid price cannot be negative'),
  bid: z.number().nonnegative('Bid price cannot be negative'),
  ask: z.number().nonnegative('Ask price cannot be negative'),
  iv: z.number().positive('IV must be positive').optional(),
  delta: z.number().optional(),
  gamma: z.number().optional(),
  theta: z.number().optional(),
  vega: z.number().optional(),
  rho: z.number().optional(),
  volume: z.number().nonnegative().optional(),
  openInterest: z.number().nonnegative().optional(),
  lastPrice: z.number().nonnegative().optional(),
  change: z.number().optional(),
  changePct: z.number().optional()
});

export const OptionsChainSchema = z.object({
  symbol: SymbolSchema,
  spot: z.number().positive('Spot price must be positive'),
  expiryDate: ExpirySchema,
  daysToExpiry: z.number().positive('Days to expiry must be positive'),
  strikes: z.array(z.number().positive()).min(1, 'At least one strike required'),
  calls: z.array(OptionSchema).min(1, 'At least one call required'),
  puts: z.array(OptionSchema).min(1, 'At least one put required'),
  timestamp: z.string().datetime().optional(),
  source: z.string().optional()
});

/**
 * Expected move schemas
 */
export const ExpectedMoveSchema = z.object({
  straddle: z.object({
    abs: z.number().nonnegative('Straddle move cannot be negative'),
    pct: z.number().nonnegative('Straddle percentage cannot be negative')
  }),
  iv: z.object({
    abs: z.number().nonnegative('IV move cannot be negative'),
    pct: z.number().nonnegative('IV percentage cannot be negative')
  }),
  bands: z.object({
    oneSigma: z.object({
      upper: z.number(),
      lower: z.number()
    }),
    twoSigma: z.object({
      upper: z.number(),
      lower: z.number()
    })
  }),
  confidence: z.object({
    straddle: z.enum(['high', 'medium', 'low']),
    iv: z.enum(['high', 'medium', 'low'])
  })
});

/**
 * IV statistics schemas
 */
export const IVStatsSchema = z.object({
  rank: z.number().min(0).max(1, 'IV rank must be between 0 and 1'),
  percentile: z.number().min(0).max(100, 'Percentile must be between 0 and 100'),
  current: z.number().positive('Current IV must be positive'),
  min: z.number().positive('Min IV must be positive'),
  max: z.number().positive('Max IV must be positive'),
  mean: z.number().positive('Mean IV must be positive'),
  median: z.number().positive('Median IV must be positive'),
  stdDev: z.number().nonnegative('Standard deviation cannot be negative'),
  daysInSample: z.number().positive('Days in sample must be positive')
});

/**
 * Earnings schemas
 */
export const EarningsEventSchema = z.object({
  date: DateSchema,
  confidence: z.enum(['confirmed', 'estimated']),
  timing: z.enum(['bmo', 'amc', 'unknown']).optional(), // Before market open, after market close
  estimate: z.object({
    eps: z.number().optional(),
    revenue: z.number().optional()
  }).optional()
});

export const RealizedMoveSchema = z.object({
  date: DateSchema,
  realizedMovePct: z.number(),
  priceChange: z.number(),
  priceBefore: z.number().positive(),
  priceAfter: z.number().positive(),
  volume: z.number().nonnegative().optional()
});

export const EarningsDataSchema = z.object({
  symbol: SymbolSchema,
  next: EarningsEventSchema.optional(),
  last: z.array(RealizedMoveSchema).max(8, 'Maximum 8 historical earnings'),
  timestamp: z.string().datetime().optional()
});

/**
 * API request schemas
 */
export const OptionsRequestSchema = z.object({
  symbol: SymbolSchema,
  expiry: ExpirySchema.optional()
});

export const ExpectedMoveRequestSchema = z.object({
  symbol: SymbolSchema,
  expiry: ExpirySchema.optional()
});

export const EarningsRequestSchema = z.object({
  symbol: SymbolSchema
});

export const TopMoversRequestSchema = z.object({
  date: DateSchema.optional(),
  limit: z.number().min(1).max(50).default(10)
});

/**
 * API response schemas
 */
export const OptionsResponseSchema = z.object({
  success: z.boolean(),
  data: z.object({
    spot: z.number().positive(),
    expiryUsed: ExpirySchema,
    atm: z.object({
      strike: z.number().positive(),
      callMid: z.number().nonnegative(),
      putMid: z.number().nonnegative(),
      iv: z.number().positive(),
      T: z.number().positive()
    }),
    rows: z.array(z.object({
      strike: z.number().positive(),
      call: OptionSchema,
      put: OptionSchema
    }))
  }).optional(),
  error: z.string().optional(),
  timestamp: z.string().datetime()
});

export const ExpectedMoveResponseSchema = z.object({
  success: z.boolean(),
  data: z.object({
    em: ExpectedMoveSchema,
    ivRank: IVStatsSchema
  }).optional(),
  error: z.string().optional(),
  timestamp: z.string().datetime()
});

export const EarningsResponseSchema = z.object({
  success: z.boolean(),
  data: EarningsDataSchema.optional(),
  error: z.string().optional(),
  timestamp: z.string().datetime()
});

export const HitResponseSchema = z.object({
  success: z.boolean(),
  data: z.object({
    count: z.number().nonnegative()
  }).optional(),
  error: z.string().optional(),
  timestamp: z.string().datetime()
});

export const TopMoversResponseSchema = z.object({
  success: z.boolean(),
  data: z.array(z.object({
    symbol: SymbolSchema,
    expectedMovePct: z.number().nonnegative(),
    spot: z.number().positive().optional(),
    volume: z.number().nonnegative().optional()
  })).optional(),
  error: z.string().optional(),
  timestamp: z.string().datetime()
});

/**
 * Error response schema
 */
export const ErrorResponseSchema = z.object({
  success: z.literal(false),
  error: z.string(),
  detail: z.string().optional(),
  hint: z.string().optional(),
  timestamp: z.string().datetime()
});

/**
 * Health check schema
 */
export const HealthResponseSchema = z.object({
  status: z.enum(['healthy', 'degraded', 'unhealthy']),
  timestamp: z.string().datetime(),
  services: z.object({
    redis: z.object({
      connected: z.boolean(),
      latency: z.number().optional(),
      error: z.string().optional()
    }),
    cache: z.object({
      l1Stats: z.record(z.object({
        size: z.number(),
        hitRate: z.number()
      }))
    })
  }),
  version: z.string().optional()
});

/**
 * Utility functions for schema validation
 */
export function validateRequest<T>(schema: z.ZodSchema<T>, data: unknown): {
  success: boolean;
  data?: T;
  error?: string;
  details?: string[];
} {
  try {
    const result = schema.parse(data);
    return { success: true, data: result };
  } catch (error) {
    if (error instanceof z.ZodError) {
      return {
        success: false,
        error: 'Validation failed',
        details: error.errors.map(e => `${e.path.join('.')}: ${e.message}`)
      };
    }
    
    return {
      success: false,
      error: 'Unknown validation error',
      details: [error instanceof Error ? error.message : 'Unknown error']
    };
  }
}

/**
 * Create standardized API response
 */
export function createApiResponse<T>(
  data?: T,
  error?: string,
  detail?: string,
  hint?: string
): {
  success: boolean;
  data?: T;
  error?: string;
  detail?: string;
  hint?: string;
  timestamp: string;
} {
  return {
    success: !error,
    ...(data && { data }),
    ...(error && { error }),
    ...(detail && { detail }),
    ...(hint && { hint }),
    timestamp: new Date().toISOString()
  };
}

/**
 * Type exports for use in API routes
 */
export type OptionsRequest = z.infer<typeof OptionsRequestSchema>;
export type ExpectedMoveRequest = z.infer<typeof ExpectedMoveRequestSchema>;
export type EarningsRequest = z.infer<typeof EarningsRequestSchema>;
export type TopMoversRequest = z.infer<typeof TopMoversRequestSchema>;

export type OptionsResponse = z.infer<typeof OptionsResponseSchema>;
export type ExpectedMoveResponse = z.infer<typeof ExpectedMoveResponseSchema>;
export type EarningsResponse = z.infer<typeof EarningsResponseSchema>;
export type HitResponse = z.infer<typeof HitResponseSchema>;
export type TopMoversResponse = z.infer<typeof TopMoversResponseSchema>;
export type ErrorResponse = z.infer<typeof ErrorResponseSchema>;
export type HealthResponse = z.infer<typeof HealthResponseSchema>;
