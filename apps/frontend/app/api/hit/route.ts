/**
 * /api/hit - Visitor counter endpoint
 * Increments daily visitor count and returns current count
 */

export const dynamic = 'force-dynamic';
import { NextRequest, NextResponse } from 'next/server';
import { createApiResponse } from '@/lib/schemas';
import { QuantivCache } from '@/lib/cache/redis';

/**
 * Rate limiting helper
 * Simple in-memory rate limiter to prevent abuse
 */
class RateLimiter {
  private static requests = new Map<string, { count: number; resetTime: number }>();
  private static readonly WINDOW_MS = 60 * 1000; // 1 minute window
  private static readonly MAX_REQUESTS = 10; // Max 10 requests per minute per IP
  
  static isAllowed(ip: string): boolean {
    const now = Date.now();
    const key = ip;
    
    const existing = this.requests.get(key);
    
    if (!existing || now > existing.resetTime) {
      // New window
      this.requests.set(key, {
        count: 1,
        resetTime: now + this.WINDOW_MS
      });
      return true;
    }
    
    if (existing.count >= this.MAX_REQUESTS) {
      return false;
    }
    
    existing.count++;
    return true;
  }
  
  static getRemainingRequests(ip: string): number {
    const existing = this.requests.get(ip);
    if (!existing || Date.now() > existing.resetTime) {
      return this.MAX_REQUESTS;
    }
    return Math.max(0, this.MAX_REQUESTS - existing.count);
  }
}

/**
 * GET /api/hit - Increment visitor count
 */
export async function GET(request: NextRequest) {
  const startTime = Date.now();
  
  try {
    // Get client IP for rate limiting
    const forwarded = request.headers.get('x-forwarded-for');
    const ip = forwarded ? forwarded.split(',')[0].trim() : 
               request.headers.get('x-real-ip') || 
               'unknown';
    
    // Apply rate limiting
    if (!RateLimiter.isAllowed(ip)) {
      const remaining = RateLimiter.getRemainingRequests(ip);
      
      return NextResponse.json(
        createApiResponse(
          undefined, 
          'Rate limit exceeded', 
          'Too many requests from this IP address',
          'Please wait before making more requests'
        ),
        { 
          status: 429,
          headers: {
            'X-RateLimit-Remaining': remaining.toString(),
            'X-RateLimit-Reset': '60',
            'Retry-After': '60'
          }
        }
      );
    }
    
    // Increment visitor count
    const count = await QuantivCache.incrementVisitorCount();
    
    const response = createApiResponse({ count });
    const processingTime = Date.now() - startTime;
    
    // Add headers
    const headers = new Headers({
      'Content-Type': 'application/json',
      'Cache-Control': 'no-cache, no-store, must-revalidate',
      'X-Processing-Time': `${processingTime}ms`,
      'X-Visitor-Count': count.toString(),
      'X-Client-IP': ip,
      'X-RateLimit-Remaining': RateLimiter.getRemainingRequests(ip).toString()
    });
    
    return NextResponse.json(response, { headers });
    
  } catch (error) {
    console.error('[API] /api/hit error:', error);
    
    const errorResponse = createApiResponse(
      undefined,
      'Internal server error',
      error instanceof Error ? error.message : 'Unknown error',
      'Visitor count may not have been updated'
    );
    
    return NextResponse.json(errorResponse, { status: 500 });
  }
}

/**
 * POST /api/hit - Alternative method for incrementing
 */
export async function POST(request: NextRequest) {
  // Same logic as GET - some clients prefer POST for state-changing operations
  return GET(request);
}

/**
 * OPTIONS /api/hit - CORS preflight
 */
export async function OPTIONS() {
  return new NextResponse(null, {
    status: 200,
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    },
  });
}
