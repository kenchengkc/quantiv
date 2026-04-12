// Type declarations for modules without built-in types
declare module 'yahoo-finance2' {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const yahooFinance: {
    quote(symbol: string): Promise<any>;
    options(symbol: string, opts: Record<string, unknown>): Promise<any>;
  };
  export default yahooFinance;
}

declare module 'ws' {
  import { EventEmitter } from 'events';
  class WebSocket extends EventEmitter {
    constructor(address: string, protocols?: string | string[]);
    send(data: string | Buffer): void;
    close(code?: number, reason?: string): void;
    readonly readyState: number;
    static readonly OPEN: number;
    static readonly CLOSED: number;
    static readonly CONNECTING: number;
  }
  export default WebSocket;
}
