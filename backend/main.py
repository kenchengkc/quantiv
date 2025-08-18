#!/usr/bin/env python3
"""
Quantiv FastAPI Backend - Expected Move Forecasting API
Serves ML-generated expected moves with live market data integration
"""

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import asyncpg
import aioredis
import httpx
import structlog
from datetime import datetime, date, timedelta
import os
from contextlib import asynccontextmanager
import json

# Configure structured logging
logger = structlog.get_logger()

# Pydantic models
class ExpectedMoveRequest(BaseModel):
    symbol: str = Field(..., description="Stock symbol (e.g., AAPL)")
    horizons: List[str] = Field(default=["to_exp", "1d", "5d"], description="Forecast horizons")
    include_live: bool = Field(default=True, description="Include live market data")

class ExpectedMoveResponse(BaseModel):
    symbol: str
    timestamp: datetime
    forecasts: List[Dict[str, Any]]
    live_data: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any]

class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    services: Dict[str, str]

# Global connections
db_pool: asyncpg.Pool = None
redis_client: aioredis.Redis = None
http_client: httpx.AsyncClient = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle"""
    global db_pool, redis_client, http_client
    
    logger.info("🚀 Starting Quantiv API...")
    
    # Initialize database pool
    db_pool = await asyncpg.create_pool(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "quantiv_user"),
        password=os.getenv("POSTGRES_PASSWORD", "quantiv_secure_2024"),
        database=os.getenv("POSTGRES_DB", "quantiv_options"),
        min_size=5,
        max_size=20
    )
    
    # Initialize Redis
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_client = aioredis.from_url(redis_url, decode_responses=True)
    
    # Initialize HTTP client for Polygon
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        headers={"Authorization": f"Bearer {os.getenv('POLYGON_API_KEY', '')}"}
    )
    
    logger.info("✅ Services initialized")
    
    yield
    
    # Cleanup
    logger.info("🔄 Shutting down services...")
    await db_pool.close()
    await redis_client.close()
    await http_client.aclose()

# Create FastAPI app
app = FastAPI(
    title="Quantiv Expected Move API",
    description="ML-powered options expected move forecasting",
    version="2.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://quantiv.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ExpectedMoveService:
    """Service for expected move calculations and caching"""
    
    @staticmethod
    async def get_cached_forecast(symbol: str, horizons: List[str]) -> Optional[Dict]:
        """Get cached forecast from Redis"""
        cache_key = f"em_forecast:{symbol}:{':'.join(sorted(horizons))}"
        
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                data = json.loads(cached)
                # Check if cache is still fresh (< 5 minutes)
                cached_time = datetime.fromisoformat(data['timestamp'])
                if datetime.now() - cached_time < timedelta(minutes=5):
                    return data
        except Exception as e:
            logger.warning("Cache read failed", error=str(e))
        
        return None
    
    @staticmethod
    async def cache_forecast(symbol: str, horizons: List[str], data: Dict):
        """Cache forecast in Redis"""
        cache_key = f"em_forecast:{symbol}:{':'.join(sorted(horizons))}"
        
        try:
            await redis_client.setex(
                cache_key, 
                300,  # 5 minutes TTL
                json.dumps(data, default=str)
            )
        except Exception as e:
            logger.warning("Cache write failed", error=str(e))
    
    @staticmethod
    async def get_latest_forecasts(symbol: str, horizons: List[str]) -> List[Dict]:
        """Get latest ML forecasts from PostgreSQL"""
        query = """
        SELECT 
            underlying,
            quote_ts,
            exp_date,
            horizon,
            em_baseline,
            em_calibrated,
            em_quantile,
            band68_low,
            band68_high,
            band95_low,
            band95_high,
            model_version
        FROM em_forecasts
        WHERE underlying = $1 
          AND horizon = ANY($2)
          AND quote_ts >= NOW() - INTERVAL '1 day'
        ORDER BY quote_ts DESC, exp_date ASC
        LIMIT 50
        """
        
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(query, symbol, horizons)
            
        return [dict(row) for row in rows]
    
    @staticmethod
    async def get_live_market_data(symbol: str) -> Optional[Dict]:
        """Fetch live market data from Polygon"""
        if not os.getenv('POLYGON_API_KEY'):
            return None
        
        try:
            # Get current stock price
            url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/prev"
            response = await http_client.get(url)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('results'):
                    result = data['results'][0]
                    return {
                        'symbol': symbol,
                        'price': result.get('c'),  # close price
                        'change': result.get('c', 0) - result.get('o', 0),
                        'change_percent': ((result.get('c', 0) - result.get('o', 0)) / result.get('o', 1)) * 100,
                        'volume': result.get('v'),
                        'timestamp': datetime.now()
                    }
        except Exception as e:
            logger.warning("Live data fetch failed", symbol=symbol, error=str(e))
        
        return None

# Dependency injection
async def get_em_service() -> ExpectedMoveService:
    return ExpectedMoveService()

# API Endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    services = {}
    
    # Check database
    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        services["database"] = "healthy"
    except Exception:
        services["database"] = "unhealthy"
    
    # Check Redis
    try:
        await redis_client.ping()
        services["redis"] = "healthy"
    except Exception:
        services["redis"] = "unhealthy"
    
    # Check Polygon API
    services["polygon"] = "configured" if os.getenv('POLYGON_API_KEY') else "not_configured"
    
    status = "healthy" if all(s in ["healthy", "configured"] for s in services.values()) else "degraded"
    
    return HealthResponse(
        status=status,
        timestamp=datetime.now(),
        services=services
    )

@app.post("/api/expected-move", response_model=ExpectedMoveResponse)
async def get_expected_move(
    request: ExpectedMoveRequest,
    em_service: ExpectedMoveService = Depends(get_em_service)
):
    """Get expected move forecasts for a symbol"""
    symbol = request.symbol.upper()
    
    logger.info("Expected move request", symbol=symbol, horizons=request.horizons)
    
    # Check cache first
    cached = await em_service.get_cached_forecast(symbol, request.horizons)
    if cached:
        logger.info("Returning cached forecast", symbol=symbol)
        return ExpectedMoveResponse(**cached)
    
    # Get ML forecasts from database
    forecasts = await em_service.get_latest_forecasts(symbol, request.horizons)
    
    if not forecasts:
        raise HTTPException(
            status_code=404, 
            detail=f"No forecasts found for {symbol}"
        )
    
    # Get live market data if requested
    live_data = None
    if request.include_live:
        live_data = await em_service.get_live_market_data(symbol)
    
    # Prepare response
    response_data = {
        "symbol": symbol,
        "timestamp": datetime.now(),
        "forecasts": forecasts,
        "live_data": live_data,
        "metadata": {
            "forecast_count": len(forecasts),
            "horizons_requested": request.horizons,
            "has_live_data": live_data is not None
        }
    }
    
    # Cache the response
    await em_service.cache_forecast(symbol, request.horizons, response_data)
    
    return ExpectedMoveResponse(**response_data)

@app.get("/api/symbols")
async def get_available_symbols():
    """Get list of symbols with available forecasts"""
    query = """
    SELECT DISTINCT underlying as symbol, COUNT(*) as forecast_count
    FROM em_forecasts
    WHERE quote_ts >= NOW() - INTERVAL '7 days'
    GROUP BY underlying
    ORDER BY forecast_count DESC, underlying
    LIMIT 100
    """
    
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query)
    
    return [{"symbol": row["symbol"], "forecast_count": row["forecast_count"]} for row in rows]

@app.get("/api/symbols/{symbol}/history")
async def get_symbol_history(symbol: str, days: int = 30):
    """Get historical forecasts for a symbol"""
    symbol = symbol.upper()
    
    query = """
    SELECT 
        quote_ts,
        horizon,
        em_calibrated,
        band68_low,
        band68_high
    FROM em_forecasts
    WHERE underlying = $1 
      AND quote_ts >= NOW() - INTERVAL '%s days'
    ORDER BY quote_ts DESC, horizon
    LIMIT 1000
    """ % days
    
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, symbol)
    
    return [dict(row) for row in rows]

# Background task for model updates
@app.post("/api/admin/refresh-forecasts")
async def refresh_forecasts(background_tasks: BackgroundTasks):
    """Trigger forecast refresh (admin endpoint)"""
    
    async def refresh_task():
        logger.info("Starting forecast refresh...")
        # This would trigger the batch ML pipeline
        # For now, just clear relevant caches
        try:
            pattern = "em_forecast:*"
            keys = await redis_client.keys(pattern)
            if keys:
                await redis_client.delete(*keys)
            logger.info("Forecast cache cleared", keys_cleared=len(keys))
        except Exception as e:
            logger.error("Cache clear failed", error=str(e))
    
    background_tasks.add_task(refresh_task)
    return {"message": "Forecast refresh initiated"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_config={
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                },
            },
            "handlers": {
                "default": {
                    "formatter": "default",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {
                "level": "INFO",
                "handlers": ["default"],
            },
        }
    )
