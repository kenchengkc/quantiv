# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Architecture Overview

Quantiv is a full-stack options trading analytics platform with real-time ML-powered expected move forecasting. The architecture consists of four main components:

### Frontend (Next.js 14 + TypeScript + React)
- **Framework**: Next.js 14 with App Router
- **UI Components**: Custom components in `/components/` using Tailwind CSS and Radix UI
- **State Management**: React Query (`@tanstack/react-query`) for server state, React hooks for client state
- **Key Features**: Symbol search, options chains, expected moves, earnings analysis, watchlist
- **Real-time Data**: WebSocket hooks in `/hooks/useOptionsWebSocket.ts` for live options data
- **Type Safety**: Comprehensive Zod schemas in `/lib/schemas.ts` for API validation

### Backend API (FastAPI + Python)
- **Framework**: FastAPI with asyncio for high-performance async operations
- **Database**: PostgreSQL with partitioned tables for 87M+ options records
- **Caching**: Redis for response caching (5-10 minute TTL)
- **Data Sources**: Polygon.io API for live market data
- **Key Services**: Expected move calculations, options chain analysis, earnings data
- **Background Tasks**: ML pipeline integration for forecast updates

### ML Pipeline (Python)
- **Purpose**: Generate expected move forecasts using historical options data
- **Storage**: Parquet files for data lake, PostgreSQL for serving layer
- **Models**: Placeholder implementation in `/ml/pipeline.py` (ready for production ML models)
- **Batch Processing**: Docker-based pipeline with configurable symbol discovery

### Infrastructure (Docker + PostgreSQL + Redis)
- **Orchestration**: Docker Compose for local development
- **Database**: Partitioned PostgreSQL with optimized indexes for analytics queries
- **Caching**: Redis cluster for API response caching and WebSocket state
- **Scaling**: Ready for production deployment with environment-based configuration

## Common Development Commands

### Frontend Development
```bash
# Install dependencies
npm install

# Start development server
npm run dev

# Build for production
npm run build

# Run production server
npm start

# Type checking
npm run type-check

# Linting
npm run lint

# Run tests
npm run test
# or with UI
npx vitest --ui
```

### Backend Development
```bash
# Install Python dependencies
pip install -r requirements.txt

# Run FastAPI server directly
cd backend && python main.py

# Run with Docker
docker-compose up api

# Run backend tests
cd backend && python -m pytest

# Format Python code
black backend/
ruff check backend/
```

### Full Stack Development
```bash
# Start all services (database, cache, API, frontend)
docker-compose up

# Start only backend services
docker-compose up postgres redis api

# Start with ML pipeline
docker-compose --profile batch up ml-pipeline

# View logs
docker-compose logs -f api
docker-compose logs -f frontend

# Database shell
docker-compose exec postgres psql -U quantiv_user -d quantiv_options

# Redis shell
docker-compose exec redis redis-cli
```

### Database Operations
```bash
# Initialize database schema
docker-compose up postgres
# Schema is automatically created via init scripts

# Connect to database
psql -h localhost -U quantiv_user -d quantiv_options

# Reset database
docker-compose down -v
docker-compose up postgres

# Backup database
docker-compose exec postgres pg_dump -U quantiv_user quantiv_options > backup.sql
```

### ML Pipeline
```bash
# Run ML pipeline once
docker-compose run --rm ml-pipeline

# Check pipeline logs
docker-compose logs ml-pipeline

# Validate environment
cd scripts && python validate_env.py
```

## Project Structure Patterns

### React Components
- Components are organized by feature in `/components/`
- Use TypeScript interfaces for props
- Integrate with React Query for data fetching
- Follow the pattern: `{FeatureName}Panel.tsx` for main components

### API Routes
- Backend routes follow RESTful patterns in `backend/main.py`
- All requests/responses validated with Pydantic models
- Async/await pattern for database operations
- Redis caching implemented at service level

### Data Flow
1. **Frontend** → API request via React Query hook
2. **Backend** → Check Redis cache → Query PostgreSQL → Return data
3. **ML Pipeline** → Process historical data → Update PostgreSQL forecasts
4. **WebSocket** → Real-time options data → Frontend updates

### Database Schema
- `options_chain` table partitioned by date (handles 87M+ records)
- `em_forecasts` table for ML-generated expected moves
- `volatility_history` for IV analytics
- Optimized indexes for symbol+date lookups

### Key Custom Hooks
- `useOptions.ts` - Options chain data fetching
- `useExpectedMove.ts` - Expected move calculations
- `useEarnings.ts` - Earnings data and history
- `useOptionsWebSocket.ts` - Real-time options data
- `useWatchlist.ts` - Persistent watchlist management

## Development Environment Setup

### Required Environment Variables
```bash
# API Keys (required for live data)
POLYGON_API_KEY=your_polygon_api_key

# Database (defaults provided in docker-compose.yml)
POSTGRES_HOST=localhost
POSTGRES_USER=quantiv_user
POSTGRES_PASSWORD=quantiv_secure_2024
POSTGRES_DB=quantiv_options

# Redis
REDIS_URL=redis://localhost:6379

# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### Database Partitioning Strategy
The `options_chain` table is partitioned by date to handle large datasets efficiently:
- Partitions created for years 2019-2025
- Queries should include date filters for optimal performance
- Use `EXPLAIN ANALYZE` to verify partition pruning

### Performance Considerations
- PostgreSQL queries use partitioning and optimized indexes
- Redis caching reduces database load (5-10 minute TTL)
- React Query provides client-side caching and background refetching
- WebSocket connections managed with cleanup and reconnection logic

## Testing

### Frontend Tests (Vitest + React Testing Library)
```bash
# Run all tests
npm run test

# Run tests in watch mode
npm run test -- --watch

# Run with coverage
npm run test -- --coverage

# Run tests with UI
npx vitest --ui
```

### Backend Tests (pytest)
```bash
# Install test dependencies
pip install pytest pytest-asyncio

# Run all backend tests
cd backend && python -m pytest

# Run with coverage
cd backend && python -m pytest --cov=.
```

### API Testing
The FastAPI backend includes automatic OpenAPI documentation:
- Development: http://localhost:8000/docs
- Interactive API testing available via Swagger UI
- Health check endpoint: http://localhost:8000/health

## Deployment Notes

### Production Environment
- Frontend: Deploy Next.js to Vercel or similar platform
- Backend: Deploy FastAPI with uvicorn workers
- Database: PostgreSQL with connection pooling
- Cache: Redis cluster for high availability
- ML Pipeline: Scheduled batch jobs (cron/Kubernetes)

### Environment-Specific Configurations
- Use `.env.production` for production settings
- Database connection pooling configured in `backend/main.py`
- CORS origins configured for production domains
- Rate limiting and authentication ready for implementation
