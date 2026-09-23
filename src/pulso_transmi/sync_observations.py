"""Collector incremental ejecutado por GitHub Actions."""
import psycopg
from pulso_transmi.submit_current_cycle import db_url, load_env, sync_stream

def main():
    load_env()
    with psycopg.connect(db_url()) as connection:
        total=sync_stream(connection)
    print(f"Collector finalizado: {total} filas recorridas; upsert sin duplicados.")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
