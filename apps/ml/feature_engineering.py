#!/usr/bin/env python3
"""
Feature Engineering Pipeline for Multi-Horizon ML Models
Creates training datasets for LightGBM models per lead time horizon
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, date
import pandas as pd
import numpy as np
import duckdb
from typing import Dict, List, Tuple, Optional, Any
import logging
from dataclasses import dataclass
import warnings
warnings.filterwarnings('ignore')

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class FeatureSet:
    """Feature set for a specific horizon"""
    horizon: int  # T-k days
    features: pd.DataFrame
    target: pd.Series
    metadata: Dict[str, Any]

class FeatureEngineer:
    """Feature engineering for expected move ML models"""
    
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.conn = None
        self.horizons = [1, 2, 3, 7, 14, 21]  # T-k lead times
        self._setup_duckdb()
    
    def _setup_duckdb(self):
        """Initialize DuckDB with views"""
        try:
            self.conn = duckdb.connect(":memory:")
            self.conn.execute("INSTALL parquet")
            self.conn.execute("LOAD parquet")
            
            # Options chain view
            options_path = self.data_dir / "parquet" / "options_chain"
            if options_path.exists():
                self.conn.execute(f"""
                    CREATE VIEW options_chain AS 
                    SELECT * FROM read_parquet('{options_path}/**/*.parquet')
                """)
            
            # Earnings calendar
            earnings_path = self.data_dir / "earnings_calendar.csv"
            if earnings_path.exists():
                self.conn.execute(f"""
                    CREATE VIEW earnings_calendar AS 
                    SELECT * FROM read_csv('{earnings_path}')
                """)
            
            # Volatility history (if available)
            vol_path = self.data_dir / "parquet" / "volatility_history"
            if vol_path.exists():
                self.conn.execute(f"""
                    CREATE VIEW volatility_history AS 
                    SELECT * FROM read_parquet('{vol_path}/**/*.parquet')
                """)
            
            logger.info("DuckDB views created successfully")
            
        except Exception as e:
            logger.error(f"Failed to setup DuckDB: {e}")
            raise
    
    def extract_training_data(self, 
                            start_date: str = "2019-01-01",
                            end_date: str = "2024-06-30") -> Dict[int, FeatureSet]:
        """Extract training data for all horizons"""
        
        logger.info(f"Extracting training data from {start_date} to {end_date}")
        
        # Base query for earnings events with options data using actual schema
        base_query = """
        WITH earnings_events AS (
            SELECT 
                act_symbol as symbol,
                date::DATE as earnings_date,
                NULL as sector,
                NULL as market_cap
            FROM earnings_calendar 
            WHERE date BETWEEN ? AND ?
                AND act_symbol IS NOT NULL
        ),
        
        -- Pair call/put at same strike/date/expiration and compute mids
        paired AS (
            SELECT 
                oc_call.act_symbol as symbol,
                oc_call.date::DATE as quote_date,
                oc_call.expiration as expiration_date,
                oc_call.strike as strike,
                (oc_call.bid + oc_call.ask)/2.0 as call_mid,
                (oc_put.bid + oc_put.ask)/2.0 as put_mid,
                oc_call.delta as call_delta,
                oc_put.delta as put_delta,
                oc_call.gamma as call_gamma,
                oc_call.theta as call_theta,
                oc_call.vega  as call_vega,
                COALESCE(oc_call.vol,0) + COALESCE(oc_put.vol,0) as total_vol
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
        
        -- Estimate spot S_hat per (symbol, quote_date) via |delta|≈0.5
        spot_est AS (
            SELECT 
                symbol,
                quote_date,
                arg_min(strike, ABS(ABS(COALESCE(call_delta,0.0)) - 0.5)) AS s_hat
            FROM paired
            GROUP BY symbol, quote_date
        ),
        
        -- Join to earnings and compute lead time; keep strikes near ATM
        snapshots AS (
            SELECT 
                e.symbol,
                e.earnings_date,
                e.sector,
                e.market_cap,
                p.quote_date,
                (e.earnings_date - p.quote_date) as lead_time_days,
                p.expiration_date,
                p.strike,
                p.call_mid,
                p.put_mid,
                p.call_delta,
                p.put_delta,
                p.call_gamma,
                p.call_theta,
                p.call_vega,
                p.total_vol,
                s.s_hat,
                ABS(LN(p.strike / NULLIF(s.s_hat,0))) as log_moneyness,
                (p.expiration_date - p.quote_date) / 365.25 as tte_years
            FROM earnings_events e
            JOIN paired p ON p.symbol = e.symbol
            JOIN spot_est s ON s.symbol = p.symbol AND s.quote_date = p.quote_date
            WHERE p.quote_date <= e.earnings_date
              AND (e.earnings_date - p.quote_date) BETWEEN 1 AND 30
              AND p.expiration_date BETWEEN e.earnings_date AND e.earnings_date + INTERVAL '60 days'
              AND s.s_hat > 0
        )
        
        SELECT * FROM snapshots
        WHERE lead_time_days IN ({})
        ORDER BY symbol, earnings_date, lead_time_days, log_moneyness
        """.format(','.join(map(str, self.horizons)))
        
        try:
            # Execute query
            result = self.conn.execute(base_query, [start_date, end_date]).fetchall()
            columns = [desc[0] for desc in self.conn.description]
            df = pd.DataFrame(result, columns=columns)
            
            if df.empty:
                logger.warning("No options data found")
                return {}
            
            logger.info(f"Loaded {len(df)} options records")
            logger.info(f"Columns: {df.columns.tolist()}")
            logger.info(f"Sample row: {df.iloc[0].to_dict() if len(df) > 0 else 'N/A'}")
            
            # Get realized moves using S_hat around earnings
            realized_moves = self._get_realized_moves(df, start_date, end_date)
            
            # Build feature sets per horizon
            feature_sets = {}
            for horizon in self.horizons:
                feature_set = self._build_features_for_horizon(df, realized_moves, horizon)
                if feature_set is not None:
                    feature_sets[horizon] = feature_set
                    logger.info(f"T-{horizon}: {len(feature_set.features)} training samples")
            
            return feature_sets
            
        except Exception as e:
            logger.error(f"Failed to extract training data: {e}")
            return {}
    
    def _get_realized_moves(self, options_df: pd.DataFrame, 
                           start_date: str, end_date: str) -> pd.DataFrame:
        """Get realized moves for target variable"""
        
        # Get unique earnings events
        earnings_events = options_df[['symbol', 'earnings_date']].drop_duplicates()
        
        realized_moves = []
        
        for _, event in earnings_events.iterrows():
            symbol = event['symbol']
            earnings_date = event['earnings_date']
            
            # Reuse spot_est logic to compute S_hat pre and post earnings
            pre_query = """
            WITH paired AS (
                SELECT strike, delta, date::DATE as quote_date
                FROM options_chain
                WHERE act_symbol = ? AND date BETWEEN ? - INTERVAL '5 days' AND ?
            )
            SELECT arg_min(strike, ABS(ABS(COALESCE(delta,0.0)) - 0.5))
            FROM paired
            """

            post_query = """
            WITH paired AS (
                SELECT strike, delta, date::DATE as quote_date
                FROM options_chain
                WHERE act_symbol = ? AND date BETWEEN ? + 1 AND ? + 3
            )
            SELECT arg_min(strike, quote_date)
            FROM paired
            """
            
            try:
                pre_result = self.conn.execute(pre_query, [symbol, earnings_date, earnings_date, earnings_date]).fetchone()
                post_result = self.conn.execute(post_query, [symbol, earnings_date, earnings_date]).fetchone()
                
                if pre_result and post_result and pre_result[0] and post_result[0]:
                    pre_price = float(pre_result[0])
                    post_price = float(post_result[0])
                    
                    if pre_price > 0 and post_price > 0:
                        realized_move = abs(np.log(post_price / pre_price))
                        realized_moves.append({
                            'symbol': symbol,
                            'earnings_date': earnings_date,
                            'pre_price': pre_price,
                            'post_price': post_price,
                            'realized_move': realized_move
                        })
            
            except Exception as e:
                logger.debug(f"Could not get realized move for {symbol} {earnings_date}: {e}")
                continue
        
        return pd.DataFrame(realized_moves)
    
    def _build_features_for_horizon(self, options_df: pd.DataFrame, 
                                   realized_moves: pd.DataFrame, 
                                   horizon: int) -> Optional[FeatureSet]:
        """Build features for specific horizon (T-k)"""
        
        # Filter to specific horizon
        horizon_df = options_df[options_df['lead_time_days'] == horizon].copy()
        
        if horizon_df.empty:
            logger.warning(f"No data for T-{horizon}")
            return None
        
        # Group by (symbol, earnings_date) to create features
        features_list = []
        
        for (symbol, earnings_date), group in horizon_df.groupby(['symbol', 'earnings_date']):
            
            # Check if we have realized move
            realized_row = realized_moves[
                (realized_moves['symbol'] == symbol) & 
                (realized_moves['earnings_date'] == earnings_date)
            ]
            
            if realized_row.empty:
                continue
            
            realized_move = realized_row.iloc[0]['realized_move']
            
            try:
                features = self._extract_features_from_chain(group, symbol, earnings_date, horizon)
                features['realized_move'] = realized_move
                features_list.append(features)
                
            except Exception as e:
                logger.debug(f"Failed to extract features for {symbol} {earnings_date}: {e}")
                continue
        
        if not features_list:
            return None
        
        # Convert to DataFrame
        features_df = pd.DataFrame(features_list)
        
        # Separate features and target
        target = features_df['realized_move']
        features = features_df.drop('realized_move', axis=1)
        
        # Calculate EM_math baseline for target transformation
        features['em_math'] = features['atm_straddle_pct']
        
        # Target: correction factor = realized_move / em_math
        target_corrected = target / features['em_math'].clip(lower=0.001)
        
        return FeatureSet(
            horizon=horizon,
            features=features,
            target=target_corrected,
            metadata={
                'n_samples': len(features),
                'target_mean': target_corrected.mean(),
                'target_std': target_corrected.std(),
                'em_math_mean': features['em_math'].mean()
            }
        )
    
    def _extract_features_from_chain(self, chain_group: pd.DataFrame, 
                                   symbol: str, earnings_date: date, 
                                   horizon: int) -> Dict[str, float]:
        """Extract features from options chain snapshot"""
        
        features = {
            'symbol_encoded': hash(symbol) % 10000,  # Simple encoding
            'horizon': horizon,
            'earnings_month': earnings_date.month,
            'earnings_weekday': earnings_date.weekday(),
        }
        
        # Spot estimate S_hat from log_moneyness definition
        s_hat = chain_group['s_hat'].iloc[0]
        features['underlying_price'] = s_hat
        features['log_price'] = np.log(max(s_hat, 1e-6))
        
        # Market cap and sector (if available)
        if 'market_cap' in chain_group.columns:
            market_cap = chain_group['market_cap'].iloc[0]
            features['log_market_cap'] = np.log(max(market_cap or 1e6, 1e6))
        
        # ATM identification and straddle pricing
        chain_group = chain_group.copy()
        chain_group['atm_distance'] = np.abs(np.log(chain_group['strike'] / s_hat))
        atm_strike = chain_group.loc[chain_group['atm_distance'].idxmin(), 'strike']
        
        # ATM options
        atm_calls = chain_group[(chain_group['strike'] == atm_strike) & (chain_group['call_delta'].notna())]
        atm_puts = chain_group[(chain_group['strike'] == atm_strike) & (chain_group['put_delta'].notna())]
        
        if not atm_calls.empty and not atm_puts.empty:
            call_price = atm_calls['call_mid'].iloc[0]
            put_price = atm_puts['put_mid'].iloc[0]
            straddle_price = call_price + put_price
            
            features['atm_straddle_price'] = straddle_price
            features['atm_straddle_pct'] = straddle_price / max(s_hat, 1e-6)
            features['atm_iv'] = abs(atm_calls['call_delta'].iloc[0])  # proxy
            
            # Greeks
            features['atm_delta'] = abs(atm_calls['call_delta'].iloc[0] or 0)
            features['atm_gamma'] = atm_calls['call_gamma'].iloc[0] or 0
            features['atm_theta'] = atm_calls['call_theta'].iloc[0] or 0
            features['atm_vega'] = atm_calls['call_vega'].iloc[0] or 0
        else:
            # Fallback values
            features.update({
                'atm_straddle_price': 0,
                'atm_straddle_pct': 0.02,  # Default 2%
                'atm_iv': 0.3,
                'atm_delta': 0.5,
                'atm_gamma': 0,
                'atm_theta': 0,
                'atm_vega': 0
            })
        
        # Skew features (25-delta proxy using normalized mids)
        try:
            calls_25d = chain_group[(chain_group['call_delta'].between(0.2, 0.3))]
            puts_25d = chain_group[(chain_group['put_delta'].between(-0.3, -0.2))]
            
            if not calls_25d.empty and not puts_25d.empty:
                call_25d_norm = (calls_25d['call_mid'] / max(s_hat,1e-6)).mean()
                put_25d_norm  = (puts_25d['put_mid']  / max(s_hat,1e-6)).mean()
                features['skew_25d'] = put_25d_norm - call_25d_norm
            else:
                features['skew_25d'] = 0
        except:
            features['skew_25d'] = 0
        
        # Volume features
        total_volume = chain_group['total_vol'].sum()
        features['total_volume'] = total_volume
        features['volume_oi_ratio'] = total_volume  # proxy (no OI)
        
        # Put/call volume ratio (proxy from deltas)
        call_volume = chain_group[chain_group['call_delta'].notna()]['total_vol'].sum()
        put_volume = chain_group[chain_group['put_delta'].notna()]['total_vol'].sum()
        features['pc_volume_ratio'] = put_volume / max(call_volume, 1)
        
        # Term structure features
        expirations = chain_group['tte_years'].unique()
        if len(expirations) > 1:
            # IV term structure slope
            exp_iv_pairs = []
            for exp in expirations:
                exp_data = chain_group[chain_group['tte_years'] == exp]
                if not exp_data.empty:
                    exp_iv_pairs.append((exp, (exp_data['call_mid'] + exp_data['put_mid']).mean() / max(s_hat,1e-6)))
            
            if len(exp_iv_pairs) >= 2:
                exp_iv_pairs.sort()
                iv_slope = (exp_iv_pairs[-1][1] - exp_iv_pairs[0][1]) / (exp_iv_pairs[-1][0] - exp_iv_pairs[0][0])
                features['iv_term_slope'] = iv_slope
            else:
                features['iv_term_slope'] = 0
        else:
            features['iv_term_slope'] = 0
        
        # Time to earnings
        features['tte_earnings'] = horizon / 365.25
        
        return features
    
    def save_training_data(self, feature_sets: Dict[int, FeatureSet], 
                          output_dir: str = "data/ml_training") -> Dict[int, str]:
        """Save training data for each horizon"""
        
        output_paths = {}
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        for horizon, feature_set in feature_sets.items():
            # Combine features and target
            training_df = feature_set.features.copy()
            training_df['target'] = feature_set.target
            
            # Save to Parquet
            file_path = output_path / f"training_T{horizon}.parquet"
            training_df.to_parquet(file_path, index=False)
            output_paths[horizon] = str(file_path)
            
            logger.info(f"Saved T-{horizon} training data: {len(training_df)} samples to {file_path}")
            
            # Save metadata
            metadata_path = output_path / f"metadata_T{horizon}.json"
            import json
            with open(metadata_path, 'w') as f:
                json.dump(feature_set.metadata, f, indent=2)
        
        return output_paths

def main():
    """Extract and save training data for all horizons"""
    
    # Initialize feature engineer
    engineer = FeatureEngineer()
    
    # Extract training data
    feature_sets = engineer.extract_training_data(
        start_date="2019-01-01",
        end_date="2024-06-30"  # Leave recent data for validation
    )
    
    if not feature_sets:
        logger.error("No training data extracted")
        return
    
    # Save training data
    output_paths = engineer.save_training_data(feature_sets)
    
    # Print summary
    print("\n=== Training Data Summary ===")
    for horizon, path in output_paths.items():
        metadata = feature_sets[horizon].metadata
        print(f"T-{horizon}: {metadata['n_samples']} samples")
        print(f"  Target mean: {metadata['target_mean']:.3f}")
        print(f"  Target std: {metadata['target_std']:.3f}")
        print(f"  EM math mean: {metadata['em_math_mean']:.3f}")
        print(f"  Saved to: {path}")

if __name__ == "__main__":
    main()
