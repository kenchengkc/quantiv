/**
 * WebSocket Management API Route
 * 
 * Provides WebSocket subscription management for real-time options data.
 * Enables subscription to minute-by-minute OHLC aggregates for options contracts.
 * 
 * Query Parameters:
 * - action: 'subscribe' | 'unsubscribe' | 'status' (required)
 * - contract: Options contract ticker (required for subscribe/unsubscribe)
 * 
 * Returns:
 * - success: boolean
 * - data: Subscription status or connection info
 * - error: Error message if any
 * - timestamp: ISO timestamp
 */

import { NextRequest, NextResponse } from 'next/server';
import { createApiResponse } from '@/lib/schemas';
import PolygonWebSocketService from '@/lib/services/polygonWebSocketService';

/**
 * GET /api/websocket?action=subscribe&contract=O:SPY251219C00650000
 * GET /api/websocket?action=status
 */
export async function GET(request: NextRequest) {
  const startTime = Date.now();

  try {
    // Parse and validate query parameters
    const url = new URL(request.url);
    const searchParams = url.searchParams;
    const action = searchParams.get('action');
    const contract = searchParams.get('contract');
    
    if (!action || !['subscribe', 'unsubscribe', 'status'].includes(action)) {
      return NextResponse.json(
        createApiResponse(undefined, 'Invalid action', 'Action must be subscribe, unsubscribe, or status'),
        { status: 400 }
      );
    }

    if ((action === 'subscribe' || action === 'unsubscribe') && !contract) {
      return NextResponse.json(
        createApiResponse(undefined, 'Missing contract parameter', 'Contract parameter is required for subscribe/unsubscribe'),
        { status: 400 }
      );
    }

    if (contract && !contract.startsWith('O:')) {
      return NextResponse.json(
        createApiResponse(undefined, 'Invalid contract format', 'Contract must be in format O:SYMBOL...'),
        { status: 400 }
      );
    }

    const wsService = PolygonWebSocketService.getInstance();
    let responseData: any;

    switch (action) {
      case 'subscribe':
        console.log(`[websocket-api] Subscribing to ${contract}`);
        const subscribed = await wsService.subscribeToContract(contract!);
        
        responseData = {
          action: 'subscribe',
          contract: contract,
          subscribed,
          message: subscribed ? 'Successfully subscribed' : 'Subscription failed',
          connectionStatus: wsService.getConnectionStatus(),
          responseTime: Date.now() - startTime
        };
        break;

      case 'unsubscribe':
        console.log(`[websocket-api] Unsubscribing from ${contract}`);
        const unsubscribed = await wsService.unsubscribeFromContract(contract!);
        
        responseData = {
          action: 'unsubscribe',
          contract: contract,
          unsubscribed,
          message: unsubscribed ? 'Successfully unsubscribed' : 'Unsubscription failed',
          connectionStatus: wsService.getConnectionStatus(),
          responseTime: Date.now() - startTime
        };
        break;

      case 'status':
        const subscriptions = wsService.getSubscriptions();
        const connectionStatus = wsService.getConnectionStatus();
        
        responseData = {
          action: 'status',
          connectionStatus,
          subscriptions: Array.from(subscriptions.entries()).map(([ticker, sub]) => ({
            contractTicker: ticker,
            subscribed: sub.subscribed,
            lastUpdate: sub.lastUpdate,
            messageCount: sub.messageCount
          })),
          totalSubscriptions: subscriptions.size,
          responseTime: Date.now() - startTime
        };
        break;
    }
      
    const response = createApiResponse(responseData);
    const processingTime = Date.now() - startTime;
      
    return NextResponse.json(response, {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache, no-store, must-revalidate', // No caching for WebSocket management
        'X-Processing-Time': `${processingTime}ms`,
        'X-Action': action,
        'X-Data-Source': 'polygon-websocket'
      }
    });
    
  } catch (error) {
    console.error('[API] /api/websocket error:', error);
    
    const errorResponse = createApiResponse(
      undefined,
      'Internal server error',
      error instanceof Error ? error.message : 'Unknown error',
      'Please try again or contact support if the issue persists'
    );
    
    return NextResponse.json(errorResponse, { status: 500 });
  }
}

/**
 * POST /api/websocket - Batch subscription management
 */
export async function POST(request: NextRequest) {
  const startTime = Date.now();

  try {
    const body = await request.json();
    const { action, contracts } = body;

    if (!action || !['subscribe', 'unsubscribe'].includes(action)) {
      return NextResponse.json(
        createApiResponse(undefined, 'Invalid action', 'Action must be subscribe or unsubscribe'),
        { status: 400 }
      );
    }

    if (!contracts || !Array.isArray(contracts) || contracts.length === 0) {
      return NextResponse.json(
        createApiResponse(undefined, 'Invalid contracts', 'Contracts must be a non-empty array'),
        { status: 400 }
      );
    }

    // Validate contract formats
    const invalidContracts = contracts.filter(c => !c.startsWith('O:'));
    if (invalidContracts.length > 0) {
      return NextResponse.json(
        createApiResponse(undefined, 'Invalid contract formats', `Invalid contracts: ${invalidContracts.join(', ')}`),
        { status: 400 }
      );
    }

    const wsService = PolygonWebSocketService.getInstance();
    const results = [];

    for (const contract of contracts) {
      try {
        let result;
        if (action === 'subscribe') {
          result = await wsService.subscribeToContract(contract);
        } else {
          result = await wsService.unsubscribeFromContract(contract);
        }

        results.push({
          contract,
          success: result,
          action
        });
      } catch (error) {
        results.push({
          contract,
          success: false,
          action,
          error: error instanceof Error ? error.message : 'Unknown error'
        });
      }
    }

    const responseData = {
      action: `batch_${action}`,
      results,
      totalContracts: contracts.length,
      successCount: results.filter(r => r.success).length,
      failureCount: results.filter(r => !r.success).length,
      connectionStatus: wsService.getConnectionStatus(),
      responseTime: Date.now() - startTime
    };
      
    const response = createApiResponse(responseData);
    const processingTime = Date.now() - startTime;
      
    return NextResponse.json(response, {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'X-Processing-Time': `${processingTime}ms`,
        'X-Action': `batch_${action}`,
        'X-Data-Source': 'polygon-websocket'
      }
    });
    
  } catch (error) {
    console.error('[API] /api/websocket POST error:', error);
    
    const errorResponse = createApiResponse(
      undefined,
      'Internal server error',
      error instanceof Error ? error.message : 'Unknown error',
      'Please try again or contact support if the issue persists'
    );
    
    return NextResponse.json(errorResponse, { status: 500 });
  }
}

/**
 * OPTIONS /api/websocket - CORS preflight
 */
export async function OPTIONS() {
  return new NextResponse(null, {
    status: 200,
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    },
  });
}
