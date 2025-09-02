import psycopg2
import os
from dotenv import load_dotenv

def get_conn():
    load_dotenv()
    return psycopg2.connect(
        dbname=os.getenv("PG_DB", "quantiv_options"),
        user=os.getenv("PG_USER", "quantiv_user"),
        password=os.getenv("PG_PASSWORD", "quantiv_secure_2024"),
        host=os.getenv("PG_HOST", "localhost")
    )

def remove_duplicates():
    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()
    
    try:
        # Create temp table with distinct rows
        cur.execute("""
            CREATE TEMP TABLE deduplicated AS
            SELECT DISTINCT ON (date, act_symbol, expiration, strike, call_put) *
            FROM options_chain
            ORDER BY date, act_symbol, expiration, strike, call_put, ctid
        """)
        
        # Truncate and repopulate original table
        cur.execute("TRUNCATE options_chain")
        cur.execute("INSERT INTO options_chain SELECT * FROM deduplicated")
        
        conn.commit()
        print(f"Removed duplicates - table now has {cur.rowcount:,} unique rows")
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    remove_duplicates()
