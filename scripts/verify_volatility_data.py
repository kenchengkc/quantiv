import os
import pandas as pd
import psycopg2
from pathlib import Path
from dotenv import load_dotenv

# Configuration
CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "volatility_history.csv"
BATCH_SIZE = 10000

def get_conn():
    load_dotenv()
    return psycopg2.connect(
        dbname=os.getenv("PG_DB", "quantiv_options"),
        user=os.getenv("PG_USER", "quantiv_user"),
        password=os.getenv("PG_PASSWORD", "quantiv_secure_2024"),
        host=os.getenv("PG_HOST", "localhost")
    )

def check_volatility_data():
    """Check if volatility data exists in PostgreSQL"""
    conn = get_conn()
    
    # Check if volatility table exists
    with conn.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'volatility_history'
            )
        """)
        table_exists = cur.fetchone()[0]
        
        if not table_exists:
            print("volatility_history table not found in PostgreSQL")
            return
            
        # Compare row counts
        csv_count = len(pd.read_csv(CSV_PATH))
        cur.execute("SELECT COUNT(*) FROM volatility_history")
        pg_count = cur.fetchone()[0]
        
        print(f"CSV rows: {csv_count:,} | PostgreSQL rows: {pg_count:,}")
        
        # Sample check for first date
        first_csv_date = pd.read_csv(CSV_PATH, nrows=1)['date'].iloc[0]
        cur.execute("""
            SELECT 1 FROM volatility_history 
            WHERE date = %s 
            LIMIT 1
        """, (first_csv_date,))
        
        if not cur.fetchone():
            print(f"Data mismatch: {first_csv_date} not found in PostgreSQL")
        else:
            print("First date matches")
    
    conn.close()

if __name__ == "__main__":
    check_volatility_data()
