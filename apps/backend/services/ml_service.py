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
                FROM em_features 
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
        """Generate ML prediction for a symbol."""
        if not self.model:
            logger.warning("No model available for prediction")
            return None
        
        features = self.get_latest_features(symbol)
        if not features:
            logger.warning("No features available for symbol", symbol=symbol)
            return None
        
        try:
            # Prepare feature vector
            feature_names = [
                'iv_t1', 'hv_t1', 'iv_week_ago', 'iv_month_ago', 
                'iv_hv_spread', 'iv_percentile_est', 'avg_iv_t1', 
                'atm_iv_t1', 'iv_skew', 'avg_gamma_t1', 'avg_vega_t1', 
                'call_put_ratio', 'total_contracts'
            ]
            
            # Extract features, handling missing values
            feature_vector = []
            for name in feature_names:
                value = features.get(name)
                if value is None or pd.isna(value):
                    # Use reasonable defaults
                    if name in ['iv_t1', 'avg_iv_t1', 'atm_iv_t1']:
                        value = 0.25  # Default IV
                    elif name in ['hv_t1']:
                        value = 0.20  # Default HV
                    elif name in ['iv_percentile_est']:
                        value = 0.5   # Median percentile
                    elif name in ['call_put_ratio']:
                        value = 1.0   # Neutral ratio
                    elif name in ['total_contracts']:
                        value = 1000  # Default volume
                    else:
                        value = 0.0   # Default to zero
                
                feature_vector.append(float(value))
            
            # Make prediction
            X = np.array(feature_vector).reshape(1, -1)
            
            if hasattr(self.model, 'predict'):
                prediction = self.model.predict(X)[0]
            else:
                # Heuristic model
                iv = feature_vector[0]  # iv_t1
                prediction = 0.36 * iv  # Use learned alpha
            
            # Calculate confidence bands (68% and 95%)
            # For now, use simple scaling factors
            band68_low = prediction * 0.7
            band68_high = prediction * 1.3
            band95_low = prediction * 0.5
            band95_high = prediction * 1.5
            
            return {
                'symbol': symbol,
                'prediction_date': datetime.now(),
                'earnings_date': features.get('earnings_date'),
                'em_baseline': float(prediction),
                'band68_low': float(band68_low),
                'band68_high': float(band68_high),
                'band95_low': float(band95_low),
                'band95_high': float(band95_high),
                'model_type': type(self.model).__name__,
                'features_used': feature_names,
                'confidence': 'medium'  # Could be enhanced with model uncertainty
            }
            
        except Exception as e:
            logger.error("Failed to generate prediction", symbol=symbol, error=str(e))
            return None
    
    def get_predictions_for_symbols(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """Get predictions for multiple symbols."""
        predictions = []
        for symbol in symbols:
            pred = self.predict_expected_move(symbol)
            if pred:
                predictions.append(pred)
        return predictions
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the loaded model."""
        if not self.model:
            return {'status': 'no_model'}
        
        info = {
            'status': 'loaded',
            'model_type': type(self.model).__name__,
            'available_symbols': len(self.get_available_symbols()),
            'loaded_at': datetime.now().isoformat()
        }
        
        if self.model_metadata:
            info.update({
                'training_date': self.model_metadata.get('timestamp'),
                'performance': self.model_metadata.get('performance', {}),
                'features': self.model_metadata.get('features', [])
            })
        
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
