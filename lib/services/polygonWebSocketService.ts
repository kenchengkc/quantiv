/**
 * Polygon.io WebSocket Service for Real-Time Options Data
 * Provides live minute-by-minute OHLC aggregates for options contracts
 */

import WebSocket from 'ws';

export interface PolygonOptionsAggregate {
  ev: 'AM'; // Event type (Aggregates per Minute)
  sym: string; // Option contract ticker (e.g., O:SPY251219C00650000)
  v: number; // Tick volume
  av: number; // Today's accumulated volume
  op: number; // Today's official opening price
  vw: number; // Tick's volume weighted average price
  o: number; // Opening tick price for this aggregate window
  c: number; // Closing tick price for this aggregate window
  h: number; // Highest tick price for this aggregate window
  l: number; // Lowest tick price for this aggregate window
  a: number; // Today's volume weighted average price
  z: number; // Average trade size for this aggregate window
  s: number; // Start timestamp of aggregate window (Unix milliseconds)
  e: number; // End timestamp of aggregate window (Unix milliseconds)
}

export interface ProcessedOptionsAggregate {
  contractTicker: string;
  underlyingSymbol: string;
  timestamp: string;
  timeframe: 'minute';
  ohlc: {
    open: number;
    high: number;
    low: number;
    close: number;
  };
  volume: {
    tick: number;
    accumulated: number;
    averageTradeSize: number;
  };
  pricing: {
    vwap: number;
    dailyVwap: number;
    officialOpen: number;
  };
  window: {
    startTime: string;
    endTime: string;
    durationMs: number;
  };
  dataSource: 'polygon-websocket';
  receivedAt: string;
}

export interface WebSocketSubscription {
  contractTicker: string;
  subscribed: boolean;
  lastUpdate: string;
  messageCount: number;
}

class PolygonWebSocketService {
  private static instance: PolygonWebSocketService;
  private ws: WebSocket | null = null;
  private apiKey: string;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 1000; // Start with 1 second
  private subscriptions = new Map<string, WebSocketSubscription>();
  private messageHandlers = new Set<(data: ProcessedOptionsAggregate) => void>();
  private isConnecting = false;
  private heartbeatInterval: NodeJS.Timeout | null = null;

  // WebSocket URLs
  private readonly wsUrls = {
    delayed: 'wss://delayed.polygon.io/options',
    realtime: 'wss://socket.polygon.io/options' // Requires upgraded plan
  };

  constructor() {
    this.apiKey = process.env.POLYGON_API_KEY || '';
    if (!this.apiKey) {
      console.warn('[Polygon WebSocket] API key not configured');
    }
  }

  static getInstance(): PolygonWebSocketService {
    if (!PolygonWebSocketService.instance) {
      PolygonWebSocketService.instance = new PolygonWebSocketService();
    }
    return PolygonWebSocketService.instance;
  }

  /**
   * Connect to Polygon WebSocket (delayed feed)
   */
  async connect(): Promise<boolean> {
    if (this.isConnecting || (this.ws && this.ws.readyState === WebSocket.OPEN)) {
      return true;
    }

    if (!this.apiKey) {
      console.error('[Polygon WebSocket] Cannot connect: API key not configured');
      return false;
    }

    this.isConnecting = true;

    try {
      console.log('[Polygon WebSocket] Connecting to delayed options feed...');
      
      this.ws = new WebSocket(this.wsUrls.delayed);

      return new Promise((resolve) => {
        if (!this.ws) {
          resolve(false);
          return;
        }

        this.ws.on('open', () => {
          console.log('[Polygon WebSocket] Connected successfully');
          this.isConnecting = false;
          this.reconnectAttempts = 0;
          this.authenticate();
          this.startHeartbeat();
          resolve(true);
        });

        this.ws.on('message', (data) => {
          this.handleMessage(data.toString());
        });

        this.ws.on('close', (code, reason) => {
          console.log(`[Polygon WebSocket] Connection closed: ${code} ${reason.toString()}`);
          this.isConnecting = false;
          this.stopHeartbeat();
          this.handleReconnect();
        });

        this.ws.on('error', (error) => {
          console.error('[Polygon WebSocket] Connection error:', error);
          this.isConnecting = false;
          resolve(false);
        });

        // Timeout after 10 seconds
        setTimeout(() => {
          if (this.isConnecting) {
            console.error('[Polygon WebSocket] Connection timeout');
            this.isConnecting = false;
            resolve(false);
          }
        }, 10000);
      });

    } catch (error) {
      console.error('[Polygon WebSocket] Connection failed:', error);
      this.isConnecting = false;
      return false;
    }
  }

  /**
   * Authenticate with Polygon WebSocket
   */
  private authenticate(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return;
    }

    const authMessage = {
      action: 'auth',
      params: this.apiKey
    };

