/**
 * /api/health - Health check endpoint
 * Returns system status, cache stats, and Redis connectivity
 */

export const dynamic = 'force-dynamic';
import { NextResponse } from 'next/server';
import { getAllCacheStats } from '@/lib/cache/lru';
import { checkRedisHealth } from '@/lib/cache/redis';

/**
 * GET /api/health
 */
export async function GET() {
  const startTime = Date.now();
  
  try {
    // Check Redis health
    const redisHealth = await checkRedisHealth();
    
    // Get L1 cache statistics
    const cacheStats = getAllCacheStats();
    const l1Stats = Object.fromEntries(
      Object.entries(cacheStats).map(([name, stats]) => [
        name,
        {
          size: stats.size,
          hitRate: Math.round(stats.hitRate * 100) / 100
        }
      ])
    );
    
    // Determine overall system status
    let status: 'healthy' | 'degraded' | 'unhealthy';
    
    if (redisHealth.connected) {
      status = 'healthy';
    } else if (redisHealth.error && redisHealth.error.includes('CRITICAL')) {
      // If Redis has critical errors, mark as unhealthy
      status = 'unhealthy';
    } else {
      // System can still function without Redis (L1 cache only)
      status = 'degraded';
    }
    
    const healthData = {
      status,
      timestamp: new Date().toISOString(),
      services: {
        redis: redisHealth,
        cache: {
          l1Stats
        }
      },
      version: process.env.npm_package_version || '1.0.0'
    };
    
    const processingTime = Date.now() - startTime;
    
    // Add headers
    const headers = new Headers({
      'Content-Type': 'application/json',
      'Cache-Control': 'no-cache, no-store, must-revalidate',
      'X-Processing-Time': `${processingTime}ms`,
      'X-Health-Status': status
    });
    
    return NextResponse.json(healthData, { 
      headers,
      status: status === 'unhealthy' ? 503 : 200
    });
    
  } catch (error) {
    console.error('[API] /api/health error:', error);
    
    const errorData = {
      status: 'unhealthy' as const,
      timestamp: new Date().toISOString(),
      services: {
        redis: {
          connected: false,
          error: error instanceof Error ? error.message : 'Unknown error'
        },
        cache: {
          l1Stats: {}
        }
      }
    };
    
    return NextResponse.json(errorData, { status: 503 });
  }
}

/**
 * OPTIONS /api/health - CORS preflight
 */
export async function OPTIONS() {
  return new NextResponse(null, {
    status: 200,
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    },
  });
}
