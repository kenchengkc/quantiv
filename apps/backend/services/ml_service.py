#!/usr/bin/env python3
"""
ML Service for loading and serving expected move predictions.
Integrates trained models with the backend API.
"""

import pickle
import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any
import duckdb
import structlog

logger = structlog.get_logger()

class MLService:
    def __init__(self, data_dir: Path, duckdb_path: Optional[Path] = None):
        self.data_dir = data_dir
        self.models_dir = data_dir / "models"
        self.duckdb_path = duckdb_path or (data_dir / "quantiv.duckdb")
        
        self.model = None
        self.model_metadata = None
        self.conn = None
        
        self._load_model()
        self._connect_duckdb()
    
    def _load_model(self):
        """Load the latest trained model."""
        try:
            latest_model_path = self.models_dir / "em_model_latest.pkl"
            
            if not latest_model_path.exists():
                logger.warning("No trained model found", path=str(latest_model_path))
                return
            
            with open(latest_model_path, 'rb') as f:
                self.model = pickle.load(f)
            
            # Load metadata if available
            metadata_files = list(self.models_dir.glob("*_summary.json"))
            if metadata_files:
                latest_metadata = sorted(metadata_files)[-1]
                with open(latest_metadata, 'r') as f:
                    self.model_metadata = json.load(f)
            
            logger.info("ML model loaded successfully", 
                       model_type=type(self.model).__name__,
                       metadata_available=self.model_metadata is not None)
                       
        except Exception as e:
            logger.error("Failed to load ML model", error=str(e))
    
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
    
    def predict_expected_move(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get pre-computed ML prediction for a symbol from forecasts."""
        if not self.conn:
            logger.warning("No DuckDB connection available")
            return None
        
        try:
            # Get the latest forecast for this symbol
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
                ORDER BY earnings_date DESC, created_at DESC
                LIMIT 1
            """, [symbol]).fetchone()
            
            if not result:
                logger.warning("No pre-computed forecast available for symbol", symbol=symbol)
                return None
            
            # Convert result to dict
            (act_symbol, earnings_date, em_baseline, band68_low, band68_high, 
             band95_low, band95_high, model_type, features_used, confidence, created_at) = result
            
            return {
                'symbol': act_symbol,
                'prediction_date': datetime.now(),
                'earnings_date': earnings_date,
                'em_baseline': float(em_baseline),
                'band68_low': float(band68_low),
                'band68_high': float(band68_high),
                'band95_low': float(band95_low),
                'band95_high': float(band95_high),
                'model_type': model_type,
                'features_used': features_used.split(',') if features_used else [],
                'confidence': confidence,
                'forecast_created_at': created_at
            }
            
        except Exception as e:
            logger.error("Failed to get pre-computed forecast", symbol=symbol, error=str(e))
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
        """Get information about the loaded model."""
        if not self.model:
            return {'status': 'no_model'}
        
        info = {
            'status': 'loaded',
            'model_type': type(self.model).__name__,
            'available_symbols': len(self.get_available_symbols()),
            'loaded_at': datetime.now().isoformat(),
            'forecast_mode': 'pre_computed'
        }
        
        if self.model_metadata:
            info.update({
                'training_date': self.model_metadata.get('timestamp'),
                'performance': self.model_metadata.get('performance', {}),
                'features': self.model_metadata.get('features', [])
            })
        
        # Add forecast statistics
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
        """Reload the model from disk."""
        try:
            self._load_model()
            return self.model is not None
        except Exception as e:
            logger.error("Failed to refresh model", error=str(e))
            return False
    
    def close(self):
        """Close connections."""
        if self.conn:
            self.conn.close()
            self.conn = None
