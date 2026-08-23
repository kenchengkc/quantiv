#!/usr/bin/env python3
"""Historical multipliers that adjust the straddle expected move by ticker, sector, and days to earnings."""
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, date
import pandas as pd
import numpy as np
import duckdb
from typing import Dict, List, Tuple, Optional
import logging
from dataclasses import dataclass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class BiasPoint:
    """One earnings event: implied move vs what actually happened."""
    symbol: str
    earnings_date: date
    lead_time_days: int
    em_math: float
    realized_move: float
    bias_ratio: float  # realized_move / em_math
    sector: Optional[str] = None
    market_cap: Optional[float] = None
    vix_level: Optional[float] = None

class BiasCurveBuilder:
    """Fit those historical multipliers from past earnings events."""
    
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.conn = None
        self._setup_duckdb()
    
    def _setup_duckdb(self):
        """Open DuckDB and point it at the Parquet files."""
        try:
            self.conn = duckdb.connect(":memory:")
            self.conn.execute("INSTALL parquet")
            self.conn.execute("LOAD parquet")
            
            # Create views for options and earnings data
            options_path = self.data_dir / "parquet" / "options_chain"
            if options_path.exists():
                self.conn.execute(f"""
                    CREATE VIEW options_chain AS 
                    SELECT * FROM read_parquet('{options_path}/**/*.parquet')
                """)
                logger.info(f"Created options_chain view from {options_path}")
            
            # Load earnings calendar
            earnings_path = self.data_dir / "earnings_calendar.csv"
            if earnings_path.exists():
                self.conn.execute(f"""
                    CREATE VIEW earnings_calendar AS 
                    SELECT * FROM read_csv('{earnings_path}')
                """)
                logger.info(f"Loaded earnings calendar from {earnings_path}")
                
        except Exception as e:
            logger.error(f"Failed to setup DuckDB: {e}")
            raise
    
    def extract_historical_bias_points(self, 
                                     start_date: str = "2019-01-01",
                                     end_date: str = "2024-12-31") -> List[BiasPoint]:
        """Extract historical bias points for curve fitting"""
        
        logger.info(f"Extracting bias points from {start_date} to {end_date}")
        
        # Query to get earnings events with options data
        query = """
        WITH earnings_events AS (
            SELECT 
                act_symbol as symbol,
                date::DATE as earnings_date,
                NULL as sector
            FROM earnings_calendar 
            WHERE date BETWEEN ? AND ?
                AND act_symbol IS NOT NULL
        ),
        
        -- Build same-day option pairs and estimate spot via delta≈±0.5 strikes
        paired AS (
            SELECT 
                oc_call.act_symbol as symbol,
                oc_call.date::DATE as quote_date,
                oc_call.expiration as expiration_date,
                oc_call.strike as strike,
                -- mid prices
                (oc_call.bid + oc_call.ask) / 2.0 as call_mid,
                (oc_put.bid + oc_put.ask) / 2.0 as put_mid,
                oc_call.delta as call_delta,
                oc_put.delta as put_delta
            FROM options_chain oc_call
            JOIN options_chain oc_put
              ON oc_call.act_symbol = oc_put.act_symbol
             AND oc_call.date = oc_put.date
             AND oc_call.expiration = oc_put.expiration
             AND oc_call.strike = oc_put.strike
            WHERE oc_call.call_put = 'C' AND oc_put.call_put = 'P'
              AND oc_call.bid > 0 AND oc_call.ask > 0
              AND oc_put.bid > 0 AND oc_put.ask > 0
        ),
        
        -- Estimate spot as the strike where |delta|≈0.5 (avg call/put proxy)
        spot_est AS (
            SELECT 
                symbol,
                quote_date,
                -- choose the strike with minimal distance to 0.5 delta
                arg_min(strike, ABS(ABS(COALESCE(call_delta,0.0)) - 0.5)) AS s_hat
            FROM paired
            GROUP BY symbol, quote_date
        ),
        
        -- Join to nearest earnings and compute lead time
        with_lead AS (
            SELECT 
                e.symbol,
                e.earnings_date,
                e.sector,
                p.quote_date,
                date_diff('day', p.quote_date, e.earnings_date) AS lead_time_days,
                p.expiration_date,
                p.strike,
                p.call_mid,
                p.put_mid,
                s.s_hat,
                ABS(LN(p.strike / NULLIF(s.s_hat,0))) AS atm_distance
            FROM earnings_events e
            JOIN paired p ON p.symbol = e.symbol AND p.quote_date <= e.earnings_date
            JOIN spot_est s ON s.symbol = p.symbol AND s.quote_date = p.quote_date
            WHERE date_diff('day', p.quote_date, e.earnings_date) BETWEEN 1 AND 30
              AND p.expiration_date BETWEEN e.earnings_date AND e.earnings_date + INTERVAL '45 days'
              AND s.s_hat > 0
        ),
        
        -- Pick best ATM per (symbol, earnings_date, lead_time)
        best_atm AS (
            SELECT * FROM (
                SELECT *,
                    ROW_NUMBER() OVER (PARTITION BY symbol, earnings_date, lead_time_days ORDER BY atm_distance ASC) rn
                FROM with_lead
            ) WHERE rn = 1
        ),
        
        -- Compute EM math using S_hat proxy
        em_baseline AS (
            SELECT 
                symbol,
                earnings_date,
                sector,
                quote_date,
                lead_time_days,
                expiration_date,
                strike,
                (call_mid + put_mid) AS straddle_price,
                (call_mid + put_mid) / NULLIF(s_hat,0) AS em_math_pct,
                s_hat
            FROM best_atm
            WHERE s_hat > 0
        ),
        
        -- Compute realized move using S_hat pre/post earnings without window functions
        pre_dates AS (
            SELECT eb.symbol, eb.earnings_date, MAX(se.quote_date) AS pre_qd
            FROM em_baseline eb
            JOIN spot_est se ON se.symbol = eb.symbol
            WHERE se.quote_date BETWEEN eb.earnings_date - INTERVAL '5 days' AND eb.earnings_date
            GROUP BY eb.symbol, eb.earnings_date
        ),
        post_dates AS (
            SELECT eb.symbol, eb.earnings_date, MIN(se.quote_date) AS post_qd
            FROM em_baseline eb
            JOIN spot_est se ON se.symbol = eb.symbol
            WHERE se.quote_date BETWEEN eb.earnings_date + 1 AND eb.earnings_date + 3
            GROUP BY eb.symbol, eb.earnings_date
        ),
        realized AS (
            SELECT 
                eb.*,
                pre_se.s_hat AS s_pre,
                post_se.s_hat AS s_post,
                CASE WHEN pre_se.s_hat > 0 AND post_se.s_hat > 0
                     THEN ABS(LN(post_se.s_hat / pre_se.s_hat))
                     ELSE NULL END AS realized_move_pct
            FROM em_baseline eb
            LEFT JOIN pre_dates pd ON pd.symbol = eb.symbol AND pd.earnings_date = eb.earnings_date
            LEFT JOIN spot_est pre_se ON pre_se.symbol = eb.symbol AND pre_se.quote_date = pd.pre_qd
            LEFT JOIN post_dates pod ON pod.symbol = eb.symbol AND pod.earnings_date = eb.earnings_date
            LEFT JOIN spot_est post_se ON post_se.symbol = eb.symbol AND post_se.quote_date = pod.post_qd
        )
        
        SELECT 
            symbol,
            earnings_date,
            sector,
            lead_time_days,
            s_hat,
            em_math_pct,
            realized_move_pct,
            realized_move_pct / NULLIF(em_math_pct, 0) AS bias_ratio,
            straddle_price
        FROM realized
        WHERE em_math_pct > 0.001 
          AND realized_move_pct IS NOT NULL AND realized_move_pct > 0
          AND bias_ratio BETWEEN 0.1 AND 10.0
        ORDER BY symbol, earnings_date, lead_time_days
        """
        
        try:
            result = self.conn.execute(query, [start_date, end_date]).fetchall()
            columns = [desc[0] for desc in self.conn.description]
            
            bias_points = []
            for row in result:
                data = dict(zip(columns, row))
                bias_points.append(BiasPoint(
                    symbol=data['symbol'],
                    earnings_date=data['earnings_date'],
                    lead_time_days=data['lead_time_days'],
                    em_math=data['em_math_pct'],
                    realized_move=data['realized_move_pct'],
                    bias_ratio=data['bias_ratio'],
                    sector=data.get('sector'),
                ))
            
            logger.info(f"Extracted {len(bias_points)} bias points")
            return bias_points
            
        except Exception as e:
            logger.error(f"Failed to extract bias points: {e}")
            return []
    
    def build_bias_curves(self, bias_points: List[BiasPoint]) -> Dict[str, Dict[int, float]]:
        """Build bias curves by lead time and sector"""
        
        logger.info("Building bias curves by lead time and sector")
        
        # Convert to DataFrame for easier analysis
        df = pd.DataFrame([
            {
                'symbol': bp.symbol,
                'earnings_date': bp.earnings_date,
                'lead_time_days': bp.lead_time_days,
                'em_math': bp.em_math,
                'realized_move': bp.realized_move,
                'bias_ratio': bp.bias_ratio,
                'sector': bp.sector or 'Unknown'
            }
            for bp in bias_points
        ])
        
        if df.empty:
            logger.warning("No bias points available for curve building")
            return {}
        
        # Build curves by lead time buckets
        lead_time_buckets = [1, 2, 3, 7, 14, 21, 30]
        
        bias_curves = {}
        
        # Overall market bias curve
        market_curve = {}
        for bucket in lead_time_buckets:
            # Get observations within +/- 1 day of bucket
            mask = (df['lead_time_days'] >= bucket - 1) & (df['lead_time_days'] <= bucket + 1)
            bucket_data = df[mask]
            
            if len(bucket_data) >= 10:  # Minimum observations for statistical significance
                # Use median for robustness against outliers
                bias_multiplier = bucket_data['bias_ratio'].median()
                market_curve[bucket] = bias_multiplier
                logger.info(f"Market T-{bucket}: {bias_multiplier:.3f} (n={len(bucket_data)})")
        
        bias_curves['market'] = market_curve
        
        # Sector-specific bias curves
        for sector in df['sector'].unique():
            if sector == 'Unknown':
                continue
                
            sector_df = df[df['sector'] == sector]
            sector_curve = {}
            
            for bucket in lead_time_buckets:
                mask = (sector_df['lead_time_days'] >= bucket - 1) & (sector_df['lead_time_days'] <= bucket + 1)
                bucket_data = sector_df[mask]
                
                if len(bucket_data) >= 5:  # Lower threshold for sectors
                    bias_multiplier = bucket_data['bias_ratio'].median()
                    sector_curve[bucket] = bias_multiplier
            
            if sector_curve:  # Only add if we have data
                bias_curves[sector] = sector_curve
                logger.info(f"Sector {sector}: {len(sector_curve)} lead time points")
        
        return bias_curves
    
    def save_bias_curves(self, bias_curves: Dict[str, Dict[int, float]], 
                        output_path: str = "data/bias_curves.parquet"):
        """Save bias curves to Parquet for serving"""
        
        logger.info(f"Saving bias curves to {output_path}")
        
        # Convert to flat DataFrame
        records = []
        for entity, curve in bias_curves.items():
            for lead_time, multiplier in curve.items():
                records.append({
                    'entity': entity,
                    'entity_type': 'market' if entity == 'market' else 'sector',
                    'lead_time_days': lead_time,
                    'bias_multiplier': multiplier,
                    'created_at': datetime.now()
                })
        
        df = pd.DataFrame(records)
        
        # Ensure output directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Save to Parquet
        df.to_parquet(output_path, index=False)
        logger.info(f"Saved {len(records)} bias curve points")
        
        return output_path
    
    def get_bias_multiplier(self, symbol: str, sector: Optional[str], 
                           lead_time_days: int, bias_curves: Dict[str, Dict[int, float]]) -> float:
        """Get bias multiplier for a specific symbol/sector/lead_time"""
        
        # Find closest lead time bucket
        available_buckets = list(bias_curves.get('market', {}).keys())
        if not available_buckets:
            return 1.0  # Default to no bias adjustment
        
        closest_bucket = min(available_buckets, key=lambda x: abs(x - lead_time_days))
        
        # Try sector-specific first, then fall back to market
        if sector and sector in bias_curves:
            sector_curve = bias_curves[sector]
            if closest_bucket in sector_curve:
                return sector_curve[closest_bucket]
        
        # Fall back to market curve
        market_curve = bias_curves.get('market', {})
        return market_curve.get(closest_bucket, 1.0)

def main():
    """Build and save historical bias curves"""
    
    # Initialize builder
    builder = BiasCurveBuilder()
    
    # Extract historical bias points
    bias_points = builder.extract_historical_bias_points(
        start_date="2019-01-01",
        end_date="2024-12-31"
    )
    
    if not bias_points:
        logger.error("No bias points extracted - check data availability")
        return
    
    # Build bias curves
    bias_curves = builder.build_bias_curves(bias_points)
    
    if not bias_curves:
        logger.error("No bias curves built - insufficient data")
        return
    
    # Save curves
    output_path = builder.save_bias_curves(bias_curves)
    logger.info(f"Bias curves saved to {output_path}")
    
    # Print summary
    print("\n=== Bias Curve Summary ===")
    for entity, curve in bias_curves.items():
        print(f"{entity}: {len(curve)} lead time points")
        for lead_time, multiplier in sorted(curve.items()):
            print(f"  T-{lead_time}: {multiplier:.3f}")

if __name__ == "__main__":
    main()
