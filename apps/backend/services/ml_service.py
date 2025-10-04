#!/usr/bin/env python3
"""
ML Service for loading and serving expected move predictions.
Integrates ML MVP2 multi-horizon models with bias curve conditioning.
"""

import sys
import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any
import duckdb
import structlog

# Add parent directory to path for ml module imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    from ml.serving_pipeline import MLServingPipeline
    ML_PIPELINE_AVAILABLE = True
except ImportError as e:
    ML_PIPELINE_AVAILABLE = False
    import warnings
    warnings.warn(f"ML Pipeline not available: {e}")

logger = structlog.get_logger()

class MLService:
    """Backend ML service wrapping ML MVP2 serving pipeline."""
    
    def __init__(self, data_dir: Path, duckdb_path: Optional[Path] = None):
        self.data_dir = data_dir
        self.models_dir = data_dir / "models"
        self.duckdb_path = duckdb_path or (data_dir / "quantiv.duckdb")
        
        # ML MVP2 serving pipeline
        self.serving_pipeline = None
        self.conn = None
        
        self._load_ml_pipeline()
        self._connect_duckdb()
    
    def _load_ml_pipeline(self):
        """Load ML MVP2 serving pipeline with multi-horizon models."""
        try:
            if not ML_PIPELINE_AVAILABLE:
                logger.warning("ML Pipeline not available - using fallback mode")
                return
            
            # Initialize serving pipeline
            self.serving_pipeline = MLServingPipeline(
                data_dir=str(self.data_dir),
                models_dir=str(self.models_dir)
            )
            
            logger.info(
                "ML serving pipeline loaded",
                models_loaded=len(self.serving_pipeline.models),
                bias_curves=len(self.serving_pipeline.bias_curves),
                horizons=[1, 2, 3, 7, 14, 21]
            )
                       
        except Exception as e:
            logger.error("Failed to load ML serving pipeline", error=str(e))
            self.serving_pipeline = None
    
    def _connect_duckdb(self):
        """Connect to DuckDB for data access."""
        try:
            if self.duckdb_path.exists():
                self.conn = duckdb.connect(str(self.duckdb_path))
                logger.info("Connected to DuckDB", path=str(self.duckdb_path))
            else:
                logger.warning("DuckDB file not found", path=str(self.duckdb_path))
        except Exception as e:
            logger.error("Failed to connect to DuckDB", error=str(e))
    
    def get_available_symbols(self) -> List[str]:
        """Get symbols with ML predictions available."""
        if not self.conn:
            return []
        
        try:
            result = self.conn.execute("""
                SELECT DISTINCT act_symbol 
                FROM em_forecasts_view 
                ORDER BY act_symbol
            """).fetchall()
            return [row[0] for row in result]
        except Exception as e:
            logger.error("Failed to get available symbols", error=str(e))
            return []
    
    def get_latest_features(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get latest features for a symbol."""
        if not self.conn:
            return None
        
        try:
            result = self.conn.execute("""
                SELECT * FROM em_features 
                WHERE act_symbol = ? 
                ORDER BY earnings_date DESC 
                LIMIT 1
            """, [symbol]).fetchone()
            
            if not result:
                return None
            
            # Convert to dict with column names
            columns = [desc[0] for desc in self.conn.description]
            return dict(zip(columns, result))
            
        except Exception as e:
            logger.error("Failed to get latest features", symbol=symbol, error=str(e))
            return None
    
    def predict_expected_move(self, symbol: str, earnings_date: str, 
                            sector: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Generate ML-enhanced expected move prediction using serving pipeline."""
        
        # Try serving pipeline first (live generation)
        if self.serving_pipeline:
            try:
                forecast = self.serving_pipeline.generate_forecast(
                    symbol=symbol,
                    earnings_date=earnings_date,
                    sector=sector
                )
                
                return {
                    'symbol': symbol,
                    'earnings_date': earnings_date,
                    'prediction_date': datetime.now(),
                    'em_math': forecast.get('em_math', 0.0),
                    'em_ml': forecast.get('em_ml', 0.0),
                    'correction_factor': forecast.get('correction_factor', 1.0),
                    'bias_multiplier': forecast.get('bias_multiplier', 1.0),
                    'p10': forecast.get('p10', 0.0),
                    'p50': forecast.get('p50', 0.0),
                    'p90': forecast.get('p90', 0.0),
                    'combined_confidence': forecast.get('combined_confidence', 0.5),
                    'model_type': 'ml_mvp2',
                    'horizon': forecast.get('horizon', 'unknown'),
                    'method': 'live_generation'
                }
            except Exception as e:
                logger.warning(
                    "Failed to generate live ML forecast, falling back to pre-computed",
                    symbol=symbol,
                    error=str(e)
                )
        
        # Fallback: try to get pre-computed forecast from DuckDB
        if not self.conn:
            logger.warning("No DuckDB connection and no serving pipeline available")
            return None
        
        try:
            result = self.conn.execute("""
                SELECT 
                    act_symbol,
                    earnings_date,
                    em_baseline,
                    band68_low,
                    band68_high,
                    band95_low,
                    band95_high,
                    model_type,
                    features_used,
                    confidence,
                    created_at
                FROM em_forecasts_view 
                WHERE act_symbol = ? 
                  AND earnings_date = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, [symbol, earnings_date]).fetchone()
            
            if not result:
                return None
            
            (act_symbol, earnings_date, em_baseline, band68_low, band68_high, 
             band95_low, band95_high, model_type, features_used, confidence, created_at) = result
            
            return {
                'symbol': act_symbol,
                'earnings_date': earnings_date,
                'prediction_date': datetime.now(),
                'em_math': float(em_baseline) if em_baseline else 0.0,
                'em_ml': float(em_baseline) if em_baseline else 0.0,
                'p10': float(band68_low) if band68_low else 0.0,
                'p50': float(em_baseline) if em_baseline else 0.0,
                'p90': float(band68_high) if band68_high else 0.0,
                'combined_confidence': confidence if confidence else 0.5,
                'model_type': model_type or 'pre_computed',
                'features_used': features_used.split(',') if features_used else [],
                'forecast_created_at': created_at,
                'method': 'pre_computed'
            }
            
        except Exception as e:
            logger.error("Failed to get forecast", symbol=symbol, error=str(e))
            return None
    
    def get_predictions_for_symbols(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """Get predictions for multiple symbols."""
        predictions = []
        for symbol in symbols:
            pred = self.predict_expected_move(symbol)
            if pred:
                predictions.append(pred)
        return predictions
    
    def get_all_upcoming_forecasts(self, days_ahead: int = 30) -> List[Dict[str, Any]]:
        """Get all upcoming forecasts within the specified days."""
        if not self.conn:
            return []
        
        try:
            cutoff_date = (datetime.now() + timedelta(days=days_ahead)).date()
            
            result = self.conn.execute("""
                SELECT 
                    act_symbol,
                    earnings_date,
                    em_baseline,
                    band68_low,
                    band68_high,
                    band95_low,
                    band95_high,
                    model_type,
                    confidence,
                    created_at
                FROM em_forecasts_view 
                WHERE earnings_date >= CURRENT_DATE 
                  AND earnings_date <= ?
                ORDER BY earnings_date ASC, act_symbol ASC
            """, [cutoff_date]).fetchall()
            
            forecasts = []
            for row in result:
                (act_symbol, earnings_date, em_baseline, band68_low, band68_high,
                 band95_low, band95_high, model_type, confidence, created_at) = row
                
                forecasts.append({
                    'symbol': act_symbol,
                    'earnings_date': earnings_date,
                    'em_baseline': float(em_baseline),
                    'band68_low': float(band68_low),
                    'band68_high': float(band68_high),
                    'band95_low': float(band95_low),
                    'band95_high': float(band95_high),
                    'model_type': model_type,
                    'confidence': confidence,
                    'forecast_created_at': created_at
                })
            
            return forecasts
            
        except Exception as e:
            logger.error("Failed to get upcoming forecasts", error=str(e))
            return []
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the ML MVP2 pipeline and models."""
        if not self.serving_pipeline:
            return {
                'status': 'unavailable',
                'pipeline_available': ML_PIPELINE_AVAILABLE,
                'error': 'ML serving pipeline not loaded'
            }
        
        info = {
            'status': 'operational',
            'pipeline_version': 'mvp2',
            'models_loaded': len(self.serving_pipeline.models),
            'horizons_available': sorted(self.serving_pipeline.models.keys()),
            'bias_curves': list(self.serving_pipeline.bias_curves.keys()),
            'forecast_mode': 'live_generation',
            'loaded_at': datetime.now().isoformat()
        }
        
        # Add model metadata from disk
        try:
            metadata_files = list(self.models_dir.glob("metadata_T*.json"))
            if metadata_files:
                latest_metadata = sorted(metadata_files)[-1]
                with open(latest_metadata, 'r') as f:
                    metadata = json.load(f)
                    info['latest_model'] = {
                        'horizon': latest_metadata.stem.split('_')[-1],
                        'metrics': metadata.get('metrics', {}),
                        'trained_at': metadata.get('trained_at')
                    }
        except:
            pass
        
        # Add forecast statistics from DuckDB
        try:
            if self.conn:
                forecast_count = self.conn.execute("""
                    SELECT COUNT(*) FROM em_forecasts_view 
                    WHERE earnings_date >= CURRENT_DATE
                """).fetchone()[0]
                info['upcoming_forecasts'] = forecast_count
        except:
            pass
        
        return info
    
    def refresh_model(self) -> bool:
        """Reload the ML serving pipeline from disk."""
        try:
            self._load_ml_pipeline()
            return self.serving_pipeline is not None
        except Exception as e:
            logger.error("Failed to refresh ML pipeline", error=str(e))
            return False
    
    def close(self):
        """Close connections."""
        if self.conn:
            self.conn.close()
            self.conn = None
