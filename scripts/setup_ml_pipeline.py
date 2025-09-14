#!/usr/bin/env python3
"""
Complete setup script for Quantiv ML pipeline.

Runs the full Phase 0-2 setup:
- Phase 0: Data structure, DuckDB views, validation
- Phase 1: Earnings calendar, labels, features, training view
- Phase 2: Baseline model training

Usage:
  python scripts/setup_ml_pipeline.py [--local] [--skip-models]
"""

import os
import sys
import subprocess
from pathlib import Path
import argparse
from datetime import datetime

def run_script(script_path, args=None, description=""):
    """Run a Python script and handle errors."""
    cmd = [sys.executable, str(script_path)]
    if args:
        cmd.extend(args)
    
    print(f"\n{'='*60}")
    print(f"RUNNING: {description or script_path.name}")
    print(f"Command: {' '.join(cmd)}")
    print(f"{'='*60}")
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=False, text=True)
        print(f"✓ SUCCESS: {description or script_path.name}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ FAILED: {description or script_path.name}")
        print(f"Error code: {e.returncode}")
        return False
    except Exception as e:
        print(f"✗ ERROR: {description or script_path.name} - {e}")
        return False

def check_dependencies():
    """Check if required Python packages are installed."""
    print("[deps] Checking Python dependencies...")
    
    required_packages = [
        'duckdb',
        'pandas', 
        'numpy',
        'sklearn',
        'lightgbm',
        'pyarrow'
    ]
    
    missing = []
    for package in required_packages:
        try:
            __import__(package)
            print(f"  ✓ {package}")
        except ImportError:
            missing.append(package)
            print(f"  ✗ {package} (missing)")
    
    if missing:
        print(f"\n[deps] Missing packages: {', '.join(missing)}")
        print("[deps] Install with: pip install " + " ".join(missing))
        return False
    
    print("[deps] All dependencies satisfied")
    return True

def main():
    parser = argparse.ArgumentParser(description="Setup complete Quantiv ML pipeline")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use local data/ directory instead of /srv/quantiv-data"
    )
    parser.add_argument(
        "--skip-models",
        action="store_true",
        help="Skip model training (Phase 2)"
    )
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="Skip dependency check"
    )
    parser.add_argument(
        "--phase",
        choices=['0', '1', '2', 'all'],
        default='all',
        help="Run specific phase only (default: all)"
    )
    
    args = parser.parse_args()
    
    script_dir = Path(__file__).parent
    
    print("QUANTIV ML PIPELINE SETUP")
    print("=" * 60)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Mode: {'Local' if args.local else 'Production (/srv/quantiv-data)'}")
    print(f"Phase: {args.phase}")
    
    # Check dependencies
    if not args.skip_deps and not check_dependencies():
        print("\n[error] Dependency check failed. Install missing packages first.")
        sys.exit(1)
    
    # Common arguments
    common_args = ['--local'] if args.local else []
    
    success_count = 0
    total_steps = 0
    
    # Phase 0: Data layer hardening
    if args.phase in ['0', 'all']:
        print(f"\n{'='*60}")
        print("PHASE 0: DATA LAYER HARDENING")
        print(f"{'='*60}")
        
        phase0_steps = [
            (script_dir / "setup_data_structure.py", "Setup data folder structure"),
            (script_dir / "setup_duckdb_views.py", "Create DuckDB views"),
            (script_dir / "data_healthcheck.py", "Run data validation")
        ]
        
        for script, desc in phase0_steps:
            total_steps += 1
            if run_script(script, common_args, desc):
                success_count += 1
            else:
                print(f"\n[error] Phase 0 failed at: {desc}")
                if not args.local:  # Continue on local for development
                    sys.exit(1)
    
    # Phase 1: Labels & features
    if args.phase in ['1', 'all']:
        print(f"\n{'='*60}")
        print("PHASE 1: LABELS & FEATURES")
        print(f"{'='*60}")
        
        phase1_steps = [
            (script_dir / "build_em_comprehensive.py", "Build comprehensive labels and features")
        ]
        
        for script, desc in phase1_steps:
            total_steps += 1
            if run_script(script, common_args, desc):
                success_count += 1
            else:
                print(f"\n[error] Phase 1 failed at: {desc}")
                if not args.local:
                    sys.exit(1)
    
    # Phase 2: Baseline models
    if args.phase in ['2', 'all'] and not args.skip_models:
        print(f"\n{'='*60}")
        print("PHASE 2: BASELINE MODELS")
        print(f"{'='*60}")
        
        phase2_steps = [
            (script_dir / "train_baseline_models.py", "Train baseline models")
        ]
        
        for script, desc in phase2_steps:
            total_steps += 1
            if run_script(script, common_args, desc):
                success_count += 1
            else:
                print(f"\n[error] Phase 2 failed at: {desc}")
                if not args.local:
                    sys.exit(1)
    
    # Final summary
    print(f"\n{'='*60}")
    print("SETUP COMPLETE")
    print(f"{'='*60}")
    print(f"Success rate: {success_count}/{total_steps} steps completed")
    
    if success_count == total_steps:
        print("✓ All steps completed successfully!")
        print("\nNext steps:")
        print("1. Review data healthcheck output for any issues")
        print("2. Check model performance in the training logs")
        print("3. Set up daily scoring pipeline (Phase 3)")
        print("4. Build web API and UI (Phase 4)")
    else:
        print(f"⚠ {total_steps - success_count} steps failed")
        print("Review the error messages above and fix issues before proceeding.")
    
    if args.local:
        data_dir = script_dir.parent / "data"
        print(f"\nLocal data directory: {data_dir}")
        if (data_dir / "quantiv.duckdb").exists():
            print("✓ DuckDB database created")
        if (data_dir / "models").exists():
            models = list((data_dir / "models").glob("*.pkl"))
            print(f"✓ {len(models)} model files created")

if __name__ == "__main__":
    main()
