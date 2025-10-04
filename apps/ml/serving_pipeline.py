#!/usr/bin/env python3
"""
ML Serving Pipeline - Combines Math Baseline + ML Predictions
Generates live expected move forecasts with confidence bands
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, date
import pandas as pd
import numpy as np
import duckdb
import joblib
from typing import Dict, List, Tuple, Optional, Any
import logging
import json

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MLServingPipeline:
    """Serving pipeline for ML-enhanced expected move predictions"""
    
    def __init__(self, data_dir: str = "data", models_dir: str = "models"):
        self.data_dir = Path(data_dir)
        self.models_dir = Path(models_dir)
        self.conn = None
        self.models = {}
        self.bias_curves = {}
        
        self._setup_duckdb()
        self._load_models()
        self._load_bias_curves()
    
    def _setup_duckdb(self):
        """Initialize DuckDB connection"""
        try:
            self.conn = duckdb.connect(":memory:")
            self.conn.execute("INSTALL parquet")
            self.conn.execute("LOAD parquet")
            
            # Load data views
            options_path = self.data_dir / "parquet" / "options_chain"
            if options_path.exists():
                self.conn.execute(f"""
                    CREATE VIEW options_chain AS 
                    SELECT * FROM read_parquet('{options_path}/**/*.parquet')
                """)
            
            earnings_path = self.data_dir / "earnings_calendar.csv"
            if earnings_path.exists():
                self.conn.execute(f"""
                    CREATE VIEW earnings_calendar AS 
                    SELECT * FROM read_csv('{earnings_path}')
                """)
            
            logger.info("DuckDB serving views created")
            
        except Exception as e:
            logger.error(f"Failed to setup DuckDB: {e}")
            raise
    
    def _load_models(self):
        """Load trained ML models"""
        horizons = [1, 2, 3, 7, 14, 21]
        
        for horizon in horizons:
            model_path = self.models_dir / f"lgbm_T{horizon}.joblib"
            
            if model_path.exists():
                try:
                    model_data = joblib.load(model_path)
                    self.models[horizon] = model_data
                    logger.info(f"Loaded T-{horizon} model")
                except Exception as e:
                    logger.warning(f"Failed to load T-{horizon} model: {e}")
        
        logger.info(f"Loaded {len(self.models)} ML models")
    
    def _load_bias_curves(self):
        """Load historical bias curves"""
        bias_path = self.data_dir / "bias_curves.parquet"
        
        if bias_path.exists():
            try:
                df = pd.read_parquet(bias_path)
                
                # Convert to nested dict structure
                for _, row in df.iterrows():
                    entity = row['entity']
                    lead_time = row['lead_time_days']
                    multiplier = row.get('median_bias', row.get('bias_multiplier', 1.0))
                    
                    if entity not in self.bias_curves:
                        self.bias_curves[entity] = {}
                    
                    self.bias_curves[entity][lead_time] = multiplier
                
                logger.info(f"Loaded bias curves for {len(self.bias_curves)} entities")
                
            except Exception as e:
                logger.warning(f"Failed to load bias curves: {e}")
    
    def calculate_math_baseline(self, symbol: str, lead_time_days: int, 
                              sector: Optional[str] = None) -> Dict[str, float]:
        """Calculate math baseline EM with bias correction"""
        
        # Get current options data
        query = """
        SELECT 
            underlying_price,
            strike,
            call_bid, call_ask, put_bid, put_ask,
            implied_volatility as iv,
            ABS(LN(strike / underlying_price)) as atm_distance
        FROM options_chain 
        WHERE underlying_ticker = ?
            AND quote_date = (
                SELECT MAX(quote_date) 
                FROM options_chain 
                WHERE underlying_ticker = ?
            )
            AND call_bid > 0 AND put_bid > 0
        ORDER BY atm_distance ASC
        LIMIT 10
        """
        
        try:
            result = self.conn.execute(query, [symbol, symbol]).fetchall()
            
            if not result:
                logger.warning(f"No options data found for {symbol}")
                return {'em_math': 0.02, 'confidence': 0.0}  # Default 2%
            
            # Find ATM strike
            atm_row = result[0]  # Closest to ATM
            underlying_price = atm_row[0]
            call_mid = (atm_row[2] + atm_row[3]) / 2
            put_mid = (atm_row[4] + atm_row[5]) / 2
            straddle_price = call_mid + put_mid
            
            # Raw EM math
            em_math_raw = straddle_price / underlying_price
            
            # Apply bias correction
            bias_multiplier = self._get_bias_multiplier(symbol, sector, lead_time_days)
            em_math_corrected = em_math_raw * bias_multiplier
            
            return {
                'em_math': em_math_corrected,
                'em_math_raw': em_math_raw,
                'bias_multiplier': bias_multiplier,
                'straddle_price': straddle_price,
                'underlying_price': underlying_price,
                'confidence': 0.7  # Math baseline confidence
            }
            
        except Exception as e:
            logger.error(f"Failed to calculate math baseline for {symbol}: {e}")
            return {'em_math': 0.02, 'confidence': 0.0}
    
    def _get_bias_multiplier(self, symbol: str, sector: Optional[str], 
                           lead_time_days: int) -> float:
        """Get bias multiplier from historical curves"""
        
        # Find closest lead time bucket
        available_buckets = list(self.bias_curves.get('market', {}).keys())
        if not available_buckets:
            return 1.0
        
        closest_bucket = min(available_buckets, key=lambda x: abs(x - lead_time_days))
        
        # Try sector-specific first
        if sector and sector in self.bias_curves:
            sector_curve = self.bias_curves[sector]
            if closest_bucket in sector_curve:
                return sector_curve[closest_bucket]
        
        # Fall back to market
        market_curve = self.bias_curves.get('market', {})
        return market_curve.get(closest_bucket, 1.0)
    
    def extract_ml_features(self, symbol: str, lead_time_days: int) -> Optional[pd.DataFrame]:
        """Extract features for ML prediction"""
        
        # Get current options chain
        query = """
        SELECT 
            underlying_price,
            strike,
            option_type,
            call_bid, call_ask, put_bid, put_ask,
            implied_volatility as iv,
            delta, gamma, theta, vega,
            volume, open_interest,
            expiration_date,
            quote_date
        FROM options_chain 
        WHERE underlying_ticker = ?
            AND quote_date = (
                SELECT MAX(quote_date) 
                FROM options_chain 
                WHERE underlying_ticker = ?
            )
            AND call_bid > 0 AND put_bid > 0
        """
        
        try:
            result = self.conn.execute(query, [symbol, symbol]).fetchall()
            
            if not result:
                return None
            
            columns = ['underlying_price', 'strike', 'option_type', 'call_bid', 'call_ask', 
                      'put_bid', 'put_ask', 'iv', 'delta', 'gamma', 'theta', 'vega',
                      'volume', 'open_interest', 'expiration_date', 'quote_date']
            
            df = pd.DataFrame(result, columns=columns)
            
            # Extract features (similar to feature_engineering.py)
            features = self._extract_features_from_chain(df, symbol, lead_time_days)
            
            return pd.DataFrame([features])
            
        except Exception as e:
            logger.error(f"Failed to extract ML features for {symbol}: {e}")
            return None
    
    def _extract_features_from_chain(self, chain_df: pd.DataFrame, 
                                   symbol: str, lead_time_days: int) -> Dict[str, float]:
        """Extract features from options chain (simplified version)"""
        
        features = {
            'symbol_encoded': hash(symbol) % 10000,
            'horizon': lead_time_days,
            'earnings_month': datetime.now().month,
            'earnings_weekday': datetime.now().weekday(),
        }
        
        underlying_price = chain_df['underlying_price'].iloc[0]
        features['underlying_price'] = underlying_price
        features['log_price'] = np.log(underlying_price)
        features['log_market_cap'] = np.log(1e9)  # Default
        
        # ATM features
        chain_df = chain_df.copy()
        chain_df['atm_distance'] = np.abs(np.log(chain_df['strike'] / underlying_price))
        atm_strike = chain_df.loc[chain_df['atm_distance'].idxmin(), 'strike']
        
        atm_calls = chain_df[(chain_df['strike'] == atm_strike) & (chain_df['option_type'] == 'call')]
        atm_puts = chain_df[(chain_df['strike'] == atm_strike) & (chain_df['option_type'] == 'put')]
        
        if not atm_calls.empty and not atm_puts.empty:
            call_price = (atm_calls['call_bid'].iloc[0] + atm_calls['call_ask'].iloc[0]) / 2
            put_price = (atm_puts['put_bid'].iloc[0] + atm_puts['put_ask'].iloc[0]) / 2
            
            features.update({
                'atm_straddle_price': call_price + put_price,
                'atm_straddle_pct': (call_price + put_price) / underlying_price,
                'atm_iv': atm_calls['iv'].iloc[0],
                'atm_delta': abs(atm_calls['delta'].iloc[0] or 0),
                'atm_gamma': atm_calls['gamma'].iloc[0] or 0,
                'atm_theta': atm_calls['theta'].iloc[0] or 0,
                'atm_vega': atm_calls['vega'].iloc[0] or 0,
            })
        else:
            features.update({
                'atm_straddle_price': 0,
                'atm_straddle_pct': 0.02,
                'atm_iv': 0.3,
                'atm_delta': 0.5,
                'atm_gamma': 0,
                'atm_theta': 0,
                'atm_vega': 0,
            })
        
        # Additional features (simplified)
        features.update({
            'skew_25d': 0,
            'total_volume': chain_df['volume'].sum(),
            'total_oi': chain_df['open_interest'].sum(),
            'volume_oi_ratio': chain_df['volume'].sum() / max(chain_df['open_interest'].sum(), 1),
            'pc_volume_ratio': 1.0,
            'iv_term_slope': 0,
            'tte_earnings': lead_time_days / 365.25
        })
        
        return features
    
    def predict_ml_correction(self, symbol: str, lead_time_days: int) -> Dict[str, float]:
        """Get ML correction factor for math baseline"""
        
        # Find closest model horizon
        available_horizons = list(self.models.keys())
        if not available_horizons:
            return {'correction_factor': 1.0, 'confidence': 0.0}
        
        closest_horizon = min(available_horizons, key=lambda x: abs(x - lead_time_days))
        model_data = self.models[closest_horizon]
        
        # Extract features
        features_df = self.extract_ml_features(symbol, lead_time_days)
        
        if features_df is None:
            return {'correction_factor': 1.0, 'confidence': 0.0}
        
        try:
            # Ensure all required features are present
            model_features = model_data['feature_names']
            for feature in model_features:
                if feature not in features_df.columns:
                    features_df[feature] = 0  # Default value
            
            # Select and order features
            X = features_df[model_features]
            
            # Predict correction factor
            model = model_data['model']
            correction_factor = model.predict(X)[0]
            
            # Apply calibration if available
            if 'calibrator' in model_data:
                calibrator = model_data['calibrator']
                correction_factor = calibrator.predict([correction_factor])[0]
            
            # Confidence based on model performance
            metadata_path = self.models_dir / f"metadata_T{closest_horizon}.json"
            confidence = 0.5  # Default
            
            if metadata_path.exists():
                with open(metadata_path, 'r') as f:
                    metadata = json.load(f)
                    val_mae = metadata.get('metrics', {}).get('val_mae', 1.0)
                    confidence = max(0.1, min(0.9, 1.0 - val_mae))  # Convert MAE to confidence
            
            return {
                'correction_factor': max(0.1, min(5.0, correction_factor)),  # Reasonable bounds
                'confidence': confidence,
                'model_horizon': closest_horizon
            }
            
        except Exception as e:
            logger.error(f"ML prediction failed for {symbol}: {e}")
            return {'correction_factor': 1.0, 'confidence': 0.0}
    
    def generate_forecast(self, symbol: str, earnings_date: str, 
                         sector: Optional[str] = None) -> Dict[str, Any]:
        """Generate complete forecast with math + ML"""
        
        earnings_dt = datetime.strptime(earnings_date, '%Y-%m-%d').date()
        today = date.today()
        lead_time_days = (earnings_dt - today).days
        
        if lead_time_days < 0:
            return {'error': 'Earnings date is in the past'}
        
        # Math baseline
        math_baseline = self.calculate_math_baseline(symbol, lead_time_days, sector)
        
        # ML correction
        ml_correction = self.predict_ml_correction(symbol, lead_time_days)
        
        # Combined prediction
        em_math = math_baseline['em_math']
        correction_factor = ml_correction['correction_factor']
        em_ml = em_math * correction_factor
        
        # Confidence bands (simplified)
        math_confidence = math_baseline.get('confidence', 0.7)
        ml_confidence = ml_correction.get('confidence', 0.5)
        combined_confidence = (math_confidence + ml_confidence) / 2
        
        # Generate confidence bands
        uncertainty = max(0.1, 1.0 - combined_confidence)
        
        forecast = {
            'symbol': symbol,
            'earnings_date': earnings_date,
            'lead_time_days': lead_time_days,
            'forecast_date': datetime.now().isoformat(),
            
            # Predictions
            'em_math': em_math,
            'em_ml': em_ml,
            'correction_factor': correction_factor,
            
            # Confidence bands (P10, P50, P90)
            'p10': em_ml * (1 - uncertainty),
            'p50': em_ml,
            'p90': em_ml * (1 + uncertainty),
            
            # Metadata
            'math_confidence': math_confidence,
            'ml_confidence': ml_confidence,
            'combined_confidence': combined_confidence,
            'model_horizon': ml_correction.get('model_horizon'),
            'bias_multiplier': math_baseline.get('bias_multiplier', 1.0),
            'underlying_price': math_baseline.get('underlying_price'),
        }
        
        return forecast
    
    def batch_forecast(self, symbols_earnings: List[Tuple[str, str, Optional[str]]]) -> List[Dict[str, Any]]:
        """Generate forecasts for multiple symbols"""
        
        forecasts = []
        
        for symbol, earnings_date, sector in symbols_earnings:
            try:
                forecast = self.generate_forecast(symbol, earnings_date, sector)
                forecasts.append(forecast)
                logger.info(f"Generated forecast for {symbol}: EM_ML={forecast.get('em_ml', 0):.3f}")
            except Exception as e:
                logger.error(f"Failed to generate forecast for {symbol}: {e}")
                forecasts.append({
                    'symbol': symbol,
                    'earnings_date': earnings_date,
                    'error': str(e)
                })
        
        return forecasts

def main():
    """Test serving pipeline"""
    
    pipeline = MLServingPipeline()
    
    # Test forecast
    test_symbols = [
        ('AAPL', '2025-01-30', 'Technology'),
        ('MSFT', '2025-01-29', 'Technology'),
    ]
    
    forecasts = pipeline.batch_forecast(test_symbols)
    
    print("\n=== ML Serving Pipeline Test ===")
    for forecast in forecasts:
        if 'error' not in forecast:
            print(f"{forecast['symbol']}:")
            print(f"  EM Math: {forecast['em_math']:.3f}")
            print(f"  EM ML: {forecast['em_ml']:.3f}")
            print(f"  Correction: {forecast['correction_factor']:.3f}")
            print(f"  Confidence: {forecast['combined_confidence']:.3f}")
            print(f"  Bands: [{forecast['p10']:.3f}, {forecast['p90']:.3f}]")
        else:
            print(f"{forecast['symbol']}: {forecast['error']}")

if __name__ == "__main__":
    main()
