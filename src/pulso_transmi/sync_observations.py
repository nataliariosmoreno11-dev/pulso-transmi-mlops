"""Collector incremental ejecutado por GitHub Actions."""
import os

import psycopg
from pulso_transmi.submit_current_cycle import db_url, load_env, sync_stream

def main():
    load_env()
    with psycopg.connect(db_url()) as connection:
        total=sync_stream(connection)
    github_output=os.getenv("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as output:
            output.write(f"new_rows={total}\n")
    print(f"Collector finalizado: {total} filas recorridas; upsert sin duplicados.")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
