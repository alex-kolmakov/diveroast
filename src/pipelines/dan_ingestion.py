"""CLI script to run DAN content ingestion.

python -m src.pipelines.dan_ingestion          # incremental merge
python -m src.pipelines.dan_ingestion --full   # full rebuild (resets dlt state)
"""

import argparse

from src.rag.ingestion import run_pipeline

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--full",
        action="store_true",
        help="Drop the table and dlt's incremental state, then refetch everything.",
    )
    args = parser.parse_args()
    table_name = run_pipeline(full_replace=args.full)
    print(f"DAN data ingested. Table: {table_name}")
