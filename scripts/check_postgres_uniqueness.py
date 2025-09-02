import os
import psycopg2
from dotenv import load_dotenv

def get_conn():
    load_dotenv()
    return psycopg2.connect(
        dbname=os.getenv("PG_DB", "quantiv_options"),
        user=os.getenv("PG_USER", "quantiv_user"),
        password=os.getenv("PG_PASSWORD", "quantiv_secure_2024"),
        host=os.getenv("PG_HOST", "localhost")
    )

def check_uniqueness():
    conn = get_conn()
    cur = conn.cursor()
    
    # Check for duplicates on natural key
    cur.execute("""
        SELECT date, act_symbol, expiration, strike, call_put, COUNT(*) as cnt
        FROM options_chain
        GROUP BY date, act_symbol, expiration, strike, call_put
        HAVING COUNT(*) > 1
        LIMIT 10
    """)
    
    duplicates = cur.fetchall()
    
    if duplicates:
        print(f"Found {len(duplicates)} duplicate groups (showing first 10):")
        for dup in duplicates:
            print(f"Duplicate key: {dup[:-1]} (count={dup[-1]})")
    else:
        print("No duplicate rows found")
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    check_uniqueness()