    console.log('[Polygon WebSocket] Authenticating...');
    this.ws.send(JSON.stringify(authMessage));
  }

  /**
   * Subscribe to minute aggregates for an options contract
   */
  async subscribeToContract(contractTicker: string): Promise<boolean> {
    if (!contractTicker.startsWith('O:')) {
      console.error('[Polygon WebSocket] Invalid contract ticker format:', contractTicker);
      return false;
    }

    // Ensure connection
    const connected = await this.connect();
    if (!connected) {
      return false;
    }

    if (this.subscriptions.has(contractTicker)) {
      console.log(`[Polygon WebSocket] Already subscribed to ${contractTicker}`);
      return true;
    }

    const subscribeMessage = {
      action: 'subscribe',
      params: `AM.${contractTicker}` // AM = Aggregates per Minute
    };

    console.log(`[Polygon WebSocket] Subscribing to ${contractTicker}`);
    this.ws!.send(JSON.stringify(subscribeMessage));

    // Track subscription
    this.subscriptions.set(contractTicker, {
      contractTicker,
      subscribed: true,
      lastUpdate: new Date().toISOString(),
      messageCount: 0
    });

    return true;
  }

  /**
   * Unsubscribe from a contract
   */
  async unsubscribeFromContract(contractTicker: string): Promise<boolean> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return false;
    }

    const unsubscribeMessage = {
      action: 'unsubscribe',
      params: `AM.${contractTicker}`
    };

    console.log(`[Polygon WebSocket] Unsubscribing from ${contractTicker}`);
    this.ws.send(JSON.stringify(unsubscribeMessage));

    this.subscriptions.delete(contractTicker);
    return true;
  }

  /**
   * Add message handler for processed aggregates
   */
  addMessageHandler(handler: (data: ProcessedOptionsAggregate) => void): void {
    this.messageHandlers.add(handler);
  }

  /**
   * Remove message handler
   */
  removeMessageHandler(handler: (data: ProcessedOptionsAggregate) => void): void {
    this.messageHandlers.delete(handler);
  }

  /**
   * Handle incoming WebSocket messages
   */
  private handleMessage(data: string): void {
    try {
      const messages = JSON.parse(data);
      
      // Handle array of messages
      if (Array.isArray(messages)) {
        messages.forEach(msg => this.processMessage(msg));
      } else {
        this.processMessage(messages);
      }
    } catch (error) {
      console.error('[Polygon WebSocket] Message parsing error:', error);
    }
  }

  /**
   * Process individual message
   */
  private processMessage(message: any): void {
    // Handle authentication response
    if (message.ev === 'status') {
      if (message.status === 'auth_success') {
        console.log('[Polygon WebSocket] Authentication successful');
      } else if (message.status === 'auth_failed') {
        console.error('[Polygon WebSocket] Authentication failed');
      }
      return;
    }

    // Handle options aggregates
    if (message.ev === 'AM') {
      const aggregate = message as PolygonOptionsAggregate;
      const processed = this.processAggregate(aggregate);
      
      // Update subscription stats
      const subscription = this.subscriptions.get(aggregate.sym);
      if (subscription) {
        subscription.lastUpdate = new Date().toISOString();
        subscription.messageCount++;
      }

      // Notify handlers
      this.messageHandlers.forEach(handler => {
        try {
          handler(processed);
        } catch (error) {
          console.error('[Polygon WebSocket] Handler error:', error);
        }
      });
    }
  }

  /**
   * Process raw aggregate into structured format
   */
  private processAggregate(aggregate: PolygonOptionsAggregate): ProcessedOptionsAggregate {
    // Extract underlying symbol from contract ticker
    const underlyingMatch = aggregate.sym.match(/O:([A-Z]+)/);
    const underlyingSymbol = underlyingMatch ? underlyingMatch[1] : 'UNKNOWN';

    return {
      contractTicker: aggregate.sym,
      underlyingSymbol,
      timestamp: new Date(aggregate.e).toISOString(), // Use end timestamp
      timeframe: 'minute',
      ohlc: {
        open: aggregate.o,
        high: aggregate.h,
        low: aggregate.l,
        close: aggregate.c
      },
      volume: {
        tick: aggregate.v,
        accumulated: aggregate.av,
        averageTradeSize: aggregate.z
      },
      pricing: {
        vwap: aggregate.vw,
        dailyVwap: aggregate.a,
        officialOpen: aggregate.op
      },
      window: {
        startTime: new Date(aggregate.s).toISOString(),
        endTime: new Date(aggregate.e).toISOString(),
        durationMs: aggregate.e - aggregate.s
      },
      dataSource: 'polygon-websocket',
      receivedAt: new Date().toISOString()
    };
  }

  /**
   * Start heartbeat to keep connection alive
   */
  private startHeartbeat(): void {
    this.heartbeatInterval = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ action: 'ping' }));
      }
    }, 30000); // Ping every 30 seconds
  }

  /**
   * Stop heartbeat
   */
  private stopHeartbeat(): void {
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = null;
    }
  }

  /**
   * Handle reconnection logic
   */
  private handleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('[Polygon WebSocket] Max reconnection attempts reached');
      return;
    }

    this.reconnectAttempts++;
    const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1); // Exponential backoff

    console.log(`[Polygon WebSocket] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);

    setTimeout(async () => {
      const connected = await this.connect();
      if (connected) {
        // Resubscribe to all contracts
        for (const [contractTicker] of this.subscriptions) {
          await this.subscribeToContract(contractTicker);
        }
      }
    }, delay);
  }

  /**
   * Get current subscriptions
   */
  getSubscriptions(): Map<string, WebSocketSubscription> {
    return new Map(this.subscriptions);
  }

  /**
   * Get connection status
   */
  getConnectionStatus(): {
    connected: boolean;
    subscriptions: number;
    reconnectAttempts: number;
  } {
    return {
      connected: this.ws?.readyState === WebSocket.OPEN,
      subscriptions: this.subscriptions.size,
      reconnectAttempts: this.reconnectAttempts
    };
  }

  /**
   * Disconnect and cleanup
   */
  disconnect(): void {
    this.stopHeartbeat();
    
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }

    this.subscriptions.clear();
    this.messageHandlers.clear();
    this.reconnectAttempts = 0;
  }
}

export default PolygonWebSocketService;
