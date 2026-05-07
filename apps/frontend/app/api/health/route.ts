/**
 * /api/health - Health check endpoint
 * Returns system status and Redis connectivity.
 */

export const dynamic = 'force-dynamic';
import { NextResponse } from 'next/server';
import { getRedis } from '@/lib/redis';

/**
 * GET /api/health
 */
export async function GET() {
  const startTime = Date.now();
  
  try {
    const redis = getRedis();
    const redisHealth: {
      connected: boolean;
      latency?: number;
      error?: string;
    } = { connected: false };

    if (!redis) {
      redisHealth.error = 'Redis not configured (missing UPSTASH_REDIS_REST_URL or UPSTASH_REDIS_REST_TOKEN)';
    } else {
      const redisStart = Date.now();
      await redis.ping();
      redisHealth.connected = true;
      redisHealth.latency = Date.now() - redisStart;
    }

    let status: 'healthy' | 'degraded';
    
    if (redisHealth.connected) {
      status = 'healthy';
    } else {
      status = 'degraded';
    }
    
    const healthData = {
      status,
      timestamp: new Date().toISOString(),
      services: {
        redis: redisHealth
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
      status: 200
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
