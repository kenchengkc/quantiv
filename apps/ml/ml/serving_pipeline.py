#!/usr/bin/env python3
"""
ML Serving Pipeline - Combines Math Baseline + ML Predictions
Generates live expected move forecasts with confidence bands
"""
from pathlib import Path
from datetime import datetime, date
import pandas as pd
import numpy as np
import duckdb
from typing import Dict, List, Tuple, Optional, Any
import logging
import json

from ml.model_artifact import load_native_model, point_model_name

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
        """Initialize DuckDB connection with views matching actual Parquet schema.

        Parquet columns: date, act_symbol, expiration, strike, call_put,
                         bid, ask, vol, delta, gamma, theta, vega, rho
        """
        try:
            self.conn = duckdb.connect(":memory:")
            self.conn.execute("INSTALL parquet")
            self.conn.execute("LOAD parquet")

            options_path = self.data_dir / "parquet" / "options_chain"
            if options_path.exists():
                self.conn.execute(f"""
                    CREATE VIEW options_raw AS
                    SELECT * FROM read_parquet(
                        '{options_path}/**/*.parquet',
                        hive_partitioning=true
                    )
                """)

                # Paired call/put view used by both math baseline and feature extraction
                self.conn.execute("""
                    CREATE VIEW options_paired AS
                    SELECT
                        c.act_symbol,
                        c.date       AS quote_date,
                        c.expiration,
                        c.strike,
                        (c.bid + c.ask) / 2.0 AS call_mid,
                        (p.bid + p.ask) / 2.0 AS put_mid,
                        c.vol  AS call_iv,
                        p.vol  AS put_iv,
                        c.delta AS call_delta,
                        p.delta AS put_delta,
                        c.gamma AS call_gamma,
                        c.theta AS call_theta,
                        c.vega  AS call_vega,
                        COALESCE(c.vol, 0)   AS call_vol_proxy,
                        COALESCE(p.vol, 0)   AS put_vol_proxy
                    FROM options_raw c
                    JOIN options_raw p
                      ON  c.act_symbol = p.act_symbol
                      AND c.date       = p.date
                      AND c.expiration = p.expiration
                      AND c.strike     = p.strike
                    WHERE c.call_put = 'Call' AND p.call_put = 'Put'
                      AND c.bid > 0 AND c.ask > 0
                      AND p.bid > 0 AND p.ask > 0
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
            model_path = self.models_dir / point_model_name(horizon)
            
            if model_path.exists():
                try:
                    model = load_native_model(model_path)
                    self.models[horizon] = {
                        "model": model,
                        "feature_names": list(model.feature_name()),
                    }
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
        """Calculate math baseline EM with bias correction.

        Uses the paired call/put view.  S_hat is estimated as the strike
        where |call_delta| is closest to 0.5 (standard ATM proxy).
        """

        query = """
        WITH latest AS (
            SELECT MAX(quote_date) AS max_date
            FROM options_paired
            WHERE act_symbol = ?
        ),
        spot AS (
            SELECT arg_min(strike, ABS(ABS(COALESCE(call_delta,0)) - 0.5)) AS s_hat
            FROM options_paired, latest
            WHERE act_symbol = ?
              AND quote_date = latest.max_date
        )
        SELECT
            s.s_hat,
            op.strike,
            op.call_mid,
            op.put_mid,
            ABS(LN(op.strike / NULLIF(s.s_hat, 0))) AS atm_distance
        FROM options_paired op, latest l, spot s
        WHERE op.act_symbol = ?
          AND op.quote_date = l.max_date
          AND s.s_hat > 0
        ORDER BY atm_distance ASC
        LIMIT 10
        """

        try:
            result = self.conn.execute(query, [symbol, symbol, symbol]).fetchall()

            if not result:
                logger.warning(f"No options data found for {symbol}")
                return {'em_math': 0.02, 'confidence': 0.0}

            atm_row = result[0]
            underlying_price = atm_row[0]  # s_hat
            call_mid = atm_row[2]
            put_mid = atm_row[3]
            straddle_price = call_mid + put_mid

            em_math_raw = straddle_price / underlying_price

            bias_multiplier = self._get_bias_multiplier(symbol, sector, lead_time_days)
            em_math_corrected = em_math_raw * bias_multiplier

            return {
                'em_math': em_math_corrected,
                'em_math_raw': em_math_raw,
                'bias_multiplier': bias_multiplier,
                'straddle_price': straddle_price,
                'underlying_price': underlying_price,
                'confidence': 0.7,
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
        """Extract features for ML prediction from the paired options view."""

        query = """
        WITH latest AS (
            SELECT MAX(quote_date) AS max_date
            FROM options_paired WHERE act_symbol = ?
        ),
        spot AS (
            SELECT arg_min(strike, ABS(ABS(COALESCE(call_delta,0)) - 0.5)) AS s_hat
            FROM options_paired, latest
            WHERE act_symbol = ? AND quote_date = latest.max_date
        )
        SELECT
            s.s_hat,
            op.strike,
            op.call_mid,
            op.put_mid,
            op.call_iv,
            op.call_delta,
            op.call_gamma,
            op.call_theta,
            op.call_vega,
            op.expiration,
            op.quote_date
        FROM options_paired op, latest l, spot s
        WHERE op.act_symbol = ?
          AND op.quote_date = l.max_date
          AND s.s_hat > 0
        """

        try:
            result = self.conn.execute(query, [symbol, symbol, symbol]).fetchall()

            if not result:
                return None

            columns = ['s_hat', 'strike', 'call_mid', 'put_mid', 'call_iv',
                       'call_delta', 'call_gamma', 'call_theta', 'call_vega',
                       'expiration', 'quote_date']

            df = pd.DataFrame(result, columns=columns)
            features = self._extract_features_from_chain(df, symbol, lead_time_days)

            return pd.DataFrame([features])

        except Exception as e:
            logger.error(f"Failed to extract ML features for {symbol}: {e}")
            return None

    def _extract_features_from_chain(self, chain_df: pd.DataFrame,
                                   symbol: str, lead_time_days: int) -> Dict[str, float]:
        """Extract features from paired options data.

        Columns expected: s_hat, strike, call_mid, put_mid, call_iv,
                          call_delta, call_gamma, call_theta, call_vega,
                          expiration, quote_date
        """

        s_hat = float(chain_df['s_hat'].iloc[0])

        features: Dict[str, float] = {
            'symbol_encoded': hash(symbol) % 10000,
            'horizon': lead_time_days,
            'earnings_month': datetime.now().month,
            'earnings_weekday': datetime.now().weekday(),
            'underlying_price': s_hat,
            'log_price': np.log(max(s_hat, 1e-6)),
            'log_market_cap': np.log(1e9),
        }

        chain_df = chain_df.copy()
        chain_df['atm_distance'] = np.abs(np.log(chain_df['strike'] / s_hat))
        atm_idx = chain_df['atm_distance'].idxmin()
        atm_row = chain_df.loc[atm_idx]

        call_price = atm_row['call_mid']
        put_price = atm_row['put_mid']
        straddle = call_price + put_price

        features.update({
            'atm_straddle_price': straddle,
            'atm_straddle_pct': straddle / max(s_hat, 1e-6),
            'atm_iv': abs(atm_row['call_delta']) if pd.notna(atm_row['call_iv']) and atm_row['call_iv'] == 0 else (atm_row['call_iv'] or 0.3),
            'atm_delta': abs(atm_row['call_delta'] or 0),
            'atm_gamma': atm_row['call_gamma'] or 0,
            'atm_theta': atm_row['call_theta'] or 0,
            'atm_vega': atm_row['call_vega'] or 0,
        })

        # Skew: 25-delta put premium vs 25-delta call premium
        calls_25d = chain_df[chain_df['call_delta'].between(0.2, 0.3)]
        if not calls_25d.empty:
            put_25d_norm = (calls_25d['put_mid'] / max(s_hat, 1e-6)).mean()
            call_25d_norm = (calls_25d['call_mid'] / max(s_hat, 1e-6)).mean()
            features['skew_25d'] = put_25d_norm - call_25d_norm
        else:
            features['skew_25d'] = 0

        # Volume/OI not available in paired view (our schema has no OI column)
        features.update({
            'total_volume': 0,
            'volume_oi_ratio': 0,
            'pc_volume_ratio': 1.0,
            'iv_term_slope': 0,
            'tte_earnings': lead_time_days / 365.25,
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
