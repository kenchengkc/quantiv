#!/usr/bin/env python3
"""
Setup canonical data folder structure for Quantiv ML pipeline.

Creates the standard layout:
/srv/quantiv-data/
  parquet/
    options_chain/YYYY/MM/*.parquet
    volatility_history/YYYY/MM/*.parquet
  duckdb-cache/          # temp cache
  quantiv.duckdb         # DuckDB DB file
  ref/                   # reference data (earnings calendar, etc.)
  models/                # trained ML models
  outputs/               # daily scoring outputs

Usage:
  python scripts/setup_data_structure.py [--data-root /srv/quantiv-data]
"""

import os
import sys
from pathlib import Path
import argparse

def setup_data_structure(data_root: Path):
    """Create the canonical data folder structure."""
    print(f"[setup] Creating data structure at: {data_root}")
    
    # Main directories
    directories = [
        "parquet/options_chain",
        "parquet/volatility_history", 
        "duckdb-cache",
        "ref",
        "models",
        "outputs"
    ]
    
    for dir_path in directories:
        full_path = data_root / dir_path
        full_path.mkdir(parents=True, exist_ok=True)
        print(f"[setup] Created: {full_path}")
    
    # Create .gitkeep files for empty directories
    gitkeep_dirs = [
        "duckdb-cache",
        "ref", 
        "models",
        "outputs"
    ]
    
    for dir_name in gitkeep_dirs:
        gitkeep_path = data_root / dir_name / ".gitkeep"
        if not gitkeep_path.exists():
            gitkeep_path.touch()
            print(f"[setup] Created .gitkeep: {gitkeep_path}")
    
    # Create README with structure documentation
    readme_content = """# Quantiv Data Structure

This directory contains the canonical data layout for the Quantiv ML pipeline.

## Directory Structure

```
/srv/quantiv-data/
├── parquet/                    # Parquet data files
│   ├── options_chain/          # Options chain data
│   │   └── YYYY/MM/           # Year/month partitions
│   └── volatility_history/     # Volatility history data  
│       └── YYYY/MM/           # Year/month partitions
├── duckdb-cache/              # DuckDB temporary cache
├── quantiv.duckdb             # Main DuckDB database file
├── ref/                       # Reference data
│   └── earnings_calendar.csv  # Earnings dates
├── models/                    # Trained ML models
│   └── em_model_v1.pkl        # Expected move model
└── outputs/                   # Daily scoring outputs
    └── em_scores_YYYY-MM-DD.parquet
```

## Usage

- Raw data is stored in Parquet format under `parquet/`
- DuckDB views provide normalized access to the data
- Reference data (earnings calendar) goes in `ref/`
- Trained models are saved in `models/`
- Daily scoring outputs go in `outputs/`

## Data Flow

1. CSV → Postgres (via csv_to_postgres.py)
2. Postgres → Parquet (via postgres_to_parquet.py) 
3. DuckDB views → Feature engineering
4. Models → Daily scoring → outputs/
"""
    
    readme_path = data_root / "README.md"
    with open(readme_path, "w") as f:
        f.write(readme_content)
    print(f"[setup] Created README: {readme_path}")
    
    print(f"[setup] Data structure setup complete at: {data_root}")
    return True

def main():
    parser = argparse.ArgumentParser(description="Setup Quantiv data structure")
    parser.add_argument(
        "--data-root", 
        type=Path,
        default=Path("/srv/quantiv-data"),
        help="Root directory for data structure (default: /srv/quantiv-data)"
    )
    parser.add_argument(
        "--local",
        action="store_true", 
        help="Setup in local data/ directory instead of /srv/quantiv-data"
    )
    
    args = parser.parse_args()
    
    if args.local:
        # For local development, use data/ in project root
        script_dir = Path(__file__).parent
        project_root = script_dir.parent
        data_root = project_root / "data"
    else:
        data_root = args.data_root
    
    try:
        setup_data_structure(data_root)
        print("[success] Data structure setup completed successfully")
    except Exception as e:
        print(f"[error] Failed to setup data structure: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
